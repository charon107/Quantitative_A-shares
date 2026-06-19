"""
单元测试：metrics.py 统计函数
边界情况、错误处理、数据质量验证
"""
import os
import tempfile
import pytest
import pandas as pd
import numpy as np
from src.visualization.metrics import (
    load_all_latest_day,
    load_stock_kline,
    market_breadth,
    equal_weighted_index,
    limit_up_down_series,
    rolling_volatility,
    top_movers,
)


class TestLoadStockKline:
    """测试 load_stock_kline() — 加载单只股票完整 K线"""

    def test_normal_case(self, tmp_path):
        """正常：加载并按日期排序"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        # 故意打乱顺序写入
        df = pd.DataFrame({
            "date": ["2025-06-19", "2025-06-17", "2025-06-18"],
            "code": ["sh.601988"] * 3,
            "open": [10.4, 10.0, 10.2],
            "high": [10.9, 10.5, 10.7],
            "low": [10.3, 9.9, 10.1],
            "close": [10.6, 10.2, 10.4],
            "volume": [1400000, 1000000, 1200000],
            "amount": [1.4e7, 1e7, 1.2e7],
            "turn": [0.014, 0.01, 0.012],
            "pctChg": [0.9, 0.5, 0.8],
        })
        df.to_parquet(kline_dir / "sh.601988.parquet")

        result = load_stock_kline("sh.601988", str(tmp_path))

        # 应该按日期升序排列
        assert len(result) == 3
        assert list(result["date"]) == ["2025-06-17", "2025-06-18", "2025-06-19"]
        assert result["close"].iloc[-1] == 10.6
        # OHLCV 列齐全
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in result.columns

    def test_nonexistent_code(self, tmp_path):
        """边界：代码不存在抛 FileNotFoundError"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        with pytest.raises(FileNotFoundError):
            load_stock_kline("sh.999999", str(tmp_path))

    def test_empty_parquet(self, tmp_path):
        """边界：文件存在但为空抛 KeyError"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        empty_df = pd.DataFrame(columns=["date", "code", "close"])
        empty_df.to_parquet(kline_dir / "sh.601988.parquet")

        with pytest.raises(KeyError):
            load_stock_kline("sh.601988", str(tmp_path))


class TestLoadAllLatestDay:
    """测试 load_all_latest_day() — 加载所有股票最新一日"""

    def test_normal_case(self, tmp_path):
        """正常情况：3 个 parquet 文件，加载最新一日"""
        # 准备数据：3 只股票，每个 5 日数据
        for code in ["sh.601988", "sz.000001", "sh.600000"]:
            df = pd.DataFrame({
                "date": ["2025-06-15", "2025-06-16", "2025-06-17", "2025-06-18", "2025-06-19"],
                "code": [code] * 5,
                "open": [10.0, 10.1, 10.2, 10.3, 10.4],
                "high": [10.5, 10.6, 10.7, 10.8, 10.9],
                "low": [9.9, 10.0, 10.1, 10.2, 10.3],
                "close": [10.2, 10.3, 10.4, 10.5, 10.6],
                "volume": [1000000, 1100000, 1200000, 1300000, 1400000],
                "amount": [1e7, 1.1e7, 1.2e7, 1.3e7, 1.4e7],
                "turn": [0.01, 0.011, 0.012, 0.013, 0.014],
                "pctChg": [0.5, 1.0, 0.8, 1.2, 0.9],
            })
            kline_dir = tmp_path / "kline_fq"
            kline_dir.mkdir(exist_ok=True)
            df.to_parquet(kline_dir / f"{code}.parquet")

        # 调用函数
        result = load_all_latest_day(str(tmp_path))

        # 验证：应该只有最新一日（2025-06-19）的 3 只股票
        assert len(result) == 3
        assert all(result["date"] == "2025-06-19")
        assert set(result["code"]) == {"sh.601988", "sz.000001", "sh.600000"}
        assert result.loc[result["code"] == "sh.601988", "close"].values[0] == 10.6

    def test_empty_directory(self, tmp_path):
        """边界：目录为空，无 parquet 文件"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        result = load_all_latest_day(str(tmp_path))

        assert result.empty

    def test_missing_volume_column(self, tmp_path):
        """边界：某个 parquet 缺失 volume 列"""
        # 第一个文件正常
        df1 = pd.DataFrame({
            "date": ["2025-06-19"],
            "code": ["sh.601988"],
            "close": [10.6],
            "pctChg": [0.5],
            "volume": [1000000],
        })
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)
        df1.to_parquet(kline_dir / "sh.601988.parquet")

        # 第二个文件缺失 volume 列
        df2 = pd.DataFrame({
            "date": ["2025-06-19"],
            "code": ["sz.000001"],
            "close": [20.0],
            "pctChg": [1.0],
            # volume 缺失
        })
        df2.to_parquet(kline_dir / "sz.000001.parquet")

        # 调用函数 — 应该不报错，但可能只加载第一个或两个都加载（取决于实现）
        result = load_all_latest_day(str(tmp_path))
        assert len(result) >= 1


