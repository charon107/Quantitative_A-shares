"""服务器侧：把 runner 抓好的 parquet 加载进 DuckDB（不触网关）。

读取 raw_recent / adj_recent / meta / company / ths_hot，
upsert raw_kline/adj_factor → 对「有新交易日」的 code 重算前复权写 kline →
upsert stock_meta / stock_info，全量替换 ths_hot；最后原子替换 + 清 Redis 缓存。

用法：uv run python scripts/load_all_parquet.py /tmp/ingest
随后由 workflow 调 deploy/warmup_redis.py 预热。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import cache, db  # noqa: E402
from src.data_collection import tushare_client as tsc  # noqa: E402
from src.data_collection.stock_price import STATE_PATH  # noqa: E402


def _read(d: str, name: str) -> pd.DataFrame:
    p = os.path.join(d, name)
    return pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()


def main() -> None:
    d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ingest"
    raw = _read(d, "raw_recent.parquet")
    adj = _read(d, "adj_recent.parquet")
    meta = _read(d, "meta.parquet")
    comp = _read(d, "company.parquet")
    hot = _read(d, "ths_hot.parquet")
    print(f"[load_all] 输入：raw={len(raw)} adj={len(adj)} meta={len(meta)} company={len(comp)} hot={len(hot)}")

    dest = db.DUCKDB_PATH
    tmp = dest + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)

    last_date = None
    n_qfq = 0
    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)
        kmax_row = conn.execute("SELECT MAX(date) FROM kline").fetchone()
        kmax = kmax_row[0] if kmax_row else None

        if not raw.empty:
            db.upsert_raw(raw, conn)
        if not adj.empty:
            db.upsert_adj(adj, conn)

        # 只对「出现新交易日」的 code 重算前复权（高效；无新日则不重算）
        if kmax is not None:
            touched = [r[0] for r in conn.execute(
                "SELECT DISTINCT code FROM raw_kline WHERE date > ?", [kmax]
            ).fetchall()]
        else:
            touched = [r[0] for r in conn.execute("SELECT DISTINCT code FROM raw_kline").fetchall()]
        for code in touched:
            try:
                rf = db.read_raw(code, conn)
                af = db.read_adj(code, conn)
                if rf.empty or af.empty:
                    continue
                q = tsc.compute_qfq(rf, af, code)
                if not q.empty:
                    db.upsert_kline(q, conn)
                    n_qfq += 1
            except Exception as e:
                print(f"[load_all] {code} qfq 失败：{e}")

        if not meta.empty:
            db.upsert_meta(meta, conn)
        if not comp.empty:
            db.upsert_company(comp, conn)
        if not hot.empty:
            conn.execute("DELETE FROM ths_hot")
            db.upsert_ths_hot(hot, conn)

        nm = conn.execute("SELECT MAX(date) FROM kline").fetchone()[0]
        last_date = nm.strftime("%Y-%m-%d") if nm else None

    db.atomic_swap(tmp, dest)

    # 推进入库进度
    try:
        parent = os.path.dirname(os.path.abspath(STATE_PATH))
        os.makedirs(parent, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "last_complete_date": last_date},
                f, ensure_ascii=False, indent=2,
            )
    except Exception as e:
        print(f"[load_all] 写 state 失败（忽略）：{e}")

    # 清 Redis（预热交给 warmup_redis.py）
    cache.invalidate_all()
    print(f"[load_all] 完成：重算 qfq {n_qfq} 只，最新交易日 {last_date} -> {os.path.abspath(dest)}")


if __name__ == "__main__":
    main()
