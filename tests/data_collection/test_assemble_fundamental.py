"""assemble_annual_fundamental 单测：fina 缺失年份用三大报表推算指标的正确性与边界。"""
import os
import sys

import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.data_collection.tushare_client import assemble_annual_fundamental  # noqa: E402

CODE = "sh.600000"


def _fina(years, roe, yoy):
    return pd.DataFrame({
        "code": CODE, "year": list(years),
        "ann_date": [f"{y + 1}-04-15" for y in years],
        "roe": roe, "netprofit_yoy": yoy,
    })


def _inc(years, np_list):
    return pd.DataFrame({
        "code": CODE, "year": list(years),
        "ann_date": [f"{y + 1}-04-20" for y in years],
        "net_profit": np_list,
    })


def _cf(years, cfo):
    return pd.DataFrame({"code": CODE, "year": list(years), "cfo": cfo})


def _bal(years, st=None, lt=None, bond=None, assets=None, equity=None):
    n = len(list(years))
    return pd.DataFrame({
        "code": CODE, "year": list(years),
        "st_borr": st if st is not None else [0.0] * n,
        "lt_borr": lt if lt is not None else [0.0] * n,
        "bond_payable": bond if bond is not None else [0.0] * n,
        "total_assets": assets if assets is not None else [1000.0] * n,
        "equity": equity if equity is not None else [500.0] * n,
    })


def test_fina_values_kept_and_debt_ratio_from_balance():
    """fina 有值的年份原样保留；debt_ratio 全部年份由资产负债表计算。"""
    years = [2012, 2013]
    out = assemble_annual_fundamental(
        CODE,
        _fina(years, roe=[0.20, 0.22], yoy=[0.15, 0.18]),
        _inc(years, np_list=[100.0, 120.0]),
        _cf(years, cfo=[90.0, 110.0]),
        _bal(years, st=[50.0, 60.0], lt=[100.0, 100.0], bond=[0.0, 40.0], assets=[1000.0, 1000.0]),
    )
    assert list(out["year"]) == years
    assert out["roe"].tolist() == pytest.approx([0.20, 0.22])
    assert out["netprofit_yoy"].tolist() == pytest.approx([0.15, 0.18])
    assert out["debt_ratio"].tolist() == pytest.approx([0.15, 0.20])  # (50+100+0)/1000, (60+100+40)/1000
    assert out["cfo"].tolist() == pytest.approx([90.0, 110.0])


def test_hole_years_derived_from_statements():
    """fina 缺 2008-2011（代理数据洞）时，roe/yoy 用 income+balancesheet 推算。"""
    all_years = [2007, 2008, 2009]
    out = assemble_annual_fundamental(
        CODE,
        _fina([2007], roe=[0.18], yoy=[0.12]),  # 2008/2009 缺失
        _inc(all_years, np_list=[100.0, 130.0, 156.0]),
        _cf(all_years, cfo=[90.0, 120.0, 150.0]),
        _bal(all_years, equity=[500.0, 550.0, 650.0]),
    )
    by_year = out.set_index("year")
    # 2008: yoy = 130/100-1 = 0.30；roe = 130/((500+550)/2)
    assert by_year.loc[2008, "netprofit_yoy"] == pytest.approx(0.30)
    assert by_year.loc[2008, "roe"] == pytest.approx(130.0 / 525.0)
    # 2009: yoy = 156/130-1 = 0.20；roe = 156/((550+650)/2)
    assert by_year.loc[2009, "netprofit_yoy"] == pytest.approx(0.20)
    assert by_year.loc[2009, "roe"] == pytest.approx(156.0 / 600.0)
    # fina 有值的 2007 不被推算值覆盖
    assert by_year.loc[2007, "roe"] == pytest.approx(0.18)


