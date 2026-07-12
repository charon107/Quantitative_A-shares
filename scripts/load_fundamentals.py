"""服务器侧：把 runner 抓好的基本面/指数 parquet 加载进 DuckDB，并跑逐年选股写表。

读取 fundamental.parquet / index.parquet，upsert stock_fundamental / index_daily →
upsert 季度基本面/分红/业绩预告/业绩快报（fundamental_quarterly/dividend/forecast/
express.parquet，存在才入，展示用与选股解耦）→
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


# 展示用增量产物：(parquet 文件名, db upsert 函数名)。存在才入库，与选股解耦
_EXTRA_LOADS = (
    ("fundamental_quarterly.parquet", "upsert_fundamental_quarterly"),
    ("dividend.parquet", "upsert_dividend"),
    ("forecast.parquet", "upsert_forecast"),
    ("express.parquet", "upsert_express"),
)


def main() -> None:
    d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ingest_fund"
    fund = _read(d, "fundamental.parquet")
    idx = _read(d, "index.parquet")
    extras = {name: _read(d, name) for name, _fn in _EXTRA_LOADS}
    extra_desc = " ".join(f"{name.split('.')[0]}={len(frame)}" for name, frame in extras.items())
    print(f"[load_fund] 输入：fundamental={len(fund)} index={len(idx)} {extra_desc}")

    dest = db.DUCKDB_PATH
    _lock = db.acquire_ingest_lock()  # noqa: F841 持有到进程退出，防与每日入库并发丢数据
    tmp = dest + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)

    n_fund = n_idx = n_sel = purged = 0
    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)
        if db.ensure_fundamental_schema(conn):
            print("[load_fund] stock_fundamental 旧结构（debt_to_assets），已重建为 debt_ratio 口径")
        if db.ensure_quarterly_schema(conn):
            print("[load_fund] stock_fundamental_quarterly 旧结构（缺 total_debt），已重建")
        if not fund.empty:
            n_fund = db.upsert_fundamental(fund, conn)
        if not idx.empty:
            n_idx = db.upsert_index_daily(idx, conn)
        for name, fn_name in _EXTRA_LOADS:
            frame = extras[name]
            if not frame.empty:
                n = getattr(db, fn_name)(frame, conn)
                print(f"[load_fund] {name.split('.')[0]} {n} 行")

        # 跑选股（用同一写连接读刚 upsert 的 fundamental + 已有 stock_meta，避免重复 open 文件）
        panel = fundamental_screen.panel_from_conn(conn)
        nm_df = conn.execute("SELECT code, code_name FROM stock_meta").df()
        nm = dict(zip(nm_df["code"], nm_df["code_name"])) if not nm_df.empty else {}
        li_df = conn.execute("SELECT code, list_date, industry FROM stock_info").df()
        list_dates = dict(zip(li_df["code"], li_df["list_date"])) if not li_df.empty else {}
        industries = dict(zip(li_df["code"], li_df["industry"])) if not li_df.empty else {}
        selected = fundamental_screen.select_pool(
            panel, nm, list_dates=list_dates, industries=industries
        )
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
