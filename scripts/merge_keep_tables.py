"""部署辅助：把生产库中「新库为空」的表合并进新传上来的全量日线库。

场景：scripts/reingest_all.py 在本地全量重拉生成的库只有日线相关表
（kline/raw_kline/adj_factor/stock_meta），基本面/选股/指数/人气/公司
等表为空。整库替换前，需要先把生产库（DUCKDB_PATH）里这些表的数据
补进新库，否则替换会清空它们。

规则：对新库中行数为 0 的表，从生产库按列交集复制全部行；非空表不动。
生产库以 READ_ONLY ATTACH，不影响正在运行的 api 服务。

用法：DUCKDB_PATH=<生产库> uv run python scripts/merge_keep_tables.py <新库路径>
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import db  # noqa: E402


def merge(new_db_path: str) -> None:
    prod_path = os.path.abspath(db.DUCKDB_PATH)
    if not os.path.exists(prod_path):
        raise SystemExit(f"[merge] 生产库不存在：{prod_path}")
    if os.path.abspath(new_db_path) == prod_path:
        raise SystemExit("[merge] 新库路径不能与生产库相同")

    with db.connect(read_only=False, path=new_db_path) as conn:
        db.init_schema(conn)
        conn.execute(f"ATTACH '{prod_path}' AS prod (READ_ONLY)")
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        prod_tables = {
            r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_catalog='prod'"
            ).fetchall()
        }
        for tbl in tables:
            n_new = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            if n_new > 0:
                print(f"[merge] 保留新库 {tbl}: {n_new} 行")
                continue
            if tbl not in prod_tables:
                print(f"[merge] 跳过 {tbl}（生产库无此表）")
                continue
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{tbl}')").fetchall()]
            prod_cols = {
                r[0] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_catalog='prod' AND table_name=?", [tbl]
                ).fetchall()
            }
            common = ", ".join(f'"{c}"' for c in cols if c in prod_cols)
            conn.execute(f'INSERT INTO "{tbl}" ({common}) SELECT {common} FROM prod."{tbl}"')
            n = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            print(f"[merge] 从生产库补 {tbl}: {n} 行")
        db.set_meta("schema_version", str(db.SCHEMA_VERSION), conn)
        conn.execute("DETACH prod")
        conn.execute("CHECKPOINT")
    print(f"[merge] 完成 -> {os.path.abspath(new_db_path)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法：python scripts/merge_keep_tables.py <新库路径>")
    merge(sys.argv[1])
