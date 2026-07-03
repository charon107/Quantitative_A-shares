"""基本面选股（逐年历史回测口径）— DuckDB 版。

从 `SURF根据基本面选股并存储.py` 移植而来，逻辑与阈值原样保留，仅把数据源
从本地 parquet 换成 DuckDB 的 `stock_fundamental` 表，code->name 从 `stock_meta`
取，去掉 baostock 登录。

选股语义：在年份 `select_year` 用财年窗口 [fy_end-4 .. fy_end]（fy_end = select_year-1，
共 5 年）做筛选，要求 5 年数据齐全并满足下列全部阈值。
"""
from __future__ import annotations

import pandas as pd

from src import db

# ===== 策略阈值（沿用原脚本，单位为小数；tushare 采集层已 ÷100 归一化）=====
ROE_MEAN_MIN = 0.15
ROE_MIN_MIN = 0.10
YOYNI_MIN = 0.10
NP_CAGR_MIN = 0.15
LIAB_TO_ASSET_MAX = 0.5
CFO_TO_NP_MEAN_MIN = 0.7

WINDOW_YEARS = 5  # 最近 5 年

DEFAULT_START_YEAR = 2013
DEFAULT_END_YEAR = 2025


def compute_cagr(v_start: float, v_end: float, years: int) -> float:
    """净利润复合增长率；起止值非正或缺失时返回 NaN（原脚本口径）。"""
    if v_start is None or v_end is None:
        return float("nan")
    if pd.isna(v_start) or pd.isna(v_end):
        return float("nan")
    if v_start <= 0 or v_end <= 0:
        return float("nan")
    return (v_end / v_start) ** (1.0 / years) - 1.0


def load_fundamental_panel(path: str | None = None) -> pd.DataFrame:
    """读取全市场年报财务面板。列：code/year/ann_date/roe/netprofit_yoy/debt_to_assets/net_profit/cfo。"""
    df = db.query_df(
        "SELECT code, year, ann_date, roe, netprofit_yoy, debt_to_assets, net_profit, cfo "
        "FROM stock_fundamental",
        path=path,
    )
    return _finalize_panel(df)


