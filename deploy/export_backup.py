"""服务器侧：把 DuckDB 全部表导出为 zstd parquet 快照（供异地备份上传）。

只读打开生产库（不阻塞写入），逐表 COPY 到输出目录，并写 manifest.json
（导出时间、schema 版本、各表行数）供恢复时校验。

用法：DUCKDB_PATH=... uv run python deploy/export_backup.py [outdir=/tmp/db_export]
"""
from __future__ import annotations

import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import db  # noqa: E402


def main() -> None:
    outdir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/db_export"
    os.makedirs(outdir, exist_ok=True)

    manifest: dict = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": os.path.abspath(db.DUCKDB_PATH),
        "tables": {},
    }
    with db.connect(read_only=True) as conn:
        manifest["schema_version"] = db.get_meta("schema_version", conn)
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        for tbl in tables:
            dest = os.path.join(outdir, f"{tbl}.parquet")
            conn.execute(
                f"COPY (SELECT * FROM \"{tbl}\") TO '{dest}' "
                "(FORMAT PARQUET, COMPRESSION zstd)"
            )
            n = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            manifest["tables"][tbl] = n
            print(f"[export] {tbl}: {n} 行 -> {dest}")

    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    total_mb = sum(
        os.path.getsize(os.path.join(outdir, fn)) for fn in os.listdir(outdir)
    ) / 1e6
    print(f"[export] 完成：{len(manifest['tables'])} 表，共 {total_mb:.1f} MB -> {outdir}")


if __name__ == "__main__":
    main()
