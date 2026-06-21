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
from src.visualization.ui_theme import (
    COLORS,
    MA_COLORS,
    inject_global_css,
    app_header,
    badge,
    kpi_card,
    apply_chart_theme,
    add_range_selector,
    style_ranking,
)

# 颜色常量（单一来源见 ui_theme.COLORS / DESIGN.md，深色终端主题）
COLOR_UP = COLORS["up"]       # 上涨绿
COLOR_DOWN = COLORS["down"]   # 下跌红
COLOR_VOLUME = COLORS["volume"]  # 成交量副图

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
    initial_sidebar_state="expanded",
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
def load_ma_duration_samples():
    """加载 MA5>MA10 金叉区间样本（全市场重算，缓存 1 小时）"""
    try:
        from src.analysis.ma5_above_ma10_duration import compute_duration_samples
        return compute_duration_samples(DATA_DIR)
    except Exception as e:
        st.warning(f"计算 MA 多头时长失败：{e}")
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


# 起始范围选项 → start_date 映射
_RANGE_OPTIONS = ["近3月", "近6月", "今年至今", "全部"]


def _start_date_from_option(option: str) -> str:
    """把起始范围选项转换为 YYYY-MM-DD 起始日期。"""
    now = pd.Timestamp.now()
    return {
        "近3月": (now - pd.Timedelta(days=90)).strftime("%Y-%m-%d"),
        "近6月": (now - pd.Timedelta(days=180)).strftime("%Y-%m-%d"),
        "今年至今": f"{now.year}-01-01",
        "全部": "2007-01-01",
    }.get(option, f"{now.year}-01-01")


def _nontrading_breaks(dates: pd.Series) -> list:
    """计算需要在 K线 x 轴隐藏的非交易日（周末 + 节假日），消除蜡烛间空档。"""
    if dates.empty:
        return [dict(bounds=["sat", "mon"])]
    full_bdays = pd.bdate_range(dates.min(), dates.max())  # 工作日全集
    present = pd.DatetimeIndex(dates.unique())
    holidays = full_bdays.difference(present)              # 工作日中缺失的 = 节假日/停牌
    breaks = [dict(bounds=["sat", "mon"])]                 # 周末
    if len(holidays) > 0:
        breaks.append(dict(values=holidays))
    return breaks


