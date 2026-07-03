"""基本面选股逻辑单测：构造小面板，验证逐年5年窗口的阈值筛选与边界。"""
import os
import sys

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.analysis import fundamental_screen as fs  # noqa: E402


def _rows(code, years, roe, yoy, liab, np_list, cfo):
    """按年生成 stock_fundamental 原始行（单位为小数，与采集层 ÷100 后一致）。"""
    return pd.DataFrame({
        "code": code,
        "year": list(years),
        "ann_date": [f"{y + 1}-04-15" for y in years],
        "roe": roe,
        "netprofit_yoy": yoy,
        "debt_to_assets": liab,
        "net_profit": np_list,
        "cfo": cfo,
    })


def _panel(*frames) -> pd.DataFrame:
    return fs._finalize_panel(pd.concat(frames, ignore_index=True))


def test_qualified_stock_is_picked():
    """5 年全部达标 -> 入选。窗口 [2013..2017]，选股年 2018。"""
    years = range(2013, 2018)
    good = _rows(
        "sh.600519", years,
        roe=[0.30, 0.28, 0.32, 0.31, 0.29],       # 均值>0.15，最小>0.10
        yoy=[0.20, 0.18, 0.25, 0.22, 0.19],       # 每年>0.10
        liab=[0.30, 0.28, 0.29, 0.27, 0.25],      # 期末<0.5
        np_list=[100, 120, 150, 180, 210],         # 恒正、CAGR>0.15
        cfo=[90, 110, 140, 170, 200],              # CFO/NP 均值>0.7
    )
    picked = fs.pick_stocks_by_year(_panel(good), 2018)
    assert picked == ["sh.600519"]


def test_high_debt_rejected():
    """期末资产负债率 >= 0.5 -> 剔除。"""
    years = range(2013, 2018)
    bad = _rows(
        "sh.600000", years,
        roe=[0.30] * 5, yoy=[0.20] * 5,
        liab=[0.30, 0.30, 0.30, 0.30, 0.60],       # 期末 0.60 >= 0.5
        np_list=[100, 120, 150, 180, 210],
        cfo=[90, 110, 140, 170, 200],
    )
    assert fs.pick_stocks_by_year(_panel(bad), 2018) == []


def test_incomplete_window_rejected():
    """窗口内不足 5 年 -> 剔除（只有 4 年数据）。"""
    years = range(2014, 2018)  # 仅 4 年
    partial = _rows(
        "sh.600519", years,
        roe=[0.30] * 4, yoy=[0.20] * 4, liab=[0.30] * 4,
        np_list=[120, 150, 180, 210], cfo=[110, 140, 170, 200],
    )
    assert fs.pick_stocks_by_year(_panel(partial), 2018) == []


def test_low_roe_rejected():
    """ROE 均值不足 0.15 -> 剔除。"""
    years = range(2013, 2018)
    bad = _rows(
        "sz.000001", years,
        roe=[0.12, 0.11, 0.13, 0.10, 0.11],        # 均值<0.15
        yoy=[0.20] * 5, liab=[0.30] * 5,
        np_list=[100, 120, 150, 180, 210], cfo=[90, 110, 140, 170, 200],
    )
    assert fs.pick_stocks_by_year(_panel(bad), 2018) == []


def test_select_pool_across_years():
    """select_pool 汇总多年，带出 code_name。"""
    years = range(2013, 2018)
    good = _rows(
        "sh.600519", years,
        roe=[0.30] * 5, yoy=[0.20] * 5, liab=[0.30] * 5,
        np_list=[100, 120, 150, 180, 210], cfo=[90, 110, 140, 170, 200],
    )
    out = fs.select_pool(_panel(good), {"sh.600519": "贵州茅台"}, start_year=2018, end_year=2018)
    assert list(out.columns) == ["year", "code", "code_name"]
    assert out.iloc[0]["code_name"] == "贵州茅台"
    assert int(out.iloc[0]["year"]) == 2018
