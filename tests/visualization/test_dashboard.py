"""
单元测试：dashboard.py 的辅助逻辑与容错分支

聚焦 dashboard 层独有的新逻辑：
- format_stock_label：代码<->名称标签格式化（纯函数）
- load_name_map / load_kline_cached：读 parquet + 容错（@st.cache_data）
- 4 个 tab 在空数据下的优雅降级（不抛异常）

tab 内部的 st.* 渲染细节属于 Streamlit 本身，不在此单测，留给手动/QA。
"""
import pandas as pd
import pytest

from src.visualization import dashboard


# ========== format_stock_label（纯函数） ==========
class TestFormatStockLabel:
    def test_with_name(self):
        """有名称：显示 '名称 (代码)'"""
        result = dashboard.format_stock_label("sh.600006", {"sh.600006": "东风股份"})
        assert result == "东风股份 (sh.600006)"

    def test_without_name_empty_map(self):
        """空映射：回退到只显示代码"""
        assert dashboard.format_stock_label("sh.600006", {}) == "sh.600006"

    def test_code_not_in_map(self):
        """代码不在映射中：回退到只显示代码"""
        result = dashboard.format_stock_label("sh.999999", {"sh.600006": "东风股份"})
        assert result == "sh.999999"


# ========== load_name_map（读 parquet + 容错） ==========
class TestLoadNameMap:
    def test_normal(self, tmp_path, monkeypatch):
        """正常：读取映射文件返回 dict"""
        df = pd.DataFrame({
            "code": ["sh.600006", "sz.000001"],
            "code_name": ["东风股份", "平安银行"],
        })
        df.to_parquet(tmp_path / "code_name_map.parquet")

        monkeypatch.setattr(dashboard, "DATA_DIR", str(tmp_path))
        dashboard.load_name_map.clear()  # 清掉 @st.cache_data 缓存

        result = dashboard.load_name_map()
        assert result == {"sh.600006": "东风股份", "sz.000001": "平安银行"}

    def test_missing_file(self, tmp_path, monkeypatch):
        """边界：映射文件不存在返回空 dict（看板回退到只显示代码）"""
        monkeypatch.setattr(dashboard, "DATA_DIR", str(tmp_path))
        dashboard.load_name_map.clear()

        assert dashboard.load_name_map() == {}


# ========== load_kline_cached（读 K线 + 容错） ==========
class TestLoadKlineCached:
    def _write_kline(self, tmp_path, code):
        kline_dir = tmp_path / "kline_fq"
        kline_dir.mkdir(exist_ok=True)
        df = pd.DataFrame({
            "date": ["2025-06-17", "2025-06-18", "2025-06-19"],
            "code": [code] * 3,
            "open": [10.0, 10.2, 10.4],
            "high": [10.5, 10.7, 10.9],
            "low": [9.9, 10.1, 10.3],
            "close": [10.2, 10.4, 10.6],
            "volume": [1e6, 1.2e6, 1.4e6],
            "amount": [1e7, 1.2e7, 1.4e7],
            "turn": [0.01, 0.012, 0.014],
            "pctChg": [0.5, 0.8, 0.9],
        })
        df.to_parquet(kline_dir / f"{code}.parquet")

    def test_normal(self, tmp_path, monkeypatch):
        """正常：加载 K线返回非空 DataFrame"""
        self._write_kline(tmp_path, "sh.600006")
        monkeypatch.setattr(dashboard, "DATA_DIR", str(tmp_path))
        dashboard.load_kline_cached.clear()

        result = dashboard.load_kline_cached("sh.600006")
        assert len(result) == 3
        assert result["close"].iloc[-1] == 10.6

    def test_missing_returns_empty(self, tmp_path, monkeypatch):
        """边界：代码不存在返回空 DataFrame（而非抛异常）"""
        (tmp_path / "kline_fq").mkdir(exist_ok=True)
        monkeypatch.setattr(dashboard, "DATA_DIR", str(tmp_path))
        dashboard.load_kline_cached.clear()

        result = dashboard.load_kline_cached("sh.999999")
        assert result.empty


# ========== 4 个 tab 在空数据下的优雅降级 ==========
class TestTabsEmptyDataGraceful:
    """空数据时每个 tab 都应优雅返回，不抛异常（走 error/warning 分支）"""

    @pytest.fixture(autouse=True)
    def _empty_data(self, monkeypatch):
        # 让所有数据加载函数返回空，模拟"数据目录为空/未初始化"
        monkeypatch.setattr(dashboard, "load_latest_day", lambda: pd.DataFrame())
        monkeypatch.setattr(dashboard, "load_equal_weighted_index", lambda *a, **k: pd.Series(dtype=float))
        monkeypatch.setattr(dashboard, "load_limit_up_down", lambda: pd.DataFrame())
        monkeypatch.setattr(dashboard, "load_name_map", lambda: {})

    def test_market_overview_no_crash(self):
        dashboard.tab_market_overview()  # 不应抛异常

    def test_stock_query_no_crash(self):
        dashboard.tab_stock_query()

    def test_rankings_no_crash(self):
        dashboard.tab_rankings()

    def test_data_status_no_crash(self):
        dashboard.tab_data_status()