class TestMarketBreadth:
    """测试 market_breadth() — 涨跌家数、涨跌比"""

    def test_normal_case(self):
        """正常：混合涨跌平"""
        df = pd.DataFrame({
            "code": ["A", "B", "C", "D", "E"],
            "pctChg": [1.0, 0.5, 0.0, -0.5, -1.0],  # 2涨 1平 2跌
        })

        result = market_breadth(df)

        assert result["up"] == 2
        assert result["down"] == 2
        assert result["flat"] == 1
        assert result["ratio"] == 1.0  # 2/2

    def test_all_up(self):
        """边界：所有股票都涨"""
        df = pd.DataFrame({
            "code": ["A", "B", "C"],
            "pctChg": [1.0, 0.5, 2.0],
        })

        result = market_breadth(df)

        assert result["up"] == 3
        assert result["down"] == 0
        assert result["flat"] == 0
        assert result["ratio"] == np.inf

    def test_all_down(self):
        """边界：所有股票都跌"""
        df = pd.DataFrame({
            "code": ["A", "B", "C"],
            "pctChg": [-1.0, -0.5, -2.0],
        })

        result = market_breadth(df)

        assert result["up"] == 0
        assert result["down"] == 3
        assert result["flat"] == 0
        assert result["ratio"] == 0.0

    def test_empty_dataframe(self):
        """边界：空 DataFrame"""
        df = pd.DataFrame({"code": [], "pctChg": []})

        result = market_breadth(df)

        assert result["up"] == 0
        assert result["down"] == 0
        assert result["flat"] == 0
        assert np.isnan(result["ratio"])


class TestEqualWeightedIndex:
    """测试 equal_weighted_index() — 等权指数走势"""

    def test_normal_case(self, tmp_path):
        """正常：多日等权收益累计"""
        # 准备 2 只股票，3 日数据
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        for code in ["sh.601988", "sz.000001"]:
            df = pd.DataFrame({
                "date": ["2025-06-17", "2025-06-18", "2025-06-19"],
                "code": [code] * 3,
                "close": [10.0, 10.5, 10.9],  # 日收益率约 5%, 4.7%
            })
            df.to_parquet(kline_dir / f"{code}.parquet")

        result = equal_weighted_index(str(tmp_path), start_date="2025-06-17")

        # pct_change 会产生 1 个 NaN（第一日），所以日期数是 2
        assert len(result) == 2
        assert result.index[0] == "2025-06-18"
        assert result.index[-1] == "2025-06-19"
        # 累计收益应该是正的
        assert result.iloc[-1] > 0

    def test_single_stock(self, tmp_path):
        """边界：只有 1 只股票"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        df = pd.DataFrame({
            "date": ["2025-06-17", "2025-06-18", "2025-06-19"],
            "code": ["sh.601988"] * 3,
            "close": [10.0, 10.5, 10.9],
        })
        df.to_parquet(kline_dir / "sh.601988.parquet")

        result = equal_weighted_index(str(tmp_path), start_date="2025-06-17")

        assert len(result) == 2  # pct_change 产生 1 个 NaN

    def test_start_date_after_last_date(self, tmp_path):
        """边界：start_date > 最后数据日期"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        df = pd.DataFrame({
            "date": ["2025-06-17"],
            "code": ["sh.601988"],
            "close": [10.0],
        })
        df.to_parquet(kline_dir / "sh.601988.parquet")

        result = equal_weighted_index(str(tmp_path), start_date="2025-07-01")

        # 应该返回空 Series 或只有一行
        assert len(result) <= 1


