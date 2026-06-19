"""
A股股价数据可视化看板 — Streamlit 应用

4 个 tabs：大盘概览、个股查询、排行榜、数据状态
"""
import os
import sys
from pathlib import Path

# 修复 Streamlit 的导入问题
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
from streamlit_searchbox import st_searchbox

from src.visualization.metrics import (
    load_all_latest_day,
    load_stock_kline,
    market_breadth,
    equal_weighted_index,
    limit_up_down_series,
    rolling_volatility,
    top_movers,
)

# 颜色常量（遵循 DESIGN.md）
COLOR_UP = "#16a764"     # 上涨绿
COLOR_DOWN = "#cc3a21"   # 下跌红
COLOR_VOLUME = "#dbeafe" # 成交量副图浅蓝

# 搜索框单次最多展示的候选数量（避免下拉过长）
SEARCH_MAX_RESULTS = 50

# 排行榜榜单基数（表内关键词筛选在此范围内进行）
RANK_TOP_N = 50


# ========== 配置 ==========
DATA_DIR = "股价数据_parquet_fq"
st.set_page_config(
    page_title="A股股价看板",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ========== 缓存函数 ==========
@st.cache_data(ttl=3600)
def load_latest_day():
    """加载最新一日的全市场数据（缓存 1 小时）"""
    try:
        return load_all_latest_day(DATA_DIR)
    except Exception as e:
        st.warning(f"加载数据失败：{e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def load_equal_weighted_index(start_date: str = "2025-01-01"):
    """计算等权指数走势（缓存 1 小时）"""
    try:
        return equal_weighted_index(DATA_DIR, start_date=start_date)
    except Exception as e:
        st.warning(f"计算等权指数失败：{e}")
        return pd.Series()


@st.cache_data(ttl=3600)
def load_limit_up_down():
    """加载涨停/跌停走势（缓存 1 小时）"""
    try:
        return limit_up_down_series(DATA_DIR)
    except Exception as e:
        st.warning(f"加载涨停数据失败：{e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def load_name_map() -> dict:
    """
    加载代码->公司名称映射（缓存 1 小时）。

    映射文件由 stock_price.py 生成并随 HF 同步。
    若文件不存在，返回空字典，看板回退到只显示代码。
    """
    path = Path(DATA_DIR) / "code_name_map.parquet"
    if not path.exists():
        return {}
    try:
        df = pd.read_parquet(path)
        return dict(zip(df["code"], df["code_name"]))
    except Exception:
        return {}


def format_stock_label(code: str, name_map: dict) -> str:
    """格式化股票标签：'公司名称 (代码)'，无名称时只显示代码。"""
    name = name_map.get(code, "")
    return f"{name} ({code})" if name else code


def filter_stocks(codes, name_map: dict, query: str) -> list:
    """
    按代码或公司名称模糊过滤股票（大小写不敏感的子串匹配）。

    同时支持：公司名称搜索、股票代码搜索、模糊（子串）搜索。
    query 为空时返回全部代码。
    """
    q = query.strip().lower()
    if not q:
        return list(codes)
    return [
        code for code in codes
        if q in code.lower() or q in name_map.get(code, "").lower()
    ]


# ========== Tab 1: 大盘概览 ==========
def tab_market_overview():
    """大盘概览 tab：市场宽度、等权指数、涨停/跌停走势"""
    st.header("📊 大盘概览")

    df_latest = load_latest_day()

    if df_latest.empty:
        st.error("无法加载市场数据。请检查数据目录。")
        return

    # 市场宽度指标（3 个卡片）
    st.subheader("市场宽度")
    breadth = market_breadth(df_latest)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("上涨", breadth["up"], delta=None, delta_color="off")
    with col2:
        st.metric("下跌", breadth["down"], delta=None, delta_color="off")
    with col3:
        st.metric("平盘", breadth["flat"], delta=None, delta_color="off")
    with col4:
        ratio_text = f"{breadth['ratio']:.2f}" if breadth['ratio'] != float('inf') else "∞"
        st.metric("涨跌比", ratio_text, delta=None, delta_color="off")

    # 等权指数走势图
    st.subheader("等权指数走势")
    index_series = load_equal_weighted_index()
    if not index_series.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=index_series.index,
            y=index_series.values * 100,  # 转换为百分比显示
            mode="lines",
            name="等权指数",
            line=dict(color="#16a764", width=2),
        ))
        fig.update_layout(
            xaxis_title="日期",
            yaxis_title="累计收益率 (%)",
            template="plotly_white",
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True, config={"responsive": True})
    else:
        st.info("等权指数数据不足")

    # 涨停/跌停走势
    st.subheader("涨停/跌停统计")
    limit_df = load_limit_up_down()
    if not limit_df.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=limit_df["date"],
            y=limit_df["limit_up"],
            mode="lines+markers",
            name="涨停家数",
            line=dict(color="#f59e0b", width=2),
        ))
        fig.add_trace(go.Scatter(
            x=limit_df["date"],
            y=limit_df["limit_down"],
            mode="lines+markers",
            name="跌停家数",
            line=dict(color="#3b82f6", width=2),
        ))
        fig.update_layout(
            xaxis_title="日期",
            yaxis_title="家数",
            template="plotly_white",
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True, config={"responsive": True})
    else:
        st.info("涨停/跌停数据不足")


