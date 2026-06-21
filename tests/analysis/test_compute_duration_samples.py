"""
端到端测试：compute_duration_samples（编排函数，含真实 parquet IO）

用 tmp_path 写两只迷你股票，验证：
- 全市场遍历 + MA 计算 + 区间抽取 串起来产出正确样本；
- 跨股票的市场级 ongoing 收紧：只有 end_date == 全市场最新交易日 的样本才算未结束，
  数据提前结束（停牌/退市）的股票即使末段仍 MA5>MA10 也被改判已结束。
"""
import pandas as pd

from src.analysis.ma5_above_ma10_duration import compute_duration_samples


def _write_kline(kline_dir, code, closes, start="2025-01-02"):
    """写一只股票 parquet：date(工作日) + code + close。"""
    dates = pd.bdate_range(start=start, periods=len(closes))
    df = pd.DataFrame({"date": dates, "code": code, "close": closes})
    df.to_parquet(kline_dir / f"{code}.parquet")


class TestComputeDurationSamples:
    def test_empty_dir_returns_empty(self, tmp_path):
        """无 parquet：返回空 DataFrame（带列名），不抛异常。"""
        (tmp_path / "kline_fq").mkdir()
        out = compute_duration_samples(str(tmp_path))
        assert out.empty
        assert list(out.columns) == ["code", "start_date", "end_date", "duration", "ongoing"]

    def test_cross_stock_strict_ongoing(self, tmp_path):
        """
        LATE：14 个交易日、末段持续上涨到最后一根 → end_date==市场最新日 → ongoing=True。
        EARLY：12 个交易日（更早结束）、末段同样上涨 → 但 end_date<市场最新日 → 收紧为 ongoing=False。
        前 10 日恒为 10（MA5==MA10 不算区间），第 11 日起上涨触发金叉。
        """
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir()
        # 恒定 10 暖机 10 天，随后逐日上涨制造 MA5 上穿 MA10
        _write_kline(kline_dir, "sh.000001", [10] * 10 + [11, 12, 13, 14])  # LATE: 14 根
        _write_kline(kline_dir, "sz.000002", [10] * 10 + [11, 12])          # EARLY: 12 根

        out = compute_duration_samples(str(tmp_path))

        # 每只各一段金叉区间
        assert len(out) == 2
        assert set(out["code"]) == {"sh.000001", "sz.000002"}
        # 所有样本上穿日均在统计窗口内
        assert (out["start_date"] >= "2025-01-02").all()

        late = out[out["code"] == "sh.000001"].iloc[0]
        early = out[out["code"] == "sz.000002"].iloc[0]

        # LATE 末段延伸到全市场最新交易日 → 未结束
        assert bool(late["ongoing"]) is True
        assert late["duration"] == 4
        # EARLY 虽末段也在涨，但数据提前结束 → 市场口径下改判已结束
        assert bool(early["ongoing"]) is False
        assert early["duration"] == 2

        # 全市场仅 1 个未结束样本
        assert int(out["ongoing"].sum()) == 1
