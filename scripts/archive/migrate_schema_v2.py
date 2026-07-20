"""schema v2 一次性迁移：删 kline.adjustflag、比率列 DOUBLE->FLOAT、建 meta_kv，并重建库回收空间。

做法：新建空库（新 schema）→ ATTACH 旧库只读 → 逐表 INSERT SELECT（列交集，类型自动
CAST）→ 写 schema_version → CHECKPOINT → atomic_swap 原子替换（旧库自动进 backups/
留作回滚点）。

用法：DUCKDB_PATH=... uv run python scripts/migrate_schema_v2.py
"""
from __future__ import annotations

import os
import sys

import duckdb

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import db  # noqa: E402


def migrate(src_path: str) -> None:
    if not os.path.exists(src_path):
        raise SystemExit(f"库文件不存在：{src_path}")

    with db.connect(read_only=True, path=src_path) as probe:
        version = db.get_meta("schema_version", probe)
        if version is not None and int(version) >= db.SCHEMA_VERSION:
            print(f"[migrate_v2] 已是 schema v{version}，无需迁移")
            return
        src_tables = {r[0] for r in probe.execute("SHOW TABLES").fetchall()}

    tmp = src_path + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)

    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)
        conn.execute(f"ATTACH '{src_path}' AS _mig_src (READ_ONLY)")
        new_tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        for tbl in new_tables:
            if tbl == "meta_kv" or tbl not in src_tables:
                continue
            cols = [
                r[1] for r in conn.execute(f"PRAGMA table_info('{tbl}')").fetchall()
            ]
            src_cols = {
                r[1] for r in conn.execute(f"PRAGMA table_info('_mig_src.{tbl}')").fetchall()
            }
            common = [f'"{c}"' for c in cols if c in src_cols]
            col_list = ", ".join(common)
            conn.execute(f"INSERT INTO {tbl} ({col_list}) SELECT {col_list} FROM _mig_src.{tbl}")
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            print(f"[migrate_v2] {tbl}: {n} 行")
        conn.execute("DETACH _mig_src")
        db.set_meta("schema_version", str(db.SCHEMA_VERSION), conn)
        conn.execute("CHECKPOINT")

    old_size = os.path.getsize(src_path) / 1e6
    new_size = os.path.getsize(tmp) / 1e6
    db.atomic_swap(tmp, src_path)
    print(f"[migrate_v2] 完成：{old_size:.1f}MB -> {new_size:.1f}MB（旧库已进 backups/）")


if __name__ == "__main__":
    try:
        migrate(db.DUCKDB_PATH)
    except duckdb.Error as e:
        raise SystemExit(f"[migrate_v2] 迁移失败（原库未动）：{e}")
