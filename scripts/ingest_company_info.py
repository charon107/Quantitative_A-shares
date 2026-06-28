"""拉取全市场公司信息（tushare）写入 DuckDB 的 stock_info 表。

可独立运行，也由 deploy/refresh_data.sh 在每日入库后调用。
采用「拷贝现有库 → 写临时库 → 原子替换」，避免与 API 只读连接争锁。

运行：uv run python -m scripts.ingest_company_info  （或 python scripts/ingest_company_info.py）
需要环境变量 TUSHARE_TOKEN（及代理网关 TUSHARE_API_URL）。
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


def main() -> None:
    print("[company] 拉取 stock_basic + stock_company ...")
    df = tsc.fetch_company_info()
    print(f"[company] 取得 {len(df)} 家公司信息")

    dest = db.DUCKDB_PATH
    tmp = dest + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)

    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)
        n = db.upsert_company(df, conn)
    db.atomic_swap(tmp, dest)
    print(f"[company] 已写入 stock_info {n} 条 -> {os.path.abspath(dest)}")


if __name__ == "__main__":
    main()
