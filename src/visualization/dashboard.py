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
from datetime import datetime

from src.visualization.metrics import (
    load_all_latest_day,
    market_breadth,
    equal_weighted_index,
    limit_up_down_series,
    rolling_volatility,
    top_movers,
)


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

    stock_list = sorted(df_latest["code"].unique())
    selected_code = st.selectbox(
        "选择股票代码",
        stock_list,
        format_func=lambda x: f"{x}",
    )

    if selected_code:
        st.info(f"已选择：{selected_code}")

        # 尝试加载 K线数据
        try:
            vol = rolling_volatility(selected_code, DATA_DIR)
            if vol is not None:
                st.success(f"波动率已加载（最新：{vol.iloc[-1]:.2%}）")
            else:
                st.warning("波动率数据不足")
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
        top = top_movers(df_latest, n=10, metric="pctChg", ascending=False)
        st.subheader("涨幅 Top10")
    elif rank_type == "跌幅榜":
        top = top_movers(df_latest, n=10, metric="pctChg", ascending=True)
        st.subheader("跌幅 Top10")
    elif rank_type == "成交额":
        top = top_movers(df_latest, n=10, metric="amount", ascending=False)
        st.subheader("成交额 Top10")
    else:  # 换手率
        top = top_movers(df_latest, n=10, metric="turn", ascending=False)
        st.subheader("换手率 Top10")

    if not top.empty:
        display_cols = ["code", "pctChg", "amount", "turn"]
        display_df = top[[c for c in display_cols if c in top.columns]].copy()
        st.dataframe(display_df, use_container_width=True)
    else:
        st.info("无排行榜数据")


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


if __name__ == "__main__":
    main()