def build_kline_fig(
    df: pd.DataFrame,
    *,
    n: int | None = None,
    height: int = 600,
    ma_periods=(5, 10, 20, 60),
):
    """
    构造专业 K线（蜡烛 + 均线 + 成交量）深色主题 Figure。供个股查询与排行榜预览复用。

    参数：
        df: 完整 K线 DataFrame（含 date/open/high/low/close/volume，可选 pctChg）。
            均线在完整序列上计算后再裁剪，避免窗口开头缺失。
        n: 仅展示最近 n 个交易日（None=全部）。
        ma_periods: 叠加的均线周期。
    """
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    # 在完整序列上算均线（价格 + 量），再裁剪显示窗口
    for p in ma_periods:
        d[f"MA{p}"] = d["close"].rolling(p).mean()
    d["VMA5"] = d["volume"].rolling(5).mean()
    view = d.tail(n) if n else d

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03, row_heights=[0.74, 0.26],
    )

    # 价格均线（置于蜡烛之下先画，图例可点击开关）
    for p in ma_periods:
        col = f"MA{p}"
        if view[col].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=view["date"], y=view[col],
                    mode="lines", name=f"MA{p}",
                    line=dict(color=MA_COLORS.get(p, COLORS["text_secondary"]), width=1.2),
                    hovertemplate=f"MA{p} %{{y:.2f}}<extra></extra>",
                ),
                row=1, col=1,
            )

    # 蜡烛图（涨绿/跌红；A股惯例阳线空心、阴线实心，形状冗余助色盲辨识）
    fig.add_trace(
        go.Candlestick(
            x=view["date"],
            open=view["open"], high=view["high"],
            low=view["low"], close=view["close"],
            increasing=dict(line=dict(color=COLOR_UP), fillcolor="rgba(0,0,0,0)"),
            decreasing=dict(line=dict(color=COLOR_DOWN), fillcolor=COLOR_DOWN),
            name="K线", showlegend=False,
        ),
        row=1, col=1,
    )

    # 成交量（万股，半透明降权，从属于价格）
    vol_wan = view["volume"] / 1e4
    vol_colors = [
        COLOR_UP if c >= o else COLOR_DOWN
        for o, c in zip(view["open"], view["close"])
    ]
    fig.add_trace(
        go.Bar(
            x=view["date"], y=vol_wan,
            marker_color=vol_colors, marker_line_width=0, opacity=0.5,
            name="成交量", showlegend=False,
            hovertemplate="量 %{y:.0f} 万股<extra></extra>",
        ),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=view["date"], y=view["VMA5"] / 1e4,
            mode="lines", name="量MA5",
            line=dict(color=COLORS["text_secondary"], width=1),
            showlegend=False, hovertemplate="量MA5 %{y:.0f} 万股<extra></extra>",
        ),
        row=2, col=1,
    )

    # 最新价参考线 + 右侧价签（按当日涨跌着色）
    last = view.iloc[-1]
    last_pct = float(last.get("pctChg", 0) or 0)
    tag_color = COLOR_UP if last_pct > 0 else COLOR_DOWN if last_pct < 0 else COLORS["text_secondary"]
    fig.add_hline(
        y=float(last["close"]), line=dict(color=tag_color, width=1, dash="dash"),
        annotation_text=f"{last['close']:.2f}", annotation_position="top right",
        annotation_font=dict(color="#020617", size=12, family="Fira Code, monospace"),
        annotation_bgcolor=tag_color, annotation_bordercolor=tag_color,
        row=1, col=1,
    )

    fig.update_layout(xaxis_rangeslider_visible=False)
    fig = apply_chart_theme(fig, height=height, show_legend=True)
    # 去除非交易日空档；价格/成交量轴标题与右置
    breaks = _nontrading_breaks(view["date"])
    fig.update_xaxes(rangebreaks=breaks)
    fig.update_yaxes(title_text="价格", side="right", row=1, col=1)
    fig.update_yaxes(title_text="成交量(万股)", side="right", row=2, col=1)
    return fig


# ========== Tab 1: 大盘概览 ==========
def tab_market_overview():
    """大盘概览 tab：市场宽度、等权指数、涨停/跌停走势"""
    st.header("大盘概览")

    df_latest = load_latest_day()

    if df_latest.empty:
        st.error("无法加载市场数据。请检查数据目录或点击侧边栏「刷新数据」。")
        return

    # 市场宽度指标（4 个 KPI 卡片）
    st.subheader("市场宽度")
    breadth = market_breadth(df_latest)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_card("上涨家数", f"{breadth['up']:,}", tone="up")
    with col2:
        kpi_card("下跌家数", f"{breadth['down']:,}", tone="down")
    with col3:
        kpi_card("平盘家数", f"{breadth['flat']:,}", tone="neutral")
    with col4:
        ratio_text = f"{breadth['ratio']:.2f}" if breadth["ratio"] != float("inf") else "∞"
        kpi_card("涨跌比", ratio_text, tone="primary")

    # 起始范围选择（控制等权指数与涨停/跌停的起点）
    st.write("")
    range_option = st.selectbox(
        "起始范围", _RANGE_OPTIONS, index=2, key="overview_range"
    )
    start_date = _start_date_from_option(range_option)

    # 等权指数走势图
    st.subheader("等权指数走势")
    with st.spinner("计算等权指数中…"):
        index_series = load_equal_weighted_index(start_date=start_date)
    if not index_series.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(index_series.index),
            y=index_series.values * 100,  # 转换为百分比显示
            mode="lines",
            name="等权指数",
            line=dict(color=COLORS["accent"], width=2),
            fill="tozeroy",
            fillcolor="rgba(34,197,94,0.08)",
        ))
        fig.update_yaxes(title_text="累计收益率 (%)")
        fig = apply_chart_theme(fig, height=400)
        fig = add_range_selector(fig)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    else:
        st.info("等权指数数据不足")

    # 涨停/跌停走势（镜像柱状图：涨停向上、跌停向下，0 为中轴）
    st.subheader("涨停 / 跌停统计")
    with st.spinner("统计涨跌停中…"):
        limit_df = load_limit_up_down()
    # 响应上方「起始范围」下拉框；过滤置于 not empty 守卫内，避免空 DataFrame 无 date 列报错
    if not limit_df.empty:
        mask = pd.to_datetime(limit_df["date"]) >= pd.Timestamp(start_date)
        limit_df = limit_df[mask]
    if not limit_df.empty:
        x_dates = pd.to_datetime(limit_df["date"])
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=x_dates, y=limit_df["limit_up"], name="涨停家数",
            marker_color=COLORS["warn"], marker_line_width=0,
            hovertemplate="%{y} 家<extra>涨停家数</extra>",
        ))
        fig.add_trace(go.Bar(
            x=x_dates, y=-limit_df["limit_down"], name="跌停家数",
            marker_color=COLORS["info"], marker_line_width=0,
            customdata=limit_df["limit_down"],
            hovertemplate="%{customdata} 家<extra>跌停家数</extra>",
        ))
        fig.update_yaxes(title_text="家数")
        fig.update_layout(barmode="overlay", bargap=0.15)
        fig = apply_chart_theme(fig, height=400, show_legend=True)
        fig = add_range_selector(fig)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    else:
        st.info("涨停/跌停数据不足")


