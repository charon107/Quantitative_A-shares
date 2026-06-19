"""
A股股价数据可视化看板 — 统计计算模块

纯函数集合，输入 parquet 或 DataFrame，输出统计指标。
无 UI 逻辑，便于单独测试和缓存。
"""
import os
import glob
import pandas as pd
import numpy as np
from pathlib import Path


def load_all_latest_day(data_dir: str, kline_subdir: str = "kline_fq") -> pd.DataFrame:
    """
    汇总所有股票的最新一日数据。

    参数：
        data_dir: 数据根目录（如 "股价数据_parquet_fq"）
        kline_subdir: K线 parquet 子目录名（默认 "kline_fq"）

    返回：
        DataFrame，包含所有股票最新一日的 OHLCV 数据。
        如果无数据，返回空 DataFrame。
    """
    kline_dir = Path(data_dir) / kline_subdir
    if not kline_dir.exists():
        return pd.DataFrame()

    parquet_files = glob.glob(str(kline_dir / "*.parquet"))
    if not parquet_files:
        return pd.DataFrame()

    latest_data = []
    for parquet_file in parquet_files:
        try:
            df = pd.read_parquet(parquet_file)
            if df.empty:
                continue
            # 取最新一日（按 date 列排序后取最后一行）
            latest_row = df.sort_values("date").iloc[-1]
            latest_data.append(latest_row)
        except Exception:
            # 跳过无法读取的 parquet 文件
            continue

    if not latest_data:
        return pd.DataFrame()

    result = pd.DataFrame(latest_data)
    return result.reset_index(drop=True)


def market_breadth(df: pd.DataFrame) -> dict:
    """
    计算市场宽度指标：涨跌家数、涨跌比。

    参数：
        df: 包含 pctChg 列的 DataFrame（涨跌幅百分比）

    返回：
        字典：{
            'up': 上涨家数,
            'down': 下跌家数,
            'flat': 平盘家数,
            'ratio': 涨跌比 (up/down)，下跌为 0 时返回 inf，无数据时返回 nan
        }
    """
    if df.empty or "pctChg" not in df.columns:
        return {
            "up": 0,
            "down": 0,
            "flat": 0,
            "ratio": np.nan,
        }

    up = (df["pctChg"] > 0).sum()
    down = (df["pctChg"] < 0).sum()
    flat = (df["pctChg"] == 0).sum()

    if down == 0:
        ratio = np.inf if up > 0 else 0.0
    else:
        ratio = up / down

    return {
        "up": int(up),
        "down": int(down),
        "flat": int(flat),
        "ratio": ratio,
    }


def equal_weighted_index(
    data_dir: str,
    start_date: str = "2025-01-01",
    kline_subdir: str = "kline_fq",
) -> pd.Series:
    """
    计算等权指数走势（简单等权组合的累计收益）。

    方法：
        1. 加载所有股票的 K线数据
        2. 按日期分组，每日计算等权收益率（所有股票该日收益的算术平均）
        3. 累计收益（几何累乘）

    参数：
        data_dir: 数据根目录
        start_date: 起始日期（YYYY-MM-DD）
        kline_subdir: K线子目录

    返回：
        Series，index 为日期，values 为累计收益率（百分数，如 0.05 表示 +5%）
    """
    kline_dir = Path(data_dir) / kline_subdir
    parquet_files = glob.glob(str(kline_dir / "*.parquet"))

    if not parquet_files:
        return pd.Series(dtype=float)

    # 加载所有股票数据
    all_data = []
    for parquet_file in parquet_files:
        try:
            df = pd.read_parquet(parquet_file)
            if not df.empty and "date" in df.columns and "close" in df.columns:
                all_data.append(df[["date", "code", "close"]])
        except Exception:
            continue

    if not all_data:
        return pd.Series(dtype=float)

    combined = pd.concat(all_data, ignore_index=True)
    combined = combined.dropna(subset=["close"])

    # 计算每日收益率
    combined = combined.sort_values(["code", "date"]).reset_index(drop=True)
    combined["daily_return"] = combined.groupby("code")["close"].pct_change()

    # 按日期分组，计算等权收益率（过滤掉 NaN 后再取平均）
    daily_returns = combined.groupby("date").apply(
        lambda x: x["daily_return"].dropna().mean() if x["daily_return"].notna().any() else np.nan
    )
    daily_returns = daily_returns[daily_returns.index >= start_date]

    # 过滤掉 NaN 值（通常是第一日，因为 pct_change 会产生 NaN）
    daily_returns = daily_returns.dropna()

    if daily_returns.empty:
        return pd.Series(dtype=float)

    # 累计收益（几何累乘）：(1 + r1) * (1 + r2) * ... - 1
    cumulative_return = (1 + daily_returns).cumprod() - 1

    return cumulative_return


