"""A股看板 — 统计计算层（DuckDB 版）。

纯计算：输入来自 DuckDB（`kline` / `stock_meta` 表），输出统计指标。
无 UI、无 HTTP，便于单测与缓存。被 FastAPI 路由复用。

所有读取走 `src.db.query_df`（只读短连接）。`path=None` 时用 `DUCKDB_PATH`。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import db
from src.analysis.ma5_above_ma10_duration import (
    add_ma,
    extract_samples,
    apply_strict_ongoing,
)

MA_WINDOWS = (5, 10, 20, 60)


# ========== 全市场最新一日 ==========
def load_all_latest_day(path: str | None = None) -> pd.DataFrame:
    """每只股票最新一日的 OHLCV（全市场快照）。无数据返回空 DataFrame。"""
    return db.query_df(
        """
        SELECT * FROM kline
        QUALIFY row_number() OVER (PARTITION BY code ORDER BY date DESC) = 1
        """,
        path=path,
    )


def market_breadth(df: pd.DataFrame) -> dict:
    """市场宽度：涨/跌/平家数与涨跌比。纯 pandas，作用于已加载 df。"""
    if df.empty or "pctChg" not in df.columns:
        return {"up": 0, "down": 0, "flat": 0, "ratio": np.nan}
    up = int((df["pctChg"] > 0).sum())
    down = int((df["pctChg"] < 0).sum())
    flat = int((df["pctChg"] == 0).sum())
    if down == 0:
        ratio = np.inf if up > 0 else 0.0
    else:
        ratio = up / down
    return {"up": up, "down": down, "flat": flat, "ratio": ratio}


# ========== 等权指数 ==========
def equal_weighted_index(start_date: str = "2025-01-01", path: str | None = None) -> pd.Series:
    """等权组合累计收益（几何累乘）。index=日期，value=累计收益率（0.05=+5%）。"""
    daily = db.query_df(
        """
        WITH r AS (
            SELECT date,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM kline
        )
        SELECT date, AVG(ret) AS daily_return
        FROM r
        WHERE ret IS NOT NULL AND date >= ?
        GROUP BY date
        ORDER BY date
        """,
        [start_date],
        path=path,
    )
    if daily.empty:
        return pd.Series(dtype=float)
    s = daily.set_index("date")["daily_return"].astype(float)
    return (1 + s).cumprod() - 1


# ========== 涨停/跌停家数 ==========
def limit_up_down_series(
    up_threshold: float = 9.9,
    down_threshold: float = -9.9,
    path: str | None = None,
) -> pd.DataFrame:
    """每日涨停/跌停家数走势。列：date / limit_up / limit_down。"""
    df = db.query_df(
        """
        SELECT date,
               SUM(CASE WHEN pctChg >= ? THEN 1 ELSE 0 END) AS limit_up,
               SUM(CASE WHEN pctChg <= ? THEN 1 ELSE 0 END) AS limit_down
        FROM kline
        GROUP BY date
        ORDER BY date
        """,
        [up_threshold, down_threshold],
        path=path,
    )
    if df.empty:
        return df
    df["limit_up"] = df["limit_up"].astype(int)
    df["limit_down"] = df["limit_down"].astype(int)
    return df


# ========== 个股 K线 ==========
def breadth_series(
    up_threshold: float = 9.9,
    down_threshold: float = -9.9,
    path: str | None = None,
) -> pd.DataFrame:
    """每日市场涨跌家数走势（一条 SQL 同时给出 上涨/下跌/涨停/跌停）。

    列：date / up / down / limit_up / limit_down。
    """
    df = db.query_df(
        """
        SELECT date,
               SUM(CASE WHEN pctChg > 0 THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN pctChg < 0 THEN 1 ELSE 0 END) AS down,
               SUM(CASE WHEN pctChg >= ? THEN 1 ELSE 0 END) AS limit_up,
               SUM(CASE WHEN pctChg <= ? THEN 1 ELSE 0 END) AS limit_down
        FROM kline
        GROUP BY date
        ORDER BY date
        """,
        [up_threshold, down_threshold],
        path=path,
    )
    if df.empty:
        return df
    for c in ("up", "down", "limit_up", "limit_down"):
        df[c] = df[c].astype(int)
    return df


def day_movers(date: str, path: str | None = None) -> pd.DataFrame:
    """某交易日全部上涨/下跌个股（含名称、开盘价、收盘价、涨跌幅），按涨跌幅降序。

    列：code / code_name / open / close / pctChg。
    """
    return db.query_df(
        """
        SELECT k.code, m.code_name, k.open, k.close, k.pctChg
        FROM kline k
        LEFT JOIN stock_meta m ON k.code = m.code
        WHERE k.date = CAST(? AS DATE) AND k.pctChg <> 0
        ORDER BY k.pctChg DESC
        """,
        [date],
        path=path,
    )


def load_stock_kline(code: str, path: str | None = None) -> pd.DataFrame:
    """单只股票完整 K线（按日期升序）。无数据抛 LookupError。"""
    df = db.query_df(
        "SELECT * FROM kline WHERE code = ? ORDER BY date",
        [code],
        path=path,
    )
    if df.empty:
        raise LookupError(f"No data for {code}")
    return df.reset_index(drop=True)


def add_moving_averages(df: pd.DataFrame, windows=MA_WINDOWS) -> pd.DataFrame:
    """在 K线 df 上追加 MA{w} 列（基于收盘价）。"""
    out = df.copy()
    for w in windows:
        out[f"MA{w}"] = out["close"].rolling(window=w, min_periods=w).mean()
    return out


def rolling_volatility(code: str, window: int = 20, path: str | None = None) -> pd.Series:
    """个股滚动年化波动率（日收益标准差 * sqrt(252)）。"""
    df = load_stock_kline(code, path=path)
    daily_return = df["close"].pct_change()
    return daily_return.rolling(window=window).std() * np.sqrt(252)


def volatility_frame(code: str, window: int = 20, path: str | None = None) -> pd.DataFrame:
    """带日期的滚动波动率。列：date / value（单次查询）。"""
    df = load_stock_kline(code, path=path)
    ret = df["close"].pct_change()
    vol = ret.rolling(window=window).std() * np.sqrt(252)
    return pd.DataFrame({"date": df["date"].to_numpy(), "value": vol.to_numpy()})


# ========== 排行榜 ==========
def top_movers(
    df: pd.DataFrame,
    n: int = 10,
    metric: str = "pctChg",
    ascending: bool = False,
) -> pd.DataFrame:
    """排行榜（涨幅/跌幅/成交额/换手率等）。纯 pandas，作用于已加载快照 df。"""
    if df.empty or metric not in df.columns:
        return pd.DataFrame()
    return df.sort_values(metric, ascending=ascending).head(n).reset_index(drop=True)


# ========== 名称映射 / 搜索 ==========
def name_map(path: str | None = None) -> dict:
    """代码 -> 公司名称映射。无表/无数据返回空字典。"""
    try:
        df = db.query_df("SELECT code, code_name FROM stock_meta", path=path)
    except Exception:
        return {}
    if df.empty:
        return {}
    return dict(zip(df["code"], df["code_name"]))


def search_stocks(query: str, limit: int = 50, path: str | None = None) -> pd.DataFrame:
    """按代码或名称模糊搜索。列：code / code_name。"""
    like = f"%{query}%"
    return db.query_df(
        """
        SELECT code, code_name FROM stock_meta
        WHERE code ILIKE ? OR code_name ILIKE ?
        ORDER BY code
        LIMIT ?
        """,
        [like, like, limit],
        path=path,
    )


# ========== MA5 > MA20 多头时长 ==========
def ma_duration_samples(
    start_date: str = "2025-01-01",
    ma_short: int = 5,
    ma_long: int = 20,
    path: str | None = None,
) -> pd.DataFrame:
    """全市场 MA{short}>MA{long} 金叉区间样本（DuckDB 一次拉取 + 复用纯函数）。

    返回 detail：code/start_date/end_date/duration/ongoing，按时长降序。
    """
    cols = ["code", "start_date", "end_date", "duration", "ongoing"]
    allrows = db.query_df(
        "SELECT code, date, close FROM kline ORDER BY code, date",
        path=path,
    )
    if allrows.empty:
        return pd.DataFrame(columns=cols)

    samples: list[dict] = []
    market_last = None
    for code, group in allrows.groupby("code", sort=False):
        g = group[["date", "close"]].reset_index(drop=True)
        g = add_ma(g, ma_short, ma_long)
        last_d = g["date"].iloc[-1]
        if market_last is None or last_d > market_last:
            market_last = last_d
        samples.extend(extract_samples(g, code, start_date))

    detail = pd.DataFrame(samples, columns=cols)
    detail = apply_strict_ongoing(detail, market_last)
    if not detail.empty:
        detail = detail.sort_values("duration", ascending=False).reset_index(drop=True)
    return detail


# ========== 数据状态 ==========
def data_status(path: str | None = None) -> dict:
    """数据新鲜度与覆盖：最新交易日、覆盖股票数、总行数。"""
    if not db.database_exists(path):
        return {"latest_date": None, "n_codes": 0, "n_rows": 0}
    df = db.query_df(
        "SELECT MAX(date) AS latest_date, COUNT(DISTINCT code) AS n_codes, COUNT(*) AS n_rows FROM kline",
        path=path,
    )
    row = df.iloc[0]
    latest = row["latest_date"]
    return {
        "latest_date": None if pd.isna(latest) else pd.Timestamp(latest).strftime("%Y-%m-%d"),
        "n_codes": int(row["n_codes"]),
        "n_rows": int(row["n_rows"]),
    }