# ========== Tab: 多头时长 (MA5>MA10) ==========
def tab_ma_duration():
    """多头时长 tab：全市场 MA5>MA10 金叉区间的持续时长分布（KPI + 直方图）。"""
    st.header("多头时长")
    st.caption(
        "口径：按收盘价算 MA5/MA10，每段 MA5 > MA10 的连续交易日为一个样本，"
        "上穿日 ≥ 2025-01-01；「未结束」= 截至最新交易日 MA5 仍 > MA10（时长为下限）。"
    )

    with st.spinner("统计全市场 MA 多头时长中…（首次约需数十秒）"):
        detail = load_ma_duration_samples()

    if detail.empty:
        st.info("数据不足")
        return

    dur = detail["duration"]
    n_total = len(detail)
    n_ongoing = int(detail["ongoing"].sum())
    median = dur.median()
    p90 = dur.quantile(0.90)
    max_d = int(dur.max())

    # KPI 卡
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        kpi_card("样本总数", f"{n_total:,}", tone="primary")
    with c2:
        kpi_card("中位时长", f"{median:.0f} 天", tone="neutral")
    with c3:
        kpi_card("P90 时长", f"{p90:.0f} 天", tone="neutral")
    with c4:
        kpi_card("最长", f"{max_d} 天", tone="neutral")
    with c5:
        kpi_card("未结束", f"{n_ongoing:,}", tone="warn")

    st.write("")
    log_y = st.toggle("对数 Y 轴", value=False, key="ma_dur_logy")

    # 按整数天数统计 已结束 / 未结束 计数（堆叠）
    bins = range(1, max_d + 1)
    closed_counts = detail.loc[~detail["ongoing"], "duration"].value_counts().reindex(bins, fill_value=0)
    ongoing_counts = detail.loc[detail["ongoing"], "duration"].value_counts().reindex(bins, fill_value=0)
    x = list(bins)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=closed_counts.values, name="已结束",
        marker_color=COLORS["accent"], marker_line_width=0,
        hovertemplate="%{x} 天<br>已结束 %{y} 个<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=x, y=ongoing_counts.values, name="未结束",
        marker_color=COLORS["warn"], marker_line_width=0,
        hovertemplate="%{x} 天<br>未结束 %{y} 个<extra></extra>",
    ))
    fig.update_layout(barmode="stack", bargap=0.1)
    fig.update_xaxes(title_text="持续交易日数")
    fig.update_yaxes(title_text="样本数")
    if log_y:
        fig.update_yaxes(type="log")
    # 中位数 / P90 参考线
    fig.add_vline(
        x=median, line=dict(color=MA_COLORS[20], width=1.2, dash="dash"),
        annotation_text=f"中位 {median:.0f}", annotation_position="top",
        annotation_font_color=COLORS["text_secondary"],
    )
    fig.add_vline(
        x=p90, line=dict(color=MA_COLORS[60], width=1.2, dash="dot"),
        annotation_text=f"P90 {p90:.0f}", annotation_position="top",
        annotation_font_color=COLORS["text_secondary"],
    )
    fig = apply_chart_theme(fig, height=420, show_legend=True)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ========== Tab 2: 个股查询 ==========
