"""assemble_quarterly_fundamental 单测：累计/单季差分、fina 缺失时的报表推算兜底与边界。"""
import os
import sys

import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src import db  # noqa: E402
from src.data_collection import tushare_client as tsc  # noqa: E402
from src.data_collection.tushare_client import assemble_quarterly_fundamental  # noqa: E402

CODE = "sh.600000"
EMPTY = pd.DataFrame()


def _frame(end_dates, ann=None, **metrics):
    """构造 fetch_*_raw 风格的最小原始帧（end_date 为 'YYYYMMDD'，缺失指标列由组装器补 NA）。"""
    n = len(end_dates)
    data = {"code": CODE, "end_date": list(end_dates)}
    data["ann_date"] = list(ann) if ann is not None else [None] * n
    data.update({k: list(v) for k, v in metrics.items()})
    return pd.DataFrame(data)


def _by_period(out: pd.DataFrame):
    return out.set_index(["year", "quarter"])


def test_output_columns_sync_with_db_schema():
    """采集层列常量与 db 表列常量保持同步（防止两边加列漏改）。"""
    assert db.QUARTERLY_FUNDAMENTAL_COLUMNS == [
        "code", "end_date", "year", "quarter", "ann_date", *tsc.QUARTERLY_METRIC_COLUMNS,
    ]


def test_q_diff_q1_equals_cumulative_and_qn_is_delta():
    """Q1 单季=累计；Qn 单季=本期累计−上期累计；缺上一季则 NULL。"""
    inc = _frame(
        ["20230331", "20230630", "20231231"],  # Q3 缺失（老年份只披露半年报/年报的情形）
        net_profit=[100.0, 250.0, 500.0],
        revenue=[1000.0, 2200.0, 5000.0],
    )
    cf = _frame(["20230331", "20230630", "20231231"], cfo=[80.0, 190.0, 400.0])
    out = _by_period(assemble_quarterly_fundamental(CODE, EMPTY, inc, cf, EMPTY))

    assert out.loc[(2023, 1), "q_net_profit"] == pytest.approx(100.0)  # Q1=累计
    assert out.loc[(2023, 2), "q_net_profit"] == pytest.approx(150.0)  # 250-100
    assert pd.isna(out.loc[(2023, 4), "q_net_profit"])  # Q3 缺失，Q4 不差分
    assert out.loc[(2023, 2), "q_revenue"] == pytest.approx(1200.0)
    assert out.loc[(2023, 2), "q_cfo"] == pytest.approx(110.0)
    # 累计值原样入库
    assert out.loc[(2023, 4), "net_profit"] == pytest.approx(500.0)


def test_fina_values_kept_not_overwritten_by_derivation():
    """fina 有值的报告期原样保留，不被报表推算覆盖。"""
    fina = _frame(["20230331"], ann=["2023-04-25"], roe=[0.30], netprofit_margin=[0.20])
    inc = _frame(["20230331"], net_profit=[100.0], revenue=[1000.0])
    out = _by_period(assemble_quarterly_fundamental(CODE, fina, inc, EMPTY, EMPTY))
    assert out.loc[(2023, 1), "roe"] == pytest.approx(0.30)
    assert out.loc[(2023, 1), "netprofit_margin"] == pytest.approx(0.20)


def test_ratio_derivation_when_fina_missing():
    """fina 缺失（代理 2006-2011 数据洞）时用报表推算：净利率/毛利率/ROE/ROA/资产负债率。"""
    inc = _frame(["20221231", "20230331"], net_profit=[400.0, 100.0],
                 revenue=[4000.0, 1000.0], oper_cost=[2400.0, 600.0])
    bal = _frame(
        ["20221231", "20230331"],
        total_assets=[1900.0, 2100.0], total_liab=[650.0, 700.0], equity=[900.0, 1100.0],
    )
    out = _by_period(assemble_quarterly_fundamental(CODE, EMPTY, inc, EMPTY, bal))

    q1 = out.loc[(2023, 1)]
    assert q1["netprofit_margin"] == pytest.approx(0.10)  # 100/1000
    assert q1["grossprofit_margin"] == pytest.approx(0.40)  # (1000-600)/1000
    assert q1["roe"] == pytest.approx(100.0 / 1000.0)  # 期初=上年末: (900+1100)/2
    assert q1["roa"] == pytest.approx(100.0 / 2000.0)  # (1900+2100)/2
    assert q1["debt_to_assets"] == pytest.approx(700.0 / 2100.0)
    # 时点值直接入库
    assert q1["total_assets"] == pytest.approx(2100.0)
    assert q1["total_liab"] == pytest.approx(700.0)


