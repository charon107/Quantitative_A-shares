"""全量重拉：用 tushare 重新拉取全历史日线，统一单位与前复权基准。

背景：历史数据迁移自 baostock（volume=股、amount=元），而 tushare 每日入库是
volume=手、amount=千元，导致混合单位。本脚本清空 kline/raw_kline/adj_factor，
按交易日从 tushare 流式重拉（内存安全），再逐股重算前复权，保留 stock_info/stock_meta。

内存安全：逐日抓取后立即 upsert 入库（不在内存堆积全历史），最后逐股重算 qfq。
采用「拷贝现有库 -> 写临时库 -> 原子替换」。

运行（服务器，需 TUSHARE_TOKEN/TUSHARE_API_URL）：
    nohup uv run python scripts/reingest_all.py > /tmp/reingest.log 2>&1 &
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import duckdb  # noqa: E402

from src import db  # noqa: E402
from src.data_collection import tushare_client as tsc  # noqa: E402
from src.data_collection.stock_price import (  # noqa: E402
    START_DATE,
    STATE_PATH,
    fetch_market_snapshot,
    get_stock_list,
    name_map_frame,
)


def main() -> None:
    today = datetime.today().strftime("%Y-%m-%d")
    dates = [d.strftime("%Y-%m-%d") for d in tsc.fetch_trade_dates(START_DATE, today)]
    if not dates:
        print("无交易日，退出")
        return
    print(f"[reingest] 重拉 {len(dates)} 个交易日：{dates[0]} ~ {dates[-1]}")

    stock_df = get_stock_list()

    dest = db.DUCKDB_PATH
    tmp = dest + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)  # 保留 stock_info / stock_meta

    actual_last = None
    conn = duckdb.connect(tmp)
    try:
        conn.execute(f"SET memory_limit='{db.MEMORY_LIMIT}'")
        db.init_schema(conn)
        # 清空价量表（保留 stock_info / stock_meta）
        conn.execute("DELETE FROM kline")
        conn.execute("DELETE FROM raw_kline")
        conn.execute("DELETE FROM adj_factor")

        # 逐日流式抓取 -> 直接入库（内存安全）
        for i, d in enumerate(dates, 1):
            try:
                raw, factor = fetch_market_snapshot(d)
            except tsc.TushareFatalError as e:
                print(f"[reingest] 永久性错误，终止：{e}")
                raise
            if not raw.empty:
                db.upsert_raw(raw, conn)
                actual_last = d
            if not factor.empty:
                db.upsert_adj(factor, conn)
            if i % 20 == 0 or i == len(dates):
                print(f"[reingest] 抓取 {i}/{len(dates)} ({d})", flush=True)

        # 逐股重算前复权 -> kline
        codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM raw_kline").fetchall()]
        print(f"[reingest] 重算前复权：{len(codes)} 只", flush=True)
        done = 0
        for j, code in enumerate(codes, 1):
            try:
                raw_full = db.read_raw(code, conn)
                fac = db.read_adj(code, conn)
                if raw_full.empty or fac.empty:
                    continue
                qfq = tsc.compute_qfq(raw_full, fac, code)
                if not qfq.empty:
                    db.upsert_kline(qfq, conn)
                    done += 1
            except Exception as e:
                print(f"[reingest] {code} qfq 失败：{e}")
            if j % 500 == 0:
                print(f"[reingest] qfq {j}/{len(codes)}", flush=True)

        db.upsert_meta(name_map_frame(stock_df), conn)
        kn = conn.execute("SELECT COUNT(*) FROM kline").fetchone()[0]
        print(f"[reingest] kline 行数：{kn}，覆盖 {done} 只")
    finally:
        conn.close()

    db.atomic_swap(tmp, dest)

    # 推进入库进度
    state = {"last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "last_complete_date": actual_last}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"[reingest] 完成 -> {os.path.abspath(dest)}；last_complete_date={actual_last}")


if __name__ == "__main__":
    main()
