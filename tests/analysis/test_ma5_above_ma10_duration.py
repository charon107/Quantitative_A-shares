"""
单元测试：ma5_above_ma10_duration.extract_samples（核心区间抽取纯函数）

直接喂入构造好的 MA5/MA20，绕过 add_ma，聚焦：
- 完整金叉区间的识别与时长
- 左截断（上穿日 < START_DATE）剔除
- 右删失（延伸到序列末尾）标 ongoing
- 严格 > 边界（MA5==MA20 断开区间）
- NaN（无 MA10 的早期）不误判
"""
import numpy as np
import pandas as pd

from src.analysis.ma5_above_ma10_duration import extract_samples, apply_strict_ongoing

START = "2025-01-01"


def _df(dates, ma5, ma20):
    """用显式 MA5/MA20 构造 df（AAA 的 Arrange 辅助）。"""
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "MA5": ma5,
        "MA20": ma20,
    })


class TestExtractSamples:
    def test_closed_run_in_window(self):
        """2025 年内开始并结束的区间：1 个样本、时长正确、ongoing=False。"""
        df = _df(
            ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"],
            ma5=[9, 11, 12, 11, 9, 9],
            ma20=[10, 10, 10, 10, 10, 10],
        )
        out = extract_samples(df, "sh.600000", START)
        assert len(out) == 1
        s = out[0]
        assert s["start_date"] == "2025-01-02"
        assert s["end_date"] == "2025-01-06"
        assert s["duration"] == 3
        assert s["ongoing"] is False
        assert s["code"] == "sh.600000"

    def test_left_truncated_excluded(self):
        """上穿日在 2024（窗口前）的左截断区间：不计入。"""
        df = _df(
            ["2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"],
            ma5=[11, 12, 12, 9],
            ma20=[10, 10, 10, 10],
        )
        out = extract_samples(df, "sz.000001", START)
        assert out == []

    def test_ongoing_at_series_end(self):
        """区间延伸到最后一根 K 线：ongoing=True 且计入。"""
        df = _df(
            ["2025-03-01", "2025-03-02", "2025-03-03"],
            ma5=[9, 11, 12],
            ma20=[10, 10, 10],
        )
        out = extract_samples(df, "sh.600001", START)
        assert len(out) == 1
        assert out[0]["ongoing"] is True
        assert out[0]["duration"] == 2
        assert out[0]["start_date"] == "2025-03-02"

    def test_strict_greater_breaks_run(self):
        """MA5==MA20 当日不算区间内，区间在此断开为两段。"""
        df = _df(
            ["2025-05-01", "2025-05-02", "2025-05-05", "2025-05-06", "2025-05-07"],
            ma5=[11, 10, 11, 11, 9],   # 第 2 日相等
            ma20=[10, 10, 10, 10, 10],
        )
        out = extract_samples(df, "sh.600002", START)
        durations = sorted(s["duration"] for s in out)
        assert len(out) == 2
        assert durations == [1, 2]
        starts = sorted(s["start_date"] for s in out)
        assert starts == ["2025-05-01", "2025-05-05"]

    def test_nan_ma_not_a_run(self):
        """早期无 MA10（NaN）不误判为区间起点。"""
        df = _df(
            ["2025-02-01", "2025-02-02", "2025-02-03"],
            ma5=[np.nan, 11, 12],
            ma20=[np.nan, 10, 10],
        )
        out = extract_samples(df, "sh.600003", START)
        assert len(out) == 1
        assert out[0]["start_date"] == "2025-02-02"
        assert out[0]["duration"] == 2

    def test_empty_df(self):
        """空 df 不抛异常，返回空列表。"""
        assert extract_samples(pd.DataFrame(), "x", START) == []


class TestApplyStrictOngoing:
    MARKET_LAST = "2026-06-18"

    def test_demotes_stale_series(self):
        """end_date 早于市场最新日（停牌/退市）→ 改判已结束；等于市场最新日 → 保留 ongoing。"""
        # Arrange：两条 extract 阶段都被标 ongoing=True 的样本
        detail = pd.DataFrame({
            "code": ["sh.600000", "sz.000002"],
            "start_date": ["2025-02-01", "2026-06-01"],
            "end_date": ["2025-03-06", "2026-06-18"],  # 前者序列提前结束
            "duration": [10, 12],
            "ongoing": [True, True],
        })
        # Act
        out = apply_strict_ongoing(detail, self.MARKET_LAST)
        # Assert
        assert out.loc[0, "ongoing"] is np.False_ or out.loc[0, "ongoing"] == False  # noqa: E712
        assert bool(out.loc[1, "ongoing"]) is True
        assert int(out["ongoing"].sum()) == 1

    def test_empty_passthrough(self):
        """空 detail 原样返回，不抛异常。"""
        empty = pd.DataFrame(columns=["code", "start_date", "end_date", "duration", "ongoing"])
        assert apply_strict_ongoing(empty, self.MARKET_LAST).empty