# ========== Tab 2: 个股查询 ==========
def tab_stock_query():
    """个股查询 tab：搜索、K线、波动率"""
    st.header("📈 个股查询")

    # 构建股票代码列表
    df_latest = load_latest_day()
    if df_latest.empty:
        st.error("无法加载股票列表")
        return

    name_map = load_name_map()
    stock_list = sorted(df_latest["code"].unique())

    def search_stocks(term: str):
        """搜索框回调：随输入实时返回 (显示标签, 代码) 候选列表。"""
        matches = filter_stocks(stock_list, name_map, term)
        return [(format_stock_label(c, name_map), c) for c in matches[:SEARCH_MAX_RESULTS]]

    selected_code = st_searchbox(
        search_stocks,
        placeholder="输入代码或公司名称，如：600015 / 华夏 / sh.600",
        label="搜索股票（支持代码、公司名称、模糊匹配）",
        key="stock_searchbox",
    )

    if not selected_code:
        st.info("请在上方输入关键词并从候选中选择股票")
        return

    st.info(f"已选择：{format_stock_label(selected_code, name_map)}")

    df_k = load_kline_cached(selected_code)
    if df_k.empty:
        st.warning("无法加载该股票的 K线数据")
        return

    # 显示范围选择
    range_map = {"最近 60 日": 60, "最近 120 日": 120, "最近 250 日": 250, "全部": None}
    range_option = st.radio(
        "显示范围", list(range_map.keys()), horizontal=True, index=1
    )
    n = range_map[range_option]
    df_view = df_k.tail(n) if n else df_k

    # 股价指标卡片（基于最新一日）
    latest = df_k.iloc[-1]
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("最新价", f"{latest['close']:.2f}", delta=f"{latest.get('pctChg', 0):.2f}%")
    with col2:
        st.metric("最高 / 最低", f"{latest['high']:.2f} / {latest['low']:.2f}")
    with col3:
        st.metric("成交额", f"{latest.get('amount', 0) / 1e8:.2f} 亿")
    with col4:
        st.metric("换手率", f"{latest.get('turn', 0):.2f}%")

    # K线蜡烛图 + 成交量副图
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03, row_heights=[0.7, 0.3],
        subplot_titles=("K线（前复权）", "成交量"),
    )
    fig.add_trace(
        go.Candlestick(
            x=df_view["date"],
            open=df_view["open"], high=df_view["high"],
            low=df_view["low"], close=df_view["close"],
            increasing_line_color=COLOR_UP,
            decreasing_line_color=COLOR_DOWN,
            name="K线",
        ),
        row=1, col=1,
    )
    # 成交量柱：当日收 >= 开为涨（绿），否则跌（红）
    vol_colors = [
        COLOR_UP if c >= o else COLOR_DOWN
        for o, c in zip(df_view["open"], df_view["close"])
    ]
    fig.add_trace(
        go.Bar(
            x=df_view["date"], y=df_view["volume"],
            marker_color=vol_colors, name="成交量",
        ),
        row=2, col=1,
    )
    fig.update_layout(
        template="plotly_white",
        height=600,
        xaxis_rangeslider_visible=False,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    # 20 日滚动年化波动率曲线
    try:
        vol = rolling_volatility(selected_code, DATA_DIR)
        vol_view = vol.tail(n) if n else vol
        date_view = df_k["date"].tail(n) if n else df_k["date"]
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Scatter(
            x=date_view, y=vol_view * 100,
            mode="lines", name="20日年化波动率",
            line=dict(color="#6d9eeb", width=2),
        ))
        fig_vol.update_layout(
            title="20 日滚动年化波动率",
            xaxis_title="日期", yaxis_title="波动率 (%)",
            template="plotly_white", height=300,
        )
        st.plotly_chart(fig_vol, use_container_width=True)
    except Exception as e:
        st.warning(f"无法加载波动率：{e}")