def tab_stock_query():
    """个股查询 tab：搜索、K线、波动率"""
    st.header("个股查询")

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

    st.markdown(
        badge(f"已选择 · {format_stock_label(selected_code, name_map)}", "ok"),
        unsafe_allow_html=True,
    )

    with st.spinner("加载 K线数据中…"):
        df_k = load_kline_cached(selected_code)
    if df_k.empty:
        st.warning("无法加载该股票的 K线数据")
        return

    # 显示范围选择（加 key 以跨重跑持久化）
    range_map = {"最近 60 日": 60, "最近 120 日": 120, "最近 250 日": 250, "全部": None}
    range_option = st.radio(
        "显示范围", list(range_map.keys()), horizontal=True, index=1, key="kline_range"
    )
    n = range_map[range_option]

    # 股价指标卡片（基于最新一日）
    latest = df_k.iloc[-1]
    pct = float(latest.get("pctChg", 0) or 0)
    pct_tone = "up" if pct > 0 else "down" if pct < 0 else "neutral"
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_card(
            "最新价", f"{latest['close']:.2f}",
            delta=f"{pct:+.2f}%", delta_tone=pct_tone, tone="neutral",
        )
    with col2:
        kpi_card("最高 / 最低", f"{latest['high']:.2f} / {latest['low']:.2f}", tone="neutral")
    with col3:
        kpi_card("成交额", f"{latest.get('amount', 0) / 1e8:.2f} 亿", tone="neutral")
    with col4:
        kpi_card("换手率", f"{latest.get('turn', 0):.2f}%", tone="neutral")

    # K线蜡烛图 + 均线 + 成交量副图
    fig = build_kline_fig(df_k, n=n, height=600)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    # 20 日滚动年化波动率曲线
    try:
        vol = rolling_volatility(selected_code, DATA_DIR)
        vol_view = vol.tail(n) if n else vol
        date_view = df_k["date"].tail(n) if n else df_k["date"]
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Scatter(
            x=pd.to_datetime(date_view), y=vol_view * 100,
            mode="lines", name="20日年化波动率",
            line=dict(color=COLORS["info"], width=2),
            fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
        ))
        fig_vol.update_yaxes(title_text="波动率 (%)")
        fig_vol = apply_chart_theme(fig_vol, height=300)
        st.plotly_chart(fig_vol, width="stretch", config={"displayModeBar": False})
    except Exception as e:
        st.warning(f"无法加载波动率：{e}")


# ========== Tab 3: 排行榜 ==========
def tab_rankings():
    """排行榜 tab：涨幅/跌幅/成交额"""
    st.header("排行榜")

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
    }).reset_index(drop=True)

    st.caption("点击任意行查看该股 K线速览")
    event = st.dataframe(
        style_ranking(display_df),
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="rank_table",
    )

    # 行选择 → 内联渲染该股 K线（避免 Streamlit 无法程序化切 tab）
    sel_rows = []
    if event is not None and getattr(event, "selection", None):
        sel_rows = event.selection.get("rows", []) if isinstance(event.selection, dict) else event.selection.rows
    if sel_rows:
        sel_code = str(display_df.iloc[sel_rows[0]]["代码"])
        with st.expander(f"K线速览 · {format_stock_label(sel_code, name_map)}", expanded=True):
            with st.spinner("加载 K线中…"):
                df_k = load_kline_cached(sel_code)
            if df_k.empty:
                st.warning("无法加载该股票的 K线数据")
            else:
                fig = build_kline_fig(df_k, n=120, height=420)
                st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ========== Tab 4: 数据状态 ==========
