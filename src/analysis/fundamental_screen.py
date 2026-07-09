"""基本面选股（逐年历史回测口径）— DuckDB 版。

严格实现策略规格的三个条件（在年份 select_year，用财年窗口
[fy_end-4 .. fy_end]（fy_end = select_year-1，共 5 年）筛选，要求各指标 5 年齐全）：

  条件1（盈利能力）：ROE = 净利润/股东权益。
      过去五年平均 ROE >= 15%，且过去五年最小 ROE >= 10%。
  条件2（成长性）：过去五年净利润复合增长率 >= 15%，
      且过去五年最低净利润增长率 >= 10%，且过去五年净利润均 > 0。
  条件3（负债）：总债务/总资产 < 0.5，
      总债务 = 长期借款 + 短期借款 + 应付债券（有息负债口径，非总负债）。

数据来自 DuckDB 的 `stock_fundamental` 表（tushare 采集，比率已 ÷100 归一为小数），
code->name 从 `stock_meta` 取。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src import db

# ===== 策略阈值（单位为小数；tushare 采集层已 ÷100 归一化）=====
ROE_MEAN_MIN = 0.15    # 条件1：近5年平均 ROE
ROE_MIN_MIN = 0.10     # 条件1：近5年最小 ROE
YOYNI_MIN = 0.10       # 条件2：近5年每年净利润增速
NP_CAGR_MIN = 0.15     # 条件2：近5年净利润 CAGR
DEBT_RATIO_MAX = 0.5   # 条件3：期末总债务/总资产（有息负债口径）

WINDOW_YEARS = 5  # 最近 5 年

# 剔除的行业（tushare stock_basic.industry 口径）。银行的资金来源是存款，不计入
# 条件3 的"借款+债券"，会以极低 debt_ratio 通过筛选，但其报表结构与实体企业不可比。
EXCLUDED_INDUSTRIES = ("银行",)

DEFAULT_START_YEAR = 2013

# 年报披露截止 4/30：5 月起才能用去年年报（fy_end = 去年）对当年选股。
ANNUAL_REPORT_DEADLINE_MONTH = 5


def default_end_year(today: date | None = None) -> int:
    """选股年份上限：今年（年报披露季结束后）或去年（1–4 月，去年年报未披露完）。"""
    d = today or date.today()
    return d.year if d.month >= ANNUAL_REPORT_DEADLINE_MONTH else d.year - 1


DEFAULT_END_YEAR = default_end_year()


def compute_cagr(v_start: float, v_end: float, years: int) -> float:
    """净利润复合增长率；起止值非正或缺失时返回 NaN。"""
    if v_start is None or v_end is None:
        return float("nan")
    if pd.isna(v_start) or pd.isna(v_end):
        return float("nan")
    if v_start <= 0 or v_end <= 0:
        return float("nan")
    return (v_end / v_start) ** (1.0 / years) - 1.0


def load_fundamental_panel(path: str | None = None) -> pd.DataFrame:
    """读取全市场年报财务面板。列：code/year/ann_date/roe/netprofit_yoy/debt_ratio/net_profit。"""
    df = db.query_df(
        "SELECT code, year, ann_date, roe, netprofit_yoy, debt_ratio, net_profit "
        "FROM stock_fundamental",
        path=path,
    )
    return _finalize_panel(df)


def _finalize_panel(df: pd.DataFrame) -> pd.DataFrame:
    """把原始 stock_fundamental 行整理成选股面板（类型规整 + 去重）。"""
    if df.empty:
        return df
    df = df.copy()
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    for c in ("roe", "netprofit_yoy", "debt_ratio", "net_profit"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values(["code", "year"]).drop_duplicates(["code", "year"], keep="last")


def panel_from_conn(conn) -> pd.DataFrame:
    """用已打开的 DuckDB 连接读取选股面板（入库脚本复用同一写连接，避免重复 open 文件）。"""
    df = conn.execute(
        "SELECT code, year, ann_date, roe, netprofit_yoy, debt_ratio, net_profit "
        "FROM stock_fundamental"
    ).df()
    return _finalize_panel(df)


def pick_stocks_by_year(panel: pd.DataFrame, select_year: int) -> list[str]:
    """在年份 select_year 选股，返回 code 列表（sh.600000 风格，已排序）。

    实现模块 docstring 的三条件：
    - 条件1：roe 均值 >= 0.15 且每年 >= 0.10
    - 条件2：netprofit_yoy 每年 >= 0.10、净利润恒正、净利润 5 年 CAGR >= 0.15
    - 条件3：期末 debt_ratio（总债务/总资产）< 0.5
    - roe/netprofit_yoy/net_profit 近 5 年齐全（>=5 条）
    """
    fy_end = select_year - 1
    years = list(range(fy_end - (WINDOW_YEARS - 1), fy_end + 1))  # 5 年窗口

    sub = panel[panel["year"].isin(years)].copy()
    if sub.empty:
        return []

    g = sub.groupby("code", as_index=False)
    roe_stats = g["roe"].agg(roe_mean="mean", roe_min="min", roe_cnt="count")
    yoy_stats = g["netprofit_yoy"].agg(yoy_min="min", yoy_cnt="count")

    sub_sorted = sub.sort_values(["code", "year"])
    first_np = sub_sorted.groupby("code")["net_profit"].first().rename("np_first")
    last_np = sub_sorted.groupby("code")["net_profit"].last().rename("np_last")
    np_min = sub_sorted.groupby("code")["net_profit"].min().rename("np_min")
    np_cnt = sub_sorted.groupby("code")["net_profit"].count().rename("np_cnt")
    np_stats = pd.concat([first_np, last_np, np_min, np_cnt], axis=1).reset_index()

    debt_end = (
        sub_sorted[sub_sorted["year"] == fy_end][["code", "debt_ratio"]]
        .drop_duplicates("code", keep="last")
        .rename(columns={"debt_ratio": "debt_end"})
    )

    stats = (
        roe_stats.merge(yoy_stats, on="code", how="inner")
        .merge(np_stats, on="code", how="inner")
        .merge(debt_end, on="code", how="inner")
    )

    stats["np_cagr"] = stats.apply(
        lambda r: compute_cagr(r["np_first"], r["np_last"], years=WINDOW_YEARS - 1),
        axis=1,
    )

    need_cnt = WINDOW_YEARS
    stats = stats[
        (stats["roe_cnt"] >= need_cnt)
        & (stats["yoy_cnt"] >= need_cnt)
        & (stats["np_cnt"] >= need_cnt)
    ].copy()

    picked = stats[
        (stats["roe_mean"] >= ROE_MEAN_MIN)
        & (stats["roe_min"] >= ROE_MIN_MIN)
        & (stats["yoy_min"] >= YOYNI_MIN)
        & (stats["np_min"] > 0)
        & (stats["np_cagr"] >= NP_CAGR_MIN)
        & (stats["debt_end"] < DEBT_RATIO_MAX)
    ]["code"].sort_values().tolist()

    return picked


def select_pool(
    panel: pd.DataFrame,
    name_map: dict | None = None,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
    list_dates: dict | None = None,
    industries: dict | None = None,
) -> pd.DataFrame:
    """纯函数：给定财务面板 + code->name，跑逐年选股。返回 DataFrame[year, code, code_name]。

    list_dates（code -> 'YYYY-MM-DD' 上市日）用于回测可交易性过滤：选股年年初尚未
    上市的股票剔除——tushare 收录招股书披露的上市前财务，不过滤会把未上市新股选进
    历史年份的池子（当年根本买不到）。无上市日记录的股票不剔除（宁可多留）。

    industries（code -> 行业名）用于剔除 EXCLUDED_INDUSTRIES（如银行）。
    无行业记录的股票不剔除。

    与数据源解耦，便于单测，也便于入库脚本用「已打开的写连接」读到的 panel 直接调用，
    避免对同一 DuckDB 文件重复 open。
    """
    if panel is None or panel.empty:
        return pd.DataFrame(columns=["year", "code", "code_name"])
    nm = name_map or {}
    ld = list_dates or {}
    inds = industries or {}
    rows: list[pd.DataFrame] = []
    for y in range(start_year, end_year + 1):
        codes = pick_stocks_by_year(panel, y)
        if ld:
            cutoff = f"{y}-01-01"
            codes = [c for c in codes if not ld.get(c) or str(ld[c]) <= cutoff]
        if inds:
            codes = [c for c in codes if inds.get(c) not in EXCLUDED_INDUSTRIES]
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
    try:
        li = db.query_df("SELECT code, list_date, industry FROM stock_info", path=path)
        list_dates = dict(zip(li["code"], li["list_date"])) if not li.empty else {}
        industries = dict(zip(li["code"], li["industry"])) if not li.empty else {}
    except Exception:
        list_dates, industries = {}, {}
    return select_pool(panel, nm, start_year, end_year, list_dates=list_dates, industries=industries)