def limit_up_down_series(
    data_dir: str,
    up_threshold: float = 9.9,
    down_threshold: float = -9.9,
    kline_subdir: str = "kline_fq",
) -> pd.DataFrame:
    """
    计算每日涨停/跌停家数走势。

    参数：
        data_dir: 数据根目录
        up_threshold: 涨停阈值（默认 9.9%，对应前复权日线）
        down_threshold: 跌停阈值（默认 -9.9%）
        kline_subdir: K线子目录

    返回：
        DataFrame：
        {
            'date': 日期,
            'limit_up': 涨停家数,
            'limit_down': 跌停家数
        }
    """
    kline_dir = Path(data_dir) / kline_subdir
    parquet_files = glob.glob(str(kline_dir / "*.parquet"))

    if not parquet_files:
        return pd.DataFrame()

    # 加载所有数据
    all_data = []
    for parquet_file in parquet_files:
        try:
            df = pd.read_parquet(parquet_file)
            if not df.empty and "date" in df.columns and "pctChg" in df.columns:
                all_data.append(df[["date", "code", "pctChg"]])
        except Exception:
            continue

    if not all_data:
        return pd.DataFrame()

    combined = pd.concat(all_data, ignore_index=True)

    # 按日期统计涨停/跌停
    def count_limits(group):
        return pd.Series({
            "limit_up": (group["pctChg"] >= up_threshold).sum(),
            "limit_down": (group["pctChg"] <= down_threshold).sum(),
        })

    result = combined.groupby("date").apply(count_limits).reset_index()
    result = result.sort_values("date")

    return result


def rolling_volatility(
    code: str,
    data_dir: str,
    window: int = 20,
    kline_subdir: str = "kline_fq",
) -> pd.Series:
    """
    计算个股的滚动年化波动率。

    参数：
        code: 股票代码（如 "sh.601988"）
        data_dir: 数据根目录
        window: 窗口大小（默认 20 日）
        kline_subdir: K线子目录

    返回：
        Series，index 为日期，values 为年化波动率（百分数，如 0.25 表示 25%）
        数据不足 window 的行返回 NaN。
    """
    kline_dir = Path(data_dir) / kline_subdir
    parquet_file = kline_dir / f"{code}.parquet"

    if not parquet_file.exists():
        raise FileNotFoundError(f"Cannot find parquet file for {code}")

    df = pd.read_parquet(parquet_file)
    if df.empty:
        raise KeyError(f"No data for {code}")

    df = df.sort_values("date").reset_index(drop=True)

    # 计算日收益率
    df["daily_return"] = df["close"].pct_change()

    # 计算滚动标准差 * sqrt(252) 得年化波动率
    volatility = df["daily_return"].rolling(window=window).std() * np.sqrt(252)

    return volatility


def top_movers(
    df: pd.DataFrame,
    n: int = 10,
    metric: str = "pctChg",
    ascending: bool = False,
) -> pd.DataFrame:
    """
    获取排行榜（涨幅/跌幅/成交额/换手率等）。

    参数：
        df: 包含各指标列的 DataFrame（如 pctChg, amount, turn 等）
        n: 排行数量（默认 Top10）
        metric: 排序指标列名（默认 "pctChg"）
        ascending: 是否升序排列（默认降序）

    返回：
        排序后的 DataFrame，包含前 n 行（如果 df 少于 n 行，返回全部）。
    """
    if df.empty or metric not in df.columns:
        return pd.DataFrame()

    sorted_df = df.sort_values(metric, ascending=ascending)
    return sorted_df.head(n).reset_index(drop=True)