def test_q_roe_derivation_uses_adjacent_prev_period_equity():
    """单季 ROE 兜底：期初=上一报告期末净资产（上年Q4 → 本年Q1 跨年相邻也可用）。"""
    inc = _frame(["20221231", "20230331"], net_profit=[400.0, 100.0], revenue=[4000.0, 1000.0])
    bal = _frame(["20221231", "20230331"], equity=[900.0, 1100.0])
    out = _by_period(assemble_quarterly_fundamental(CODE, EMPTY, inc, EMPTY, bal))
    # q_net_profit(2023Q1)=100（Q1=累计），平均净资产 (900+1100)/2=1000
    assert out.loc[(2023, 1), "q_roe"] == pytest.approx(0.10)


def test_yoy_derivation_from_same_quarter_last_year():
    """累计同比兜底：与去年同期累计值比较；单季同比用单季差分值比较。"""
    inc = _frame(
        ["20220331", "20220630", "20230331", "20230630"],
        net_profit=[80.0, 200.0, 100.0, 250.0],
        revenue=[800.0, 2000.0, 1000.0, 2200.0],
    )
    out = _by_period(assemble_quarterly_fundamental(CODE, EMPTY, inc, EMPTY, EMPTY))

    assert out.loc[(2023, 2), "netprofit_yoy"] == pytest.approx(0.25)  # (250-200)/200
    assert out.loc[(2023, 2), "or_yoy"] == pytest.approx(0.10)  # (2200-2000)/2000
    # 单季：2022Q2 单季=120，2023Q2 单季=150 -> yoy=0.25；环比 (150-100)/100=0.5
    assert out.loc[(2023, 2), "q_netprofit_yoy"] == pytest.approx(0.25)
    assert out.loc[(2023, 2), "q_netprofit_qoq"] == pytest.approx(0.50)
    # 无去年同期 -> NULL
    assert pd.isna(out.loc[(2022, 1), "netprofit_yoy"])


def test_restated_period_keeps_latest_announcement():
    """同一报告期原始/更正多行，按公告日保留最新一次。"""
    inc = _frame(
        ["20230331", "20230331"],
        ann=["2023-04-20", "2023-06-30"],
        net_profit=[100.0, 105.0],
        revenue=[1000.0, 1050.0],
    )
    out = _by_period(assemble_quarterly_fundamental(CODE, EMPTY, inc, EMPTY, EMPTY))
    assert len(out) == 1
    assert out.loc[(2023, 1), "net_profit"] == pytest.approx(105.0)
    assert out.loc[(2023, 1), "ann_date"] == "2023-06-30"


def test_ann_date_falls_back_to_income():
    """fina 缺失报告期的 ann_date 用 income 公告日兜底。"""
    fina = _frame(["20230331"], ann=["2023-04-25"], roe=[0.3])
    inc = _frame(["20230331", "20230630"], ann=["2023-04-28", "2023-08-20"],
                 net_profit=[100.0, 250.0], revenue=[1000.0, 2200.0])
    out = _by_period(assemble_quarterly_fundamental(CODE, fina, inc, EMPTY, EMPTY))
    assert out.loc[(2023, 1), "ann_date"] == "2023-04-25"  # fina 优先
    assert out.loc[(2023, 2), "ann_date"] == "2023-08-20"  # income 兜底


def test_nonstandard_period_dropped_and_sorted():
    """非标准季末报告期被丢弃；输出按报告期升序，end_date 为 ISO 季末。"""
    inc = _frame(["20230630", "20231115", "20230331"],
                 net_profit=[250.0, 999.0, 100.0], revenue=[2200.0, 9.0, 1000.0])
    out = assemble_quarterly_fundamental(CODE, EMPTY, inc, EMPTY, EMPTY)
    assert out["end_date"].tolist() == ["2023-03-31", "2023-06-30"]
    assert out["quarter"].tolist() == [1, 2]


def test_eps_no_fallback_stays_null():
    """每股类/扣非类无兜底来源，fina 缺失时保持 NULL（前端断线呈现）。"""
    inc = _frame(["20230331"], net_profit=[100.0], revenue=[1000.0])
    out = _by_period(assemble_quarterly_fundamental(CODE, EMPTY, inc, EMPTY, EMPTY))
    assert pd.isna(out.loc[(2023, 1), "eps"])
    assert pd.isna(out.loc[(2023, 1), "roe_dt"])
    assert pd.isna(out.loc[(2023, 1), "profit_dedt"])


def test_all_empty_inputs():
    """四个输入全空 -> 空结果（列齐全）。"""
    out = assemble_quarterly_fundamental(CODE, EMPTY, EMPTY, EMPTY, EMPTY)
    assert out.empty
    assert list(out.columns) == [
        "code", "end_date", "year", "quarter", "ann_date", *tsc.QUARTERLY_METRIC_COLUMNS,
    ]
