"""拉取同花顺个股人气榜（最新交易日，沪深主板）写入 DuckDB 的 ths_hot 表。

只存最新一日（每日替换）。复用「拷贝库→写临时库→原子替换」模式，避免与 API 只读争锁。
由 deploy/refresh_data.sh 在每日入库后调用。需 TUSHARE_TOKEN / TUSHARE_API_URL。

运行：uv run python scripts/ingest_ths_hot.py
"""
from __future__ import annotations

import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import db  # noqa: E402
from src.data_collection import tushare_client as tsc  # noqa: E402


def _latest_trade_date() -> str | None:
    if not db.database_exists():
        return None
    try:
        with db.connect(read_only=True) as conn:
            row = conn.execute("SELECT MAX(date) FROM kline").fetchone()
        return row[0].strftime("%Y-%m-%d") if row and row[0] else None
    except Exception:
        return None


def main() -> None:
    d = _latest_trade_date()
    if not d:
        print("[ths_hot] 无 kline 数据，无法确定交易日，跳过")
        return
    print(f"[ths_hot] 交易日 {d}，拉取人气榜 ...")
    df = tsc.fetch_ths_hot(d)
    print(f"[ths_hot] 主板个股人气榜 {len(df)} 行")
    if df.empty:
        print("[ths_hot] 空，跳过写入")
        return

    dest = db.DUCKDB_PATH
    tmp = dest + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)

    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)
        conn.execute("DELETE FROM ths_hot")
        n = db.upsert_ths_hot(df, conn)
    db.atomic_swap(tmp, dest)
    print(f"[ths_hot] 已写入 {n} 行 -> {os.path.abspath(dest)}")


if __name__ == "__main__":
    main()
