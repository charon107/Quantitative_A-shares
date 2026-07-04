"""从 parquet 快照重建 DuckDB（容灾恢复，配合 deploy/export_backup.py 的产出）。

新建空库（当前 schema）→ 逐表从 parquet INSERT（列交集）→ 对照 manifest.json 校验
行数 → atomic_swap 替换到 DUCKDB_PATH（旧库若存在自动进 backups/）。

用法：DUCKDB_PATH=... uv run python scripts/restore_from_backup.py <snapshot_dir>
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import db  # noqa: E402


def restore(snapshot_dir: str) -> None:
    manifest_path = os.path.join(snapshot_dir, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

    tmp = db.DUCKDB_PATH + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)

    mismatches: list[str] = []
    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        for tbl in tables:
            src = os.path.join(snapshot_dir, f"{tbl}.parquet")
            if not os.path.exists(src):
                print(f"[restore] 跳过 {tbl}（快照无此表）")
                continue
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{tbl}')").fetchall()]
            src_cols = {
                r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()
            }
            common = [f'"{c}"' for c in cols if c in src_cols]
            col_list = ", ".join(common)
            conn.execute(
                f"INSERT OR REPLACE INTO {tbl} ({col_list}) "
                f"SELECT {col_list} FROM read_parquet('{src}')"
            )
            n = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            expected = manifest.get("tables", {}).get(tbl)
            mark = "" if expected in (None, n) else f"  !! manifest 记录 {expected} 行"
            if mark:
                mismatches.append(tbl)
            print(f"[restore] {tbl}: {n} 行{mark}")
        db.set_meta("schema_version", str(db.SCHEMA_VERSION), conn)
        conn.execute("CHECKPOINT")

    if mismatches:
        os.remove(tmp)
        raise SystemExit(f"[restore] 行数与 manifest 不符，已中止：{mismatches}")
    db.atomic_swap(tmp, db.DUCKDB_PATH)
    print(f"[restore] 完成 -> {os.path.abspath(db.DUCKDB_PATH)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法：python scripts/restore_from_backup.py <snapshot_dir>")
    restore(sys.argv[1])