# ========== Tab 3: 排行榜 ==========
def tab_rankings():
    """排行榜 tab：涨幅/跌幅/成交额"""
    st.header("🏆 排行榜")

    df_latest = load_latest_day()
    if df_latest.empty:
        st.error("无法加载排行榜数据")
        return

    rank_type = st.selectbox(
        "选择排行类型",
        ["涨幅榜", "跌幅榜", "成交额", "换手率"],
    )

    if rank_type == "涨幅榜":
        top = top_movers(df_latest, n=RANK_TOP_N, metric="pctChg", ascending=False)
        st.subheader(f"涨幅 Top{RANK_TOP_N}")
    elif rank_type == "跌幅榜":
        top = top_movers(df_latest, n=RANK_TOP_N, metric="pctChg", ascending=True)
        st.subheader(f"跌幅 Top{RANK_TOP_N}")
    elif rank_type == "成交额":
        top = top_movers(df_latest, n=RANK_TOP_N, metric="amount", ascending=False)
        st.subheader(f"成交额 Top{RANK_TOP_N}")
    else:  # 换手率
        top = top_movers(df_latest, n=RANK_TOP_N, metric="turn", ascending=False)
        st.subheader(f"换手率 Top{RANK_TOP_N}")

    if top.empty:
        st.info("无排行榜数据")
        return

    name_map = load_name_map()
    display_cols = ["code", "close", "pctChg", "amount", "turn"]
    display_df = top[[c for c in display_cols if c in top.columns]].copy()
    # 最新价保留 2 位小数（遵循 DESIGN.md 价格精度约定）
    if "close" in display_df.columns:
        display_df["close"] = display_df["close"].round(2)
    # 在代码列旁插入公司名称列
    display_df.insert(1, "名称", display_df["code"].map(lambda c: name_map.get(c, "")))

    # 表内关键词筛选：按代码或公司名称模糊过滤当前榜单（复用 filter_stocks）
    rank_query = st.text_input(
        "筛选榜单（输入代码或公司名称，支持模糊匹配）",
        placeholder="如：银行 / 600015",
        key="rank_filter",
    )
    matched_codes = set(filter_stocks(display_df["code"].tolist(), name_map, rank_query))
    display_df = display_df[display_df["code"].isin(matched_codes)]

    if display_df.empty:
        st.info("当前榜单中没有匹配的股票")
        return

    if rank_query.strip():
        st.caption(f"榜单内匹配到 {len(display_df)} 只股票")

    display_df = display_df.rename(columns={
        "code": "代码",
        "close": "最新价",
        "pctChg": "涨跌幅(%)",
        "amount": "成交额",
        "turn": "换手率(%)",
    })
    st.dataframe(display_df, use_container_width=True, hide_index=True)


# ========== Tab 4: 数据状态 ==========
def tab_data_status():
    """数据新鲜度、覆盖范围"""
    st.header("📋 数据状态")

    df_latest = load_latest_day()

    if df_latest.empty:
        st.warning("无可用数据")
        return

    # 数据统计
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("覆盖股票数", len(df_latest))

    with col2:
        latest_date = df_latest["date"].max()
        st.metric("数据日期", str(latest_date)[:10])

    with col3:
        # 计算"距今"的时间差
        try:
            latest_dt = pd.to_datetime(latest_date)
            now = pd.Timestamp.now()
            days_ago = (now - latest_dt).days
            st.metric("距今", f"{days_ago} 天前")
        except:
            st.metric("距今", "—")

    # 数据新鲜度警告
    try:
        latest_dt = pd.to_datetime(df_latest["date"].max())
        now = pd.Timestamp.now()
        days_diff = (now - latest_dt).days
        if days_diff > 2:
            st.warning(f"⚠️ 数据已过期（{days_diff} 天未更新），请等待数据同步")
    except:
        pass

    st.divider()
    st.subheader("数据源信息")
    st.info("""
    - **数据来源**：Hugging Face Dataset `Charon107/stock-price`
    - **数据类型**：前复权日线行情（3300+ 沪深主板股票）
    - **更新频率**：工作日自动更新（北京时间 09:00）
    """)


# ========== 主应用 ==========
def main():
    st.title("📊 A股股价数据看板")

    # 4 个 Tab
    tab1, tab2, tab3, tab4 = st.tabs(["大盘概览", "个股查询", "排行榜", "数据状态"])

    with tab1:
        tab_market_overview()

    with tab2:
        tab_stock_query()

    with tab3:
        tab_rankings()

    with tab4:
        tab_data_status()


@st.cache_data(ttl=3600)
def load_kline_cached(code):
    from src.visualization.metrics import load_stock_kline
    try:
        return load_stock_kline(code, DATA_DIR)
    except Exception:
        import pandas as _pd
        return _pd.DataFrame()

if __name__ == "__main__":
    main()
