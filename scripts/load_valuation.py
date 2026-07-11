"""服务器侧：把 runner 回填的估值日频 parquet 加载进 DuckDB（一次性历史回填用）。

日常增量走 load_all_parquet.py（valuation_recent.parquet）；本脚本只处理
backfill_valuation_parquet.py 的全历史产物。~930 万行与 kline 同量级，
按 pyarrow 批次流式 upsert + 定期 CHECKPOINT，避免 1.6GB 小服务器 OOM
（同 load_all_parquet 的 qfq 重算教训，2026-07-10）。

用法：uv run python scripts/load_valuation.py /tmp/ingest_val
"""
from __future__ import annotations

import os
import shutil
import sys

import pyarrow.parquet as pq

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import cache, db  # noqa: E402

_BATCH_ROWS = 500_000
_CHECKPOINT_EVERY = 4  # 每 4 批（约 200 万行）落盘一次


def main() -> None:
    d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ingest_val"
    path = os.path.join(d, "valuation.parquet")
    if not os.path.exists(path):
        raise SystemExit(f"[load_val] 找不到 {path}")

    dest = db.DUCKDB_PATH
    _lock = db.acquire_ingest_lock()  # noqa: F841 持有到进程退出，防与每日入库并发丢数据
    tmp = dest + ".new"
    for leftover in (tmp, tmp + ".wal", tmp + ".tmp"):
        if os.path.isdir(leftover):
            shutil.rmtree(leftover)
        elif os.path.exists(leftover):
            os.remove(leftover)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)

    total = 0
    pf = pq.ParquetFile(path)
    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)
        for i, batch in enumerate(pf.iter_batches(batch_size=_BATCH_ROWS), 1):
            frame = batch.to_pandas()
            total += db.upsert_valuation_daily(frame, conn)
            if i % _CHECKPOINT_EVERY == 0:
                conn.execute("CHECKPOINT")
                print(f"[load_val] 进度 {total} 行", flush=True)
        conn.execute("CHECKPOINT")

    db.atomic_swap(tmp, dest)
    cache.invalidate_all()
    print(f"[load_val] 完成：valuation {total} 行 -> {os.path.abspath(dest)}")


if __name__ == "__main__":
    main()