def tab_data_status():
    """数据新鲜度、覆盖范围"""
    st.header("数据状态")

    df_latest = load_latest_day()

    if df_latest.empty:
        st.warning("无可用数据")
        return

    latest_date, days_ago = data_freshness(df_latest)

    # 数据统计（KPI 卡片）
    col1, col2, col3 = st.columns(3)
    with col1:
        kpi_card("覆盖股票数", f"{len(df_latest):,}", tone="primary")
    with col2:
        kpi_card("数据日期", latest_date, tone="neutral")
    with col3:
        if days_ago is None:
            kpi_card("距今", "—", tone="neutral")
        else:
            kpi_card(
                "距今", f"{days_ago} 天",
                tone="down" if days_ago > 2 else "neutral",
            )

    # 数据新鲜度警告
    if days_ago is not None and days_ago > 2:
        st.warning(f"数据已过期（{days_ago} 天未更新），请等待数据同步或点击侧边栏「刷新数据」")

    st.divider()
    st.subheader("数据源信息")
    st.info("""
    - **数据来源**：Hugging Face Dataset `Charon107/stock-price`
    - **数据类型**：前复权日线行情（3300+ 沪深主板股票）
    - **更新频率**：工作日自动更新（北京时间 09:00）
    """)


# ========== 数据新鲜度（页头/侧边栏/数据状态共用） ==========
def data_freshness(df_latest: pd.DataFrame):
    """返回 (最新日期字符串, 距今天数)；无数据或解析失败时距今为 None。"""
    if df_latest.empty or "date" not in df_latest.columns:
        return "—", None
    latest_date = str(df_latest["date"].max())[:10]
    try:
        days_ago = (pd.Timestamp.now() - pd.to_datetime(latest_date)).days
    except Exception:
        days_ago = None
    return latest_date, days_ago


def render_header(df_latest: pd.DataFrame) -> None:
    """渲染页头：标题 + 副标题 + 右侧数据日期/新鲜度徽章。"""
    latest_date, days_ago = data_freshness(df_latest)
    if days_ago is None:
        right = badge("暂无数据", "warn")
    elif days_ago > 2:
        right = badge(f"{latest_date} · {days_ago} 天前", "warn")
    else:
        right = badge(f"{latest_date} · {days_ago} 天前", "ok")
    app_header(
        "A股股价数据看板",
        "前复权日线 · 微信指数量化研究",
        right_html=right,
    )


def render_sidebar(df_latest: pd.DataFrame) -> None:
    """常驻侧边栏：数据日期、覆盖股票数、新鲜度提示、刷新按钮。"""
    latest_date, days_ago = data_freshness(df_latest)
    with st.sidebar:
        st.markdown("### 数据状态")
        kpi_card("数据日期", latest_date, tone="neutral")
        st.write("")
        kpi_card("覆盖股票", f"{len(df_latest):,}", tone="primary")
        st.write("")
        if days_ago is not None and days_ago > 2:
            st.markdown(badge(f"已过期 {days_ago} 天", "warn"), unsafe_allow_html=True)
        elif days_ago is not None:
            st.markdown(badge(f"距今 {days_ago} 天", "ok"), unsafe_allow_html=True)
        st.divider()
        if st.button("刷新数据", icon=":material/refresh:", width="stretch"):
            st.cache_data.clear()
            st.rerun()
        st.caption("数据源：HF `Charon107/stock-price`")


# ========== 主应用 ==========
def main():
    inject_global_css()

    df_latest = load_latest_day()
    render_header(df_latest)
    render_sidebar(df_latest)

    # 5 个 Tab
    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["大盘概览", "个股查询", "排行榜", "多头时长", "数据状态"]
    )

    with tab1:
        tab_market_overview()

    with tab2:
        tab_stock_query()

    with tab3:
        tab_rankings()

    with tab4:
        tab_ma_duration()

    with tab5:
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
