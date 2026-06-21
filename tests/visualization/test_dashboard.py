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


# ========== filter_stocks（纯函数：代码/名称/模糊搜索） ==========
class TestFilterStocks:
    CODES = ["sh.600015", "sh.600016", "sz.000001"]
    NAME_MAP = {
        "sh.600015": "华夏银行",
        "sh.600016": "民生银行",
        "sz.000001": "平安银行",
    }

    def test_by_company_name(self):
        """按公司名称子串命中"""
        result = dashboard.filter_stocks(self.CODES, self.NAME_MAP, "华夏")
        assert result == ["sh.600015"]

    def test_by_code_number(self):
        """按代码数字片段命中（不含市场前缀）"""
        result = dashboard.filter_stocks(self.CODES, self.NAME_MAP, "600015")
        assert result == ["sh.600015"]

    def test_by_code_prefix_multi(self):
        """按代码前缀模糊命中多只"""
        result = dashboard.filter_stocks(self.CODES, self.NAME_MAP, "sh.600")
        assert result == ["sh.600015", "sh.600016"]

    def test_case_insensitive(self):
        """大小写不敏感"""
        result = dashboard.filter_stocks(self.CODES, self.NAME_MAP, "SH.600015")
        assert result == ["sh.600015"]

    def test_fuzzy_name_substring(self):
        """名称模糊子串命中多只（'银行' 命中全部）"""
        result = dashboard.filter_stocks(self.CODES, self.NAME_MAP, "银行")
        assert result == ["sh.600015", "sh.600016", "sz.000001"]

    def test_empty_query_returns_all(self):
        """空 query 返回全部"""
        assert dashboard.filter_stocks(self.CODES, self.NAME_MAP, "") == self.CODES
        assert dashboard.filter_stocks(self.CODES, self.NAME_MAP, "   ") == self.CODES

    def test_no_match_returns_empty(self):
        """无命中返回空列表"""
        assert dashboard.filter_stocks(self.CODES, self.NAME_MAP, "不存在") == []

    def test_missing_name_falls_back_to_code(self):
        """name_map 缺失该代码时只按代码匹配、不报错"""
        result = dashboard.filter_stocks(["sh.600015"], {}, "600015")
        assert result == ["sh.600015"]
        # 名称搜索在无映射时不命中，但也不抛异常
        assert dashboard.filter_stocks(["sh.600015"], {}, "华夏") == []


# ========== samples_for_duration（纯函数：按时长筛公司） ==========
class TestSamplesForDuration:
    DETAIL = pd.DataFrame({
        "code": ["sh.600015", "sz.000001", "sh.600016", "sz.000002"],
        "start_date": ["2025-02-01", "2025-03-10", "2025-01-05", "2025-04-01"],
        "end_date": ["2025-02-12", "2025-03-21", "2025-01-16", "2026-06-18"],
        "duration": [7, 7, 12, 7],
        "ongoing": [False, False, False, True],
    })
    NAME_MAP = {"sh.600015": "华夏银行", "sz.000001": "平安银行", "sh.600016": "民生银行"}

    def test_filters_and_shapes(self):
        """选某时长：只返回该时长行、固定中文列、按上穿日降序"""
        out = dashboard.samples_for_duration(self.DETAIL, 7, self.NAME_MAP)
        assert list(out.columns) == ["名称", "代码", "上穿日", "结束日", "状态"]
        assert len(out) == 3
        # 按上穿日降序：2025-04-01 > 2025-03-10 > 2025-02-01
        assert out["代码"].tolist() == ["sz.000002", "sz.000001", "sh.600015"]

    def test_name_and_status_mapping(self):
        """名称映射 + ongoing → 未结束/已结束"""
        out = dashboard.samples_for_duration(self.DETAIL, 7, self.NAME_MAP)
        row_hx = out[out["代码"] == "sh.600015"].iloc[0]
        assert row_hx["名称"] == "华夏银行"
        assert row_hx["状态"] == "已结束"
        row_og = out[out["代码"] == "sz.000002"].iloc[0]
        assert row_og["状态"] == "未结束"
        assert row_og["名称"] == ""  # 不在 name_map 中 → 留空，不报错

    def test_other_duration(self):
        """另一时长只命中对应行"""
        out = dashboard.samples_for_duration(self.DETAIL, 12, self.NAME_MAP)
        assert out["代码"].tolist() == ["sh.600016"]
        assert out.iloc[0]["状态"] == "已结束"

    def test_no_match_returns_empty(self):
        """无匹配时长 → 空 DataFrame"""
        assert dashboard.samples_for_duration(self.DETAIL, 99, self.NAME_MAP).empty

    def test_empty_detail(self):
        """空明细 → 空 DataFrame，不抛异常"""
        assert dashboard.samples_for_duration(pd.DataFrame(), 7, {}).empty


# ========== dedup_recent（纯函数：最近查看去重置顶） ==========
class TestDedupRecent:
    def test_empty_history(self):
        """空历史：只含新代码"""
        assert dashboard.dedup_recent([], "sh.600015") == ["sh.600015"]

    def test_prepend_new(self):
        """新代码置顶，其余保序"""
        assert dashboard.dedup_recent(["a", "b"], "c") == ["c", "a", "b"]

    def test_dedup_move_to_front(self):
        """已存在的代码移到最前，不重复"""
        assert dashboard.dedup_recent(["a", "b", "c"], "b") == ["b", "a", "c"]

    def test_truncate_to_max(self):
        """超出 max_n 截断，保留最近的"""
        assert dashboard.dedup_recent(["a", "b", "c"], "d", max_n=3) == ["d", "a", "b"]


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
        # 避免真跑全市场 3361 只
        monkeypatch.setattr(dashboard, "load_ma_duration_samples", lambda: pd.DataFrame())

    def test_market_overview_no_crash(self):
        dashboard.tab_market_overview()  # 不应抛异常

    def test_stock_query_no_crash(self):
        dashboard.tab_stock_query()

    def test_rankings_no_crash(self):
        dashboard.tab_rankings()

    def test_ma_duration_no_crash(self):
        dashboard.tab_ma_duration()  # 空数据走 st.info 分支，不应抛异常

    def test_data_status_no_crash(self):
        dashboard.tab_data_status()
