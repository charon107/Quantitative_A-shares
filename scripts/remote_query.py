"""本地 Python 远程查询服务器 DuckDB 的客户端（对接只读 SQL 网关 /api/sql）。

配置（环境变量或仓库根 .env）：
  WECHATNUM_API_URL  服务地址，默认 http://47.109.138.67:8501
  SQL_API_TOKEN      与服务器一致的 Bearer token（必填）

用法：
    from scripts.remote_query import query_df

    df = query_df("SELECT * FROM stock_fundamental_quarterly WHERE code='sh.600660'")
    top = query_df("SELECT code, roe FROM stock_fundamental WHERE year=2025 ORDER BY roe DESC LIMIT 20")

    # 也可以把结果直接喂给本地 duckdb 继续加工：
    import duckdb
    con = duckdb.connect()
    con.register("q", query_df("SELECT * FROM stock_valuation_daily WHERE code='sh.600519'"))
    print(con.sql("SELECT date, pe_ttm FROM q ORDER BY date DESC LIMIT 5"))

命令行：uv run python scripts/remote_query.py "SELECT COUNT(*) AS n FROM kline"
服务器返回超过 SQL_MAX_ROWS 会截断（本函数打印警告），请在 SQL 里自带过滤/LIMIT。
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pyarrow as pa
import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import config  # noqa: E402  导入即加载仓库根 .env

DEFAULT_URL = os.environ.get("WECHATNUM_API_URL", "http://47.109.138.67:8501")
_TIMEOUT_SECONDS = 120


def query_df(sql: str, url: str | None = None, token: str | None = None) -> pd.DataFrame:
    """在服务器 DuckDB 上执行只读 SQL，返回 DataFrame。SQL 报错时抛 RuntimeError（含服务端信息）。"""
    token = token or config.SQL_API_TOKEN
    if not token:
        raise RuntimeError("未配置 SQL_API_TOKEN（环境变量或仓库根 .env）")
    resp = requests.post(
        f"{(url or DEFAULT_URL).rstrip('/')}/api/sql",
        json={"sql": sql},
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    if resp.headers.get("X-Truncated") == "true":
        print(f"[remote_query] 警告：结果超过服务器行数上限，已截断为 {resp.headers.get('X-Rows')} 行", file=sys.stderr)
    with pa.ipc.open_stream(resp.content) as reader:
        return reader.read_all().to_pandas()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('用法: uv run python scripts/remote_query.py "SELECT ..."')
    frame = query_df(sys.argv[1])
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(frame)
    print(f"[{len(frame)} 行]", file=sys.stderr)
