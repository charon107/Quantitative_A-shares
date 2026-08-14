"""fetch_disclosed_report_codes 单测：按 actual_date 精确查询、校验返回日期、主板过滤与去重。"""
import os
import sys

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.data_collection import tushare_client as tsc  # noqa: E402


def _frame(rows):
    return pd.DataFrame(rows, columns=["ts_code", "end_date", "actual_date"])


def test_fetch_disclosed_report_codes_queries_by_actual_date_and_filters(monkeypatch):
    """逐日按 actual_date 查询；校验返回行 actual_date==请求日；只留主板并去重排序。"""
    responses = {
        "20260813": _frame([
            ["600257.SH", "20260630", "20260813"],  # 主板，保留
            ["000967.SZ", "20260630", "20260813"],  # 主板，保留
            ["300818.SZ", "20260630", "20260813"],  # 创业板，剔除
            ["688403.SH", "20260630", "20260813"],  # 科创板，剔除
            ["600000.SH", "20260630", "20260812"],  # 日期不符，剔除
        ]),
        "20260812": _frame([
            ["600257.SH", "20260630", "20260812"],  # 与 20260813 重复，去重
            ["000001.SZ", "20260630", "20260812"],  # 主板，保留
        ]),
        "20260811": _frame([]),
    }
    calls = []

    class FakePro:
        def disclosure_date(self, **kwargs):
            calls.append(kwargs)
            return responses.get(kwargs["actual_date"], pd.DataFrame())

    monkeypatch.setattr(tsc, "_pro", lambda: FakePro())
    monkeypatch.setattr(tsc, "_call_with_retry", lambda label, fn, *a, **kw: fn(*a, **kw))

    result = tsc.fetch_disclosed_report_codes("2026-08-13", tail_days=2)

    assert result == ["sh.600257", "sz.000001", "sz.000967"]
    # 确认走了 actual_date 精确查询（目标日 + 回溯 2 天），而非无过滤全量分页
    assert [c["actual_date"] for c in calls] == ["20260813", "20260812", "20260811"]
    assert all("offset" not in c and "limit" not in c for c in calls)


def test_fetch_disclosed_report_codes_empty_when_proxy_returns_nothing(monkeypatch):
    class FakePro:
        def disclosure_date(self, **kwargs):
            return pd.DataFrame()

    monkeypatch.setattr(tsc, "_pro", lambda: FakePro())
    monkeypatch.setattr(tsc, "_call_with_retry", lambda label, fn, *a, **kw: fn(*a, **kw))

    assert tsc.fetch_disclosed_report_codes("2026-08-13", tail_days=2) == []
