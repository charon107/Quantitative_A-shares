"""服务器侧：把 runner 抓好的人气榜 parquet 加载进 DuckDB 的 ths_hot 表（不触网关）。

空 parquet 则跳过（保留上一日数据，不清空）。加载后预热 Redis 的 hot_stocks 键。
采用「拷贝库 -> 写临时库 -> 原子替换」。

用法：uv run python scripts/load_ths_hot_parquet.py /tmp/ths_hot.parquet
"""
from __future__ import annotations

import os
import shutil
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import cache, db, metrics  # noqa: E402


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "ths_hot.parquet"
    if not os.path.exists(path):
        print(f"[load_ths_hot] 文件不存在：{path}，跳过")
        return
    df = pd.read_parquet(path)
    if df.empty:
        print("[load_ths_hot] parquet 为空，跳过（保留旧数据）")
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

    # 预热 Redis（与 services 的缓存键一致）
    try:
        cache.save("load_hot_stocks", metrics.hot_stocks(12), ttl=86400)
    except Exception as e:
        print(f"[load_ths_hot] 预热失败（忽略）：{e}")

    print(f"[load_ths_hot] 已写入 ths_hot {n} 行 -> {os.path.abspath(dest)}")


if __name__ == "__main__":
    main()
