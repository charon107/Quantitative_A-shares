"""基本面选股逻辑单测：构造小面板，验证逐年5年窗口的三条件筛选与边界。"""
import os
import sys

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.analysis import fundamental_screen as fs  # noqa: E402


def _rows(code, years, roe, yoy, debt, np_list):
    """按年生成 stock_fundamental 原始行（单位为小数，与采集层归一后一致）。"""
    return pd.DataFrame({
        "code": code,
        "year": list(years),
        "ann_date": [f"{y + 1}-04-15" for y in years],
        "roe": roe,
        "netprofit_yoy": yoy,
        "debt_ratio": debt,
        "net_profit": np_list,
    })


def _panel(*frames) -> pd.DataFrame:
    return fs._finalize_panel(pd.concat(frames, ignore_index=True))


def test_qualified_stock_is_picked():
    """5 年三条件全部达标 -> 入选。窗口 [2013..2017]，选股年 2018。"""
    years = range(2013, 2018)
    good = _rows(
        "sh.600519", years,
        roe=[0.30, 0.28, 0.32, 0.31, 0.29],       # 均值>0.15，最小>0.10
        yoy=[0.20, 0.18, 0.25, 0.22, 0.19],       # 每年>0.10
        debt=[0.30, 0.28, 0.29, 0.27, 0.25],      # 期末总债务/总资产<0.5
        np_list=[100, 120, 150, 180, 210],         # 恒正、CAGR>0.15
    )
    picked = fs.pick_stocks_by_year(_panel(good), 2018)
    assert picked == ["sh.600519"]


def test_high_debt_rejected():
    """期末总债务/总资产 >= 0.5 -> 剔除（条件3）。"""
    years = range(2013, 2018)
    bad = _rows(
        "sh.600000", years,
        roe=[0.30] * 5, yoy=[0.20] * 5,
        debt=[0.30, 0.30, 0.30, 0.30, 0.60],       # 期末 0.60 >= 0.5
        np_list=[100, 120, 150, 180, 210],
    )
    assert fs.pick_stocks_by_year(_panel(bad), 2018) == []


def test_debt_checked_only_at_window_end():
    """条件3 只看期末：窗口中间年份债务比高不影响入选。"""
    years = range(2013, 2018)
    ok = _rows(
        "sh.600036", years,
        roe=[0.30] * 5, yoy=[0.20] * 5,
        debt=[0.80, 0.80, 0.80, 0.80, 0.30],       # 仅期末 0.30 < 0.5
        np_list=[100, 120, 150, 180, 210],
    )
    assert fs.pick_stocks_by_year(_panel(ok), 2018) == ["sh.600036"]


def test_incomplete_window_rejected():
    """窗口内不足 5 年 -> 剔除（只有 4 年数据）。"""
    years = range(2014, 2018)  # 仅 4 年
    partial = _rows(
        "sh.600519", years,
        roe=[0.30] * 4, yoy=[0.20] * 4, debt=[0.30] * 4,
        np_list=[120, 150, 180, 210],
    )
    assert fs.pick_stocks_by_year(_panel(partial), 2018) == []


def test_low_roe_rejected():
    """ROE 均值不足 0.15 -> 剔除（条件1）。"""
    years = range(2013, 2018)
    bad = _rows(
        "sz.000001", years,
        roe=[0.12, 0.11, 0.13, 0.10, 0.11],        # 均值<0.15
        yoy=[0.20] * 5, debt=[0.30] * 5,
        np_list=[100, 120, 150, 180, 210],
    )
    assert fs.pick_stocks_by_year(_panel(bad), 2018) == []


def test_low_yoy_rejected():
    """任一年净利润增速 < 0.10 -> 剔除（条件2）。"""
    years = range(2013, 2018)
    bad = _rows(
        "sz.000002", years,
        roe=[0.30] * 5,
        yoy=[0.20, 0.20, 0.08, 0.20, 0.20],        # 2015 年 8% < 10%
        debt=[0.30] * 5,
        np_list=[100, 120, 150, 180, 210],
    )
    assert fs.pick_stocks_by_year(_panel(bad), 2018) == []