class TestLimitUpDownSeries:
    """测试 limit_up_down_series() — 涨停/跌停走势"""

    def test_normal_case(self, tmp_path):
        """正常：多日涨停跌停统计"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        for code in ["sh.601988", "sz.000001", "sh.600000"]:
            df = pd.DataFrame({
                "date": ["2025-06-17", "2025-06-18", "2025-06-19"],
                "code": [code] * 3,
                "pctChg": [10.0, 9.8, 5.0] if code == "sh.601988" else [-10.0, 8.5, -2.0],
            })
            df.to_parquet(kline_dir / f"{code}.parquet")

        result = limit_up_down_series(str(tmp_path))

        # 应该有日期列和 up/down 列
        assert "date" in result.columns
        assert "limit_up" in result.columns
        assert "limit_down" in result.columns
        assert len(result) == 3
        # 2025-06-17 应该有 1 只涨停（sh.601988），1 只跌停（sz.000001）
        assert result.loc[result["date"] == "2025-06-17", "limit_up"].values[0] >= 1

    def test_no_limit_day(self, tmp_path):
        """边界：某日没有涨停/跌停"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        df = pd.DataFrame({
            "date": ["2025-06-17", "2025-06-18"],
            "code": ["sh.601988"] * 2,
            "pctChg": [0.5, 1.0],  # 都没有涨停/跌停
        })
        df.to_parquet(kline_dir / "sh.601988.parquet")

        result = limit_up_down_series(str(tmp_path))

        assert all(result["limit_up"] == 0)
        assert all(result["limit_down"] == 0)


class TestRollingVolatility:
    """测试 rolling_volatility() — 个股滚动波动率"""

    def test_normal_case(self, tmp_path):
        """正常：计算 20 日年化波动率"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        # 30 日数据，使用有一定波动的价格
        dates = pd.date_range("2025-05-20", periods=30, freq="D").strftime("%Y-%m-%d")
        np.random.seed(42)
        # 生成有波动性的价格数据
        returns = np.random.randn(30) * 0.02  # 日收益率波动 2%
        closes = 10.0 * np.exp(np.cumsum(returns))

        df = pd.DataFrame({
            "date": dates,
            "code": ["sh.601988"] * 30,
            "close": closes,
        })
        df.to_parquet(kline_dir / "sh.601988.parquet")

        result = rolling_volatility("sh.601988", tmp_path, window=20)

        # 应该有 30 行数据
        assert len(result) == 30
        # 前 20 行（0-19）应该是 NaN（因为 pct_change 产生 1 个 NaN，rolling(20) 需要 20 个有效值）
        assert pd.isna(result.iloc[0:20]).all()
        # 从第 21 行（索引 20）开始应该有值
        assert not pd.isna(result.iloc[20])

    def test_insufficient_data(self, tmp_path):
        """边界：数据少于 window"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        df = pd.DataFrame({
            "date": ["2025-06-17", "2025-06-18", "2025-06-19"],  # 只有 3 日
            "code": ["sh.601988"] * 3,
            "close": [10.0, 10.5, 10.9],
        })
        df.to_parquet(kline_dir / "sh.601988.parquet")

        result = rolling_volatility("sh.601988", tmp_path, window=20)

        # 应该全部是 NaN
        assert result.isna().all()

    def test_nonexistent_code(self, tmp_path):
        """边界：代码不存在"""
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)

        # 应该返回空 Series 或 raise FileNotFoundError
        with pytest.raises((FileNotFoundError, KeyError)):
            rolling_volatility("sh.999999", tmp_path, window=20)


class TestTopMovers:
    """测试 top_movers() — 排行榜（涨幅/跌幅/成交额/换手率）"""

    def test_top_gainers(self):
        """正常：涨幅 Top10"""
        df = pd.DataFrame({
            "code": [f"sh.{6000+i}" for i in range(20)],
            "pctChg": np.random.randn(20) * 5 + 10,  # 平均 10% 涨幅
            "amount": np.random.rand(20) * 1e8,
            "turn": np.random.rand(20) * 0.1,
        })

        result = top_movers(df, n=10, metric="pctChg", ascending=False)

        assert len(result) == 10
        # 验证排序：涨幅最大的在前
        assert result["pctChg"].iloc[0] >= result["pctChg"].iloc[-1]

    def test_fewer_rows_than_n(self):
        """边界：df 少于 n 行"""
        df = pd.DataFrame({
            "code": ["sh.601988", "sz.000001", "sh.600000"],
            "pctChg": [1.0, 0.5, -0.5],
            "amount": [1e7, 1.1e7, 0.9e7],
            "turn": [0.01, 0.011, 0.009],
        })

        result = top_movers(df, n=10, metric="pctChg")

        # 应该返回全部 3 行
        assert len(result) == 3

    def test_top_volume(self):
        """正常：成交额 Top10"""
        df = pd.DataFrame({
            "code": [f"sh.{6000+i}" for i in range(15)],
            "pctChg": np.random.randn(15),
            "amount": np.random.exponential(1e8, 15),  # 指数分布（有大有小）
            "turn": np.random.rand(15) * 0.1,
        })

        result = top_movers(df, n=10, metric="amount", ascending=False)

        assert len(result) == 10
        assert result["amount"].iloc[0] >= result["amount"].iloc[-1]
