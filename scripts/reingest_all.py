"""全量重拉：用 tushare 重新拉取全历史日线，统一单位与前复权基准。

背景：历史数据迁移自 baostock（volume=股、amount=元），而 tushare 每日入库是
volume=手、amount=千元，导致混合单位。本脚本清空 kline/raw_kline/adj_factor，
按交易日从 tushare 流式重拉（内存安全），再逐股重算前复权，保留 stock_info/stock_meta。

并发策略：ThreadPoolExecutor 多天并行抓取，全局限速器锁保证总速率 ≤90 次/分，
DB 写由锁串行化（DuckDB 单写者）。并发数默认 6，可设 FETCH_WORKERS 覆盖。

内存安全：抓取结果即写 DB 不入大 list；逐股重算 qfq 照旧串行（每股独立）。

运行（本机，需 TUSHARE_TOKEN/TUSHARE_API_URL）：
    uv run python scripts/reingest_all.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import duckdb  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src import db  # noqa: E402
from src.data_collection import tushare_client as tsc  # noqa: E402
from src.data_collection.stock_price import (  # noqa: E402
    START_DATE,
    STATE_PATH,
    fetch_market_snapshot,
    get_stock_list,
    name_map_frame,
)

WORKERS = int(os.environ.get("FETCH_WORKERS", "6"))


class _NextAllowed:
    """threading 版 next_allowed（鸭子类型兼容 multiprocessing.Value 的 .value）。"""

    def __init__(self) -> None:
        self.value = 0.0


def main() -> None:
    today = datetime.today().strftime("%Y-%m-%d")
    dates = [d.strftime("%Y-%m-%d") for d in tsc.fetch_trade_dates(START_DATE, today)]
    if not dates:
        print("无交易日，退出")
        return
    print(f"[reingest] 重拉 {len(dates)} 个交易日：{dates[0]} ~ {dates[-1]}，并发 {WORKERS} 线程")

    # 全局限速器：所有线程共享"下一次允许调用"时间戳，总速率 ≤90 次/分
    tsc.configure_rate_limiter(threading.Lock(), _NextAllowed())

    stock_df = get_stock_list()

    dest = db.DUCKDB_PATH
    tmp = dest + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)  # 保留 stock_info / stock_meta

    actual_last: str | None = None
    db_lock = threading.Lock()
    conn = duckdb.connect(tmp)
    try:
        conn.execute(f"SET memory_limit='{db.MEMORY_LIMIT}'")
        db.init_schema(conn)
        # 清空价量表（保留 stock_info / stock_meta）
        conn.execute("DELETE FROM kline")
        conn.execute("DELETE FROM raw_kline")
        conn.execute("DELETE FROM adj_factor")

        # ── 并发抓取（多天并行，rate limiter 保证总速率 ≤90 次/分）──
        n_ok = 0
        n_skip = 0

        def _fetch_and_store(d: str) -> tuple[str, bool]:
            """抓取单日全市场快照 → 加锁写 DB。返回 (date, has_data)。"""
            raw, factor = fetch_market_snapshot(d)
            has_data = not raw.empty
            with db_lock:
                if not raw.empty:
                    db.upsert_raw(raw, conn)
                if not factor.empty:
                    db.upsert_adj(factor, conn)
            return d, has_data

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(_fetch_and_store, d): d for d in dates}
            for fut in tqdm(as_completed(futures), total=len(dates), desc="抓取日线", unit="天"):
                try:
                    d, has_data = fut.result()
                except tsc.TushareFatalError as e:
                    print(f"\n[reingest] 永久性错误，终止：{e}")
                    raise
                except Exception as e:
                    tqdm.write(f"[reingest] {futures[fut]} 失败：{e}")
                    n_skip += 1
                    continue
                if has_data:
                    n_ok += 1
                    if actual_last is None or d > actual_last:
                        actual_last = d
                else:
                    n_skip += 1

        print(f"[reingest] 抓取完成：{n_ok} 天有数据，{n_skip} 天无数据/失败")

        # ── 逐股重算前复权 -> kline ──
        codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM raw_kline").fetchall()]
        print(f"[reingest] 重算前复权：{len(codes)} 只", flush=True)
        done = 0
        for code in tqdm(codes, desc="重算前复权", unit="只"):
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
                tqdm.write(f"[reingest] {code} qfq 失败：{e}")

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