def test_select_pool_across_years():
    """select_pool 汇总多年，带出 code_name。"""
    years = range(2013, 2018)
    good = _rows(
        "sh.600519", years,
        roe=[0.30] * 5, yoy=[0.20] * 5, debt=[0.30] * 5,
        np_list=[100, 120, 150, 180, 210],
    )
    out = fs.select_pool(_panel(good), {"sh.600519": "贵州茅台"}, start_year=2018, end_year=2018)
    assert list(out.columns) == ["year", "code", "code_name"]
    assert out.iloc[0]["code_name"] == "贵州茅台"
    assert int(out.iloc[0]["year"]) == 2018


def test_select_pool_excludes_not_yet_listed():
    """选股年年初尚未上市的股票剔除（tushare 含招股书上市前财务，须防前视）。"""
    years = range(2013, 2018)
    good = _rows(
        "sh.603999", years,
        roe=[0.30] * 5, yoy=[0.20] * 5, debt=[0.30] * 5,
        np_list=[100, 120, 150, 180, 210],
    )
    panel = _panel(good)
    # 2019-06 才上市：2018 年选股应剔除，2020 年应保留
    ld = {"sh.603999": "2019-06-20"}
    out18 = fs.select_pool(panel, {}, start_year=2018, end_year=2018, list_dates=ld)
    assert out18.empty
    good2 = _rows(
        "sh.603999", range(2015, 2020),
        roe=[0.30] * 5, yoy=[0.20] * 5, debt=[0.30] * 5,
        np_list=[100, 120, 150, 180, 210],
    )
    out20 = fs.select_pool(_panel(good2), {}, start_year=2020, end_year=2020, list_dates=ld)
    assert list(out20["code"]) == ["sh.603999"]
    # 无上市日记录的股票不剔除
    out_no_ld = fs.select_pool(panel, {}, start_year=2018, end_year=2018, list_dates={"sz.000001": "1991-04-03"})
    assert list(out_no_ld["code"]) == ["sh.603999"]


def test_select_pool_excludes_bank_industry():
    """银行股剔除：条件3 只算借款+债券，银行以极低 debt_ratio 假性通过，须按行业排除。"""
    years = range(2013, 2018)
    bank = _rows(
        "sh.600000", years,
        roe=[0.20] * 5, yoy=[0.15] * 5, debt=[0.04] * 5,
        np_list=[100, 120, 150, 180, 210],
    )
    maker = _rows(
        "sz.000651", years,
        roe=[0.30] * 5, yoy=[0.20] * 5, debt=[0.04] * 5,
        np_list=[100, 120, 150, 180, 210],
    )
    inds = {"sh.600000": "银行", "sz.000651": "家用电器"}
    out = fs.select_pool(_panel(bank, maker), {}, start_year=2018, end_year=2018, industries=inds)
    assert list(out["code"]) == ["sz.000651"]
    # 无行业记录不剔除
    out2 = fs.select_pool(_panel(bank), {}, start_year=2018, end_year=2018, industries={"x": "y"})
    assert list(out2["code"]) == ["sh.600000"]


# ===== default_end_year：年报披露季边界 =====
from datetime import date

from src.analysis.fundamental_screen import default_end_year


def test_default_end_year_after_disclosure_deadline_uses_current_year():
    assert default_end_year(date(2026, 5, 1)) == 2026
    assert default_end_year(date(2026, 7, 9)) == 2026
    assert default_end_year(date(2026, 12, 31)) == 2026


def test_default_end_year_before_deadline_uses_previous_year():
    assert default_end_year(date(2026, 1, 1)) == 2025
    assert default_end_year(date(2026, 4, 30)) == 2025