def test_derivation_requires_consecutive_years():
    """上一年数据不连续（隔年）时，yoy/roe 不得用错基期 -> 保持 NaN。"""
    out = assemble_annual_fundamental(
        CODE,
        _fina([], roe=[], yoy=[]),
        _inc([2007, 2009], np_list=[100.0, 150.0]),  # 缺 2008
        _cf([2007, 2009], cfo=[90.0, 140.0]),
        _bal([2007, 2009], equity=[500.0, 600.0]),
    )
    by_year = out.set_index("year")
    assert pd.isna(by_year.loc[2009, "netprofit_yoy"])
    assert pd.isna(by_year.loc[2009, "roe"])


def test_turnaround_yoy_uses_abs_base():
    """上年净利润为负时，同比用 |基期| 做分母（扭亏为正增长）。"""
    out = assemble_annual_fundamental(
        CODE,
        _fina([], roe=[], yoy=[]),
        _inc([2008, 2009], np_list=[-100.0, 50.0]),
        _cf([2008, 2009], cfo=[0.0, 40.0]),
        _bal([2008, 2009], equity=[500.0, 550.0]),
    )
    by_year = out.set_index("year")
    assert by_year.loc[2009, "netprofit_yoy"] == pytest.approx(1.5)  # (50-(-100))/100


def test_zero_prev_np_gives_nan_yoy():
    """上年净利润为 0 时同比无意义 -> NaN。"""
    out = assemble_annual_fundamental(
        CODE,
        _fina([], roe=[], yoy=[]),
        _inc([2008, 2009], np_list=[0.0, 50.0]),
        _cf([2008, 2009], cfo=[0.0, 40.0]),
        _bal([2008, 2009], equity=[500.0, 550.0]),
    )
    by_year = out.set_index("year")
    assert pd.isna(by_year.loc[2009, "netprofit_yoy"])


def test_missing_balance_row_gives_nan_debt_ratio():
    """某年无资产负债表行 -> debt_ratio 为 NaN（而不是当成 0 债务）。"""
    out = assemble_annual_fundamental(
        CODE,
        _fina([2012, 2013], roe=[0.2, 0.2], yoy=[0.15, 0.15]),
        _inc([2012, 2013], np_list=[100.0, 120.0]),
        _cf([2012, 2013], cfo=[90.0, 100.0]),
        _bal([2012], st=[50.0], assets=[1000.0]),  # 2013 缺
    )
    by_year = out.set_index("year")
    assert by_year.loc[2012, "debt_ratio"] == pytest.approx(0.05)
    assert pd.isna(by_year.loc[2013, "debt_ratio"])


def test_nan_debt_components_treated_as_zero():
    """借款/债券科目缺项视为 0（无借款公司 debt_ratio = 0 而非 NaN）。"""
    nan = float("nan")
    bal = _bal([2012], st=[nan], lt=[nan], bond=[nan], assets=[1000.0])
    out = assemble_annual_fundamental(
        CODE,
        _fina([2012], roe=[0.2], yoy=[0.15]),
        _inc([2012], np_list=[100.0]),
        _cf([2012], cfo=[90.0]),
        bal,
    )
    assert out.set_index("year").loc[2012, "debt_ratio"] == pytest.approx(0.0)


def test_ann_date_falls_back_to_income():
    """fina 缺失年份的 ann_date 用 income 的公告日兜底。"""
    out = assemble_annual_fundamental(
        CODE,
        _fina([2012], roe=[0.2], yoy=[0.15]),
        _inc([2012, 2013], np_list=[100.0, 120.0]),
        _cf([2012, 2013], cfo=[90.0, 100.0]),
        _bal([2012, 2013]),
    )
    by_year = out.set_index("year")
    assert by_year.loc[2012, "ann_date"] == "2013-04-15"  # fina 的公告日
    assert by_year.loc[2013, "ann_date"] == "2014-04-20"  # income 兜底


def test_all_empty_inputs():
    """四个输入全空 -> 空结果（列齐全）。"""
    empty = pd.DataFrame()
    out = assemble_annual_fundamental(CODE, empty, empty, empty, empty)
    assert out.empty
    assert list(out.columns) == [
        "code", "year", "ann_date", "roe", "netprofit_yoy", "debt_ratio", "net_profit", "cfo",
    ]
