"""服务器侧：把 runner 抓好的基本面/指数 parquet 加载进 DuckDB，并跑逐年选股写表。

读取 fundamental.parquet / index.parquet，upsert stock_fundamental / index_daily →
跑 fundamental_screen.run_selection 全量替换 selected_stocks → 清理退市股 →
原子替换 + 清 Redis 缓存。

用法：uv run python scripts/load_fundamentals.py /tmp/ingest_fund
"""
from __future__ import annotations

import os
import shutil
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import cache, db  # noqa: E402
from src.analysis import fundamental_screen  # noqa: E402


def _read(d: str, name: str) -> pd.DataFrame:
    p = os.path.join(d, name)
    return pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()


def main() -> None:
    d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ingest_fund"
    fund = _read(d, "fundamental.parquet")
    idx = _read(d, "index.parquet")
    print(f"[load_fund] 输入：fundamental={len(fund)} index={len(idx)}")

    dest = db.DUCKDB_PATH
    tmp = dest + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)

    n_fund = n_idx = n_sel = purged = 0
    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)
        if not fund.empty:
            n_fund = db.upsert_fundamental(fund, conn)
        if not idx.empty:
            n_idx = db.upsert_index_daily(idx, conn)

        # 跑选股（用同一写连接读刚 upsert 的 fundamental + 已有 stock_meta，避免重复 open 文件）
        panel = fundamental_screen.panel_from_conn(conn)
        nm_df = conn.execute("SELECT code, code_name FROM stock_meta").df()
        nm = dict(zip(nm_df["code"], nm_df["code_name"])) if not nm_df.empty else {}
        selected = fundamental_screen.select_pool(panel, nm)
        n_sel = db.replace_selected_stocks(selected, conn)

        # 清理退市股（选股池/财务表也一并清）
        purged = db.purge_delisted(conn)

    db.atomic_swap(tmp, dest)
    cache.invalidate_all()
    print(
        f"[load_fund] 完成：fundamental {n_fund} 行，index {n_idx} 行，"
        f"选股池 {n_sel} 行，清理退市股 {purged} 只 -> {os.path.abspath(dest)}"
    )


if __name__ == "__main__":
    main()
