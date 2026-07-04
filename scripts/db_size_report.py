"""DuckDB 体积报告：文件大小 + 各表行数与估算体积（迁移前后对比用）。

用法：DUCKDB_PATH=... uv run python scripts/db_size_report.py
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import db  # noqa: E402


def main() -> None:
    path = db.DUCKDB_PATH
    print(f"[size] {path}: {os.path.getsize(path) / 1e6:.1f} MB")
    with db.connect(read_only=True) as conn:
        version = db.get_meta("schema_version", conn)
        print(f"[size] schema_version: {version or 'v1(无 meta_kv)'}")
        rows = conn.execute(
            "SELECT table_name, estimated_size FROM duckdb_tables() ORDER BY estimated_size DESC"
        ).fetchall()
        for tbl, est in rows:
            n = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            print(f"  {tbl:<20} {n:>10,} 行   估算 {est:>12,} 行块")


if __name__ == "__main__":
    main()