def _finalize_panel(df: pd.DataFrame) -> pd.DataFrame:
    """把原始 stock_fundamental 行整理成选股面板（类型规整 + 派生 cfo_to_np）。"""
    if df.empty:
        return df
    df = df.copy()
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    for c in ("roe", "netprofit_yoy", "debt_to_assets", "net_profit", "cfo"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["cfo_to_np"] = df["cfo"] / df["net_profit"].where(df["net_profit"] > 0)
    return df.sort_values(["code", "year"]).drop_duplicates(["code", "year"], keep="last")


def panel_from_conn(conn) -> pd.DataFrame:
    """用已打开的 DuckDB 连接读取选股面板（入库脚本复用同一写连接，避免重复 open 文件）。"""
    df = conn.execute(
        "SELECT code, year, ann_date, roe, netprofit_yoy, debt_to_assets, net_profit, cfo "
        "FROM stock_fundamental"
    ).df()
    return _finalize_panel(df)


def pick_stocks_by_year(panel: pd.DataFrame, select_year: int) -> list[str]:
    """在年份 select_year 选股，返回 code 列表（sh.600000 风格，已排序）。

    与原 `SURF根据基本面选股并存储.py` 的 pick_stocks_by_year 等价：
    - roe 均值 >= 0.15 且每年 >= 0.10
    - netprofit_yoy 每年 >= 0.10、净利润恒正、净利润 5 年 CAGR >= 0.15
    - 期末资产负债率 < 0.5、经营现金流/净利润均值 >= 0.7
    - 各指标近 5 年齐全（>=5 条）
    """
    fy_end = select_year - 1
    years = list(range(fy_end - (WINDOW_YEARS - 1), fy_end + 1))  # 5 年窗口

    sub = panel[panel["year"].isin(years)].copy()
    if sub.empty:
        return []

    g = sub.groupby("code", as_index=False)
    roe_stats = g["roe"].agg(roe_mean="mean", roe_min="min", roe_cnt="count")
    yoy_stats = g["netprofit_yoy"].agg(yoy_min="min", yoy_cnt="count")
    cfo_stats = g["cfo_to_np"].agg(cfo_mean="mean", cfo_cnt="count")

    sub_sorted = sub.sort_values(["code", "year"])
    first_np = sub_sorted.groupby("code")["net_profit"].first().rename("np_first")
    last_np = sub_sorted.groupby("code")["net_profit"].last().rename("np_last")
    np_min = sub_sorted.groupby("code")["net_profit"].min().rename("np_min")
    np_cnt = sub_sorted.groupby("code")["net_profit"].count().rename("np_cnt")
    np_stats = pd.concat([first_np, last_np, np_min, np_cnt], axis=1).reset_index()

    liab_end = (
        sub_sorted[sub_sorted["year"] == fy_end][["code", "debt_to_assets"]]
        .drop_duplicates("code", keep="last")
        .rename(columns={"debt_to_assets": "liab_end"})
    )

    stats = (
        roe_stats.merge(yoy_stats, on="code", how="inner")
        .merge(cfo_stats, on="code", how="inner")
        .merge(np_stats, on="code", how="inner")
        .merge(liab_end, on="code", how="inner")
    )

    stats["np_cagr"] = stats.apply(
        lambda r: compute_cagr(r["np_first"], r["np_last"], years=WINDOW_YEARS - 1),
        axis=1,
    )

    need_cnt = WINDOW_YEARS
    stats = stats[
        (stats["roe_cnt"] >= need_cnt)
        & (stats["yoy_cnt"] >= need_cnt)
        & (stats["cfo_cnt"] >= need_cnt)
        & (stats["np_cnt"] >= need_cnt)
    ].copy()

    picked = stats[
        (stats["roe_mean"] >= ROE_MEAN_MIN)
        & (stats["roe_min"] >= ROE_MIN_MIN)
        & (stats["yoy_min"] >= YOYNI_MIN)
        & (stats["np_min"] > 0)
        & (stats["np_cagr"] >= NP_CAGR_MIN)
        & (stats["liab_end"] < LIAB_TO_ASSET_MAX)
        & (stats["cfo_mean"] >= CFO_TO_NP_MEAN_MIN)
    ]["code"].sort_values().tolist()

    return picked


def select_pool(
    panel: pd.DataFrame,
    name_map: dict | None = None,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
) -> pd.DataFrame:
    """纯函数：给定财务面板 + code->name，跑逐年选股。返回 DataFrame[year, code, code_name]。

    与数据源解耦，便于单测，也便于入库脚本用「已打开的写连接」读到的 panel 直接调用，
    避免对同一 DuckDB 文件重复 open。
    """
    if panel is None or panel.empty:
        return pd.DataFrame(columns=["year", "code", "code_name"])
    nm = name_map or {}
    rows: list[pd.DataFrame] = []
    for y in range(start_year, end_year + 1):
        codes = pick_stocks_by_year(panel, y)
        if not codes:
            continue
        rows.append(pd.DataFrame({"year": y, "code": codes, "code_name": [nm.get(c) for c in codes]}))
    if not rows:
        return pd.DataFrame(columns=["year", "code", "code_name"])
    out = pd.concat(rows, ignore_index=True)
    return out.sort_values(["year", "code"]).reset_index(drop=True)


def run_selection(
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
    path: str | None = None,
) -> pd.DataFrame:
    """跑逐年选股（独立进程用；自开只读连接）。返回 DataFrame[year, code, code_name]。"""
    panel = load_fundamental_panel(path=path)
    if panel.empty:
        return pd.DataFrame(columns=["year", "code", "code_name"])
    name_map = db.query_df("SELECT code, code_name FROM stock_meta", path=path)
    nm = dict(zip(name_map["code"], name_map["code_name"])) if not name_map.empty else {}
    return select_pool(panel, nm, start_year, end_year)
