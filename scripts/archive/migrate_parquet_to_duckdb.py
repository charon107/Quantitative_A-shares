"""一次性迁移：把现有 parquet 数据导入 DuckDB（market.duckdb）。

读取 ``<base_dir>/kline_fq/*.parquet`` 建 ``kline`` 表，
读取 ``<base_dir>/code_name_map.parquet`` 建 ``stock_meta`` 表，
全程用 DuckDB 原生 ``read_parquet``，先写临时库再原子替换。

用法：
    uv run python scripts/migrate_parquet_to_duckdb.py \
        [--base-dir 股价数据_parquet_fq] [--dest market.duckdb]
"""
from __future__ import annotations

import argparse
import os
import sys

# 允许直接 `python scripts/...` 运行时找到 src 包
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src import db  # noqa: E402


def _build_kline_insert(conn) -> str:
    """按 _src 视图的实际列动态生成 INSERT SELECT：缺失列填 NULL，adjustflag 默认 '2'。"""
    cols = {r[0] for r in conn.execute("DESCRIBE _src").fetchall()}

    def num(name: str) -> str:
        return f"CAST({name} AS DOUBLE)" if name in cols else "NULL"

    adjustflag = "CAST(adjustflag AS VARCHAR)" if "adjustflag" in cols else "'2'"
    return f"""
        INSERT OR REPLACE INTO kline
            (code, date, open, high, low, close, volume, amount, pctChg, turn, adjustflag)
        SELECT
            code,
            CAST(date AS DATE) AS date,
            {num('open')}, {num('high')}, {num('low')}, {num('close')},
            {num('volume')}, {num('amount')}, {num('pctChg')}, {num('turn')},
            {adjustflag}
        FROM _src
        WHERE code IS NOT NULL AND date IS NOT NULL
    """


def migrate(base_dir: str, dest: str) -> None:
    kline_glob = os.path.join(base_dir, "kline_fq", "*.parquet")
    name_map_path = os.path.join(base_dir, "code_name_map.parquet")
    tmp = dest + ".new"

    if os.path.exists(tmp):
        os.remove(tmp)

    print(f"[migrate] 源目录：{os.path.abspath(base_dir)}")
    print(f"[migrate] 临时库：{tmp}")

    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)

        print("[migrate] 导入 kline（read_parquet glob）...")
        safe_glob = kline_glob.replace("'", "''")  # CREATE VIEW 不支持预处理参数，内联路径
        conn.execute(
            f"CREATE TEMP VIEW _src AS SELECT * FROM read_parquet('{safe_glob}', union_by_name=true)"
        )
        conn.execute(_build_kline_insert(conn))
        kline_rows = conn.execute("SELECT COUNT(*) FROM kline").fetchone()[0]
        n_codes = conn.execute("SELECT COUNT(DISTINCT code) FROM kline").fetchone()[0]
        max_date = conn.execute("SELECT MAX(date) FROM kline").fetchone()[0]
        print(f"[migrate] kline: {kline_rows} 行 / {n_codes} 只 / 最新 {max_date}")

        if os.path.exists(name_map_path):
            print("[migrate] 导入 stock_meta ...")
            conn.execute(
                "INSERT OR REPLACE INTO stock_meta (code, code_name) "
                "SELECT code, code_name FROM read_parquet(?)",
                [name_map_path],
            )
            meta_rows = conn.execute("SELECT COUNT(*) FROM stock_meta").fetchone()[0]
            print(f"[migrate] stock_meta: {meta_rows} 条")
        else:
            print(f"[migrate] 警告：未找到 {name_map_path}，跳过 stock_meta")

    db.atomic_swap(tmp, dest)
    size_mb = os.path.getsize(dest) / 1024 / 1024
    print(f"[migrate] 完成：{os.path.abspath(dest)}（{size_mb:.1f} MB）")


def main() -> None:
    parser = argparse.ArgumentParser(description="parquet → DuckDB 一次性迁移")
    parser.add_argument("--base-dir", default="股价数据_parquet_fq", help="parquet 数据根目录")
    parser.add_argument("--dest", default=db.DUCKDB_PATH, help="目标 DuckDB 文件路径")
    args = parser.parse_args()
    migrate(args.base_dir, args.dest)


if __name__ == "__main__":
    main()
