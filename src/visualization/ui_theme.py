"""
A股股价看板 — 深色终端风视觉层（样式 token + CSS + 渲染辅助）

集中所有视觉逻辑，让 dashboard.py 专注于数据与布局：
- COLORS：唯一颜色来源（与 .streamlit/config.toml、DESIGN.md 一致）
- inject_global_css：一次性注入字体/卡片/tab/滚动条等全局样式
- kpi_card / app_header / badge：自定义 HTML 组件
- apply_chart_theme / add_range_selector：统一 Plotly 深色主题（DRY）
- style_ranking：排行榜红绿涨跌 + 单元格条形（pandas Styler）
"""
from __future__ import annotations

import streamlit as st
import pandas as pd

# ========== 颜色 token（深色 OLED 终端，单一来源） ==========
COLORS = {
    "bg": "#020617",            # 近黑背景
    "panel": "#0F172A",         # 面板/卡片
    "panel_alt": "#1E293B",     # 次级面板
    "grid": "#1E293B",          # 图表网格线
    "border": "#334155",        # 边框
    "border_hover": "#475569",  # 边框 hover
    "text": "#F8FAFC",          # 主文本
    "text_secondary": "#94A3B8",# 次文本/中性
    "up": "#22C55E",            # 上涨绿
    "down": "#EF4444",          # 下跌红
    "accent": "#22C55E",        # 主色（=上涨绿）
    "warn": "#FBBF24",          # 涨停黄/警示
    "info": "#3B82F6",          # 跌停蓝
    "volume": "#1E3A5F",        # 成交量副图
}

# 均线色板（避开红/绿，防止与涨跌混淆；按周期递进区分）
MA_COLORS = {
    5: "#E2E8F0",   # 浅灰白
    10: "#FBBF24",  # 琥珀
    20: "#38BDF8",  # 天蓝
    60: "#C084FC",  # 紫
}

_FONT_SANS = "'Fira Sans', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"
_FONT_MONO = "'Fira Code', 'IBM Plex Mono', monospace"

# 涨/跌方向符号（不只靠颜色，满足 a11y color-not-only）
_ARROW = {"up": "▲", "down": "▼"}


# ========== 全局 CSS ==========
def inject_global_css() -> None:
    """注入全局深色样式（字体、卡片、tab、滚动条、减少动效）。在 main() 开头调用一次。"""
    c = COLORS
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap');

        html, body, [class*="css"], .stApp {{ font-family: {_FONT_SANS}; }}

        /* 顶部留白：留足空间避免标题被 Streamlit 顶栏裁切 */
        .block-container {{ padding-top: 3.6rem; padding-bottom: 3rem; }}
        /* 顶栏透明，不遮挡下方内容 */
        [data-testid="stHeader"] {{ background: transparent; }}

        /* ===== 页头 ===== */
        .app-header {{
            display: flex; align-items: flex-end; justify-content: space-between;
            gap: 16px; padding: 0 0 14px; margin-bottom: 18px;
            border-bottom: 1px solid {c['border']};
        }}
        .app-header .title {{
            font-size: 26px; font-weight: 700; color: {c['text']}; letter-spacing: .5px;
            display: flex; align-items: center; gap: 10px;
        }}
        .app-header .title::before {{
            content: ""; width: 6px; height: 26px; border-radius: 3px;
            background: {c['accent']}; display: inline-block;
        }}
        .app-header .subtitle {{ font-size: 13px; color: {c['text_secondary']}; margin-top: 4px; }}

        /* ===== KPI 卡片 ===== */
        .kpi-card {{
            background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px;
            padding: 14px 16px; transition: border-color .2s ease, transform .2s ease;
        }}
        .kpi-card:hover {{ border-color: {c['border_hover']}; transform: translateY(-1px); }}
        .kpi-label {{ font-size: 13px; color: {c['text_secondary']}; font-weight: 500; margin-bottom: 6px; }}
        .kpi-value {{ font-size: 30px; font-weight: 700; font-family: {_FONT_MONO}; line-height: 1.1; }}
        .kpi-value.up {{ color: {c['up']}; }}
        .kpi-value.down {{ color: {c['down']}; }}
        .kpi-value.neutral {{ color: {c['text']}; }}
        .kpi-value.primary {{ color: {c['accent']}; }}
        .kpi-value.warn {{ color: {c['warn']}; }}
        .kpi-arrow {{ font-size: .72em; margin-right: 5px; vertical-align: 2px; }}
        .kpi-delta {{ font-size: 13px; font-family: {_FONT_MONO}; margin-top: 6px; }}
        .kpi-delta.up {{ color: {c['up']}; }}
        .kpi-delta.down {{ color: {c['down']}; }}
        .kpi-delta.neutral {{ color: {c['text_secondary']}; }}

        /* ===== 徽章 ===== */
        .badge {{
            display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px;
            border-radius: 6px; font-size: 12px; font-family: {_FONT_MONO};
            border: 1px solid {c['border']}; background: {c['panel']}; color: {c['text_secondary']};
            white-space: nowrap;
        }}
        .badge.ok {{ color: {c['up']}; border-color: rgba(34,197,94,.4); background: rgba(34,197,94,.08); }}
        .badge.warn {{ color: {c['down']}; border-color: rgba(239,68,68,.4); background: rgba(239,68,68,.08); }}

        /* ===== Tabs ===== */
        .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {c['border']}; }}
        .stTabs [data-baseweb="tab"] {{
            height: 44px; padding: 0 18px; color: {c['text_secondary']};
            font-weight: 500; transition: color .2s ease, background-color .2s ease;
            border-radius: 8px 8px 0 0;
        }}
        .stTabs [data-baseweb="tab"]:hover {{ color: {c['text']}; background: {c['panel']}; }}
        .stTabs [aria-selected="true"] {{ color: {c['accent']} !important; }}
        .stTabs [data-baseweb="tab-highlight"] {{ background-color: {c['accent']}; height: 3px; }}

        /* ===== 顶部导航（segmented_control 仿 tab 观感） ===== */
        [data-testid="stSegmentedControl"] {{
            margin-bottom: 14px; border-bottom: 1px solid {c['border']};
        }}
        [data-testid="stSegmentedControl"] > div {{ gap: 4px; }}
        [data-testid="stSegmentedControl"] button {{
            background: transparent !important; border: none !important;
            color: {c['text_secondary']} !important; font-weight: 500; height: 42px;
            border-radius: 8px 8px 0 0; transition: color .2s ease, background-color .2s ease;
        }}
        [data-testid="stSegmentedControl"] button:hover {{
            color: {c['text']} !important; background: {c['panel']} !important;
        }}
        [data-testid="stSegmentedControl"] button[aria-checked="true"],
        [data-testid="stSegmentedControl"] button[kind="segmented_controlActive"] {{
            color: {c['accent']} !important; background: transparent !important;
            box-shadow: inset 0 -3px 0 {c['accent']};
        }}

        /* ===== 标题 ===== */
        h1, h2, h3 {{ color: {c['text']}; letter-spacing: .3px; }}

        /* ===== 滚动条 ===== */
        ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
        ::-webkit-scrollbar-track {{ background: {c['bg']}; }}
        ::-webkit-scrollbar-thumb {{ background: {c['border']}; border-radius: 5px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: {c['border_hover']}; }}

        /* ===== 按钮 ===== */
        .stButton > button {{ transition: all .2s ease; border-radius: 8px; }}

        /* 等宽数字工具类 */
        .mono {{ font-family: {_FONT_MONO}; }}

        /* 尊重 prefers-reduced-motion */
        @media (prefers-reduced-motion: reduce) {{
            * {{ transition: none !important; animation: none !important; }}
        }}

        /* ===== 响应式：平板 (768-1199px) ===== */
        @media (max-width: 1199px) {{
            .kpi-value {{ font-size: 24px; }}
            .app-header .title {{ font-size: 22px; }}
            .app-header {{ flex-direction: column; align-items: flex-start; gap: 8px; }}
            [data-testid="stSegmentedControl"] {{
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch;
            }}
            [data-testid="stSegmentedControl"] > div {{
                flex-wrap: nowrap !important;
            }}
            [data-testid="stSegmentedControl"] button {{
                padding: 0 10px !important; font-size: 13px;
                flex-shrink: 0 !important; white-space: nowrap !important;
            }}
        }}

        /* ===== 响应式：手机 (<768px) ===== */
        @media (max-width: 767px) {{
            /* Streamlit 列纵向堆叠 */
            [data-testid="stHorizontalBlock"] {{
                flex-wrap: wrap !important; gap: 8px !important;
            }}
            [data-testid="stHorizontalBlock"] > [data-testid="column"] {{
                flex: 0 0 100% !important; min-width: 100% !important;
            }}

            /* KPI 卡片紧凑化 */
            .kpi-card {{ padding: 10px 12px; border-radius: 8px; }}
            .kpi-value {{ font-size: 20px; }}
            .kpi-label {{ font-size: 11px; }}
            .kpi-delta {{ font-size: 11px; }}

            /* Header 缩小 */
            .app-header {{ padding: 0 0 10px; margin-bottom: 10px; }}
            .app-header .title {{ font-size: 18px; }}
            .app-header .title::before {{ width: 4px; height: 18px; }}
            .app-header .subtitle {{ font-size: 11px; }}

            /* 导航：横向滑动 */
            [data-testid="stSegmentedControl"] {{
                margin-bottom: 8px;
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch;
            }}
            [data-testid="stSegmentedControl"] > div {{
                flex-wrap: nowrap !important;
            }}
            [data-testid="stSegmentedControl"] button {{
                padding: 0 10px !important; font-size: 12px; height: 36px;
                flex-shrink: 0 !important; white-space: nowrap !important;
            }}

            /* Badge 缩小 */
            .badge {{ font-size: 10px; padding: 2px 6px; }}

            /* 表格横向滚动 */
            [data-testid="stDataFrame"] {{ overflow-x: auto !important; }}

            /* 减小间距 */
            .block-container {{ padding-top: 1.5rem; padding-bottom: 1.5rem; }}

            /* 侧边栏 overlay（不挤占主内容） */
            [data-testid="stSidebar"] {{
                position: fixed !important;
                top: 0; left: 0; height: 100vh; width: 85vw !important;
                z-index: 999;
            }}
            /* 侧边栏展开时主内容区不位移 */
            [data-testid="stAppViewContainer"] {{
                margin-left: 0 !important;
            }}
            /* 确保关闭按钮可见 */
            [data-testid="stSidebarCollapseButton"] button {{
                background: {c['panel']} !important;
                border: 1px solid {c['border']} !important;
                border-radius: 8px;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ========== HTML 组件 ==========
def app_header(title: str, subtitle: str = "", right_html: str = "") -> None:
    """渲染页头：标题 + 副标题 + 右侧自定义 HTML（如数据徽章）。"""
    st.markdown(
        f"""
        <div class="app-header">
          <div>
            <div class="title">{title}</div>
            <div class="subtitle">{subtitle}</div>
          </div>
          <div style="text-align:right">{right_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def badge(text: str, tone: str = "muted") -> str:
    """返回徽章 HTML 字符串（tone: ok / warn / muted）。供嵌入 header 或侧边栏。"""
    cls = tone if tone in ("ok", "warn") else ""
    return f'<span class="badge {cls}">{text}</span>'


def kpi_card(
    label: str,
    value: str,
    *,
    delta: str | None = None,
    delta_tone: str = "neutral",
    tone: str = "neutral",
) -> None:
    """
    渲染 KPI 卡片：语义色数字 + 可选涨跌幅 + ▲/▼ 符号。

    参数：
        label: 指标名（如 "上涨家数"）
        value: 已格式化的主数值字符串（如 "2,503"）
        delta: 可选的变化值字符串（如 "+1.23%"）
        delta_tone: delta 配色与箭头方向 up/down/neutral
        tone: 主数值配色 up/down/neutral/primary/warn（up/down 会在数值前加 ▲/▼）
    """
    arrow = f'<span class="kpi-arrow">{_ARROW[tone]}</span>' if tone in _ARROW else ""
    delta_html = ""
    if delta is not None:
        d_arrow = f'<span class="kpi-arrow">{_ARROW[delta_tone]}</span>' if delta_tone in _ARROW else ""
        delta_html = f'<div class="kpi-delta {delta_tone}">{d_arrow}{delta}</div>'

    st.markdown(
        f"""
        <div class="kpi-card">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value {tone}">{arrow}{value}</div>
          {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ========== Plotly 深色主题 ==========
def apply_chart_theme(fig, *, height: int = 400, show_legend: bool = False, hover: str = "x unified"):
    """统一应用深色 Plotly 主题：透明背景、Fira 字体、深色网格、十字光标、紧凑边距。返回 fig。"""
    c = COLORS
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=_FONT_SANS, color=c["text"], size=12),
        margin=dict(l=10, r=12, t=46, b=10),
        hovermode=hover,
        showlegend=show_legend,
        legend=dict(
            bgcolor="rgba(15,23,42,0.85)", bordercolor=c["border"], borderwidth=1,
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            font=dict(color=c["text_secondary"], size=12),
        ),
        hoverlabel=dict(
            bgcolor=c["panel"], bordercolor=c["border"],
            font=dict(family=_FONT_MONO, color=c["text"], size=12),
        ),
    )
    fig.update_xaxes(
        gridcolor=c["grid"], zerolinecolor=c["border"], linecolor=c["border"],
        tickfont=dict(family=_FONT_MONO, size=11, color=c["text_secondary"]),
        title_font=dict(color=c["text_secondary"], size=12),
        showspikes=True, spikemode="across", spikethickness=1,
        spikecolor=c["text_secondary"], spikedash="dot",
    )
    fig.update_yaxes(
        gridcolor=c["grid"], zerolinecolor=c["border"], linecolor=c["border"],
        tickfont=dict(family=_FONT_MONO, size=11, color=c["text_secondary"]),
        title_font=dict(color=c["text_secondary"], size=12),
        showspikes=True, spikemode="across", spikethickness=1,
        spikecolor=c["text_secondary"], spikedash="dot",
    )
    # 子图标题（make_subplots 标题为 annotations）在深色下需提亮
    if fig.layout.annotations:
        fig.update_annotations(font=dict(color=c["text_secondary"], size=12))
    return fig


def add_range_selector(fig):
    """为时间序列图添加 Plotly 范围选择按钮（1M/3M/6M/1Y/全部）。返回 fig。"""
    c = COLORS
    fig.update_xaxes(
        rangeselector=dict(
            buttons=[
                dict(count=1, label="1M", step="month", stepmode="backward"),
                dict(count=3, label="3M", step="month", stepmode="backward"),
                dict(count=6, label="6M", step="month", stepmode="backward"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(step="all", label="全部"),
            ],
            bgcolor=c["panel"], activecolor=c["accent"],
            bordercolor=c["border"], borderwidth=1,
            font=dict(color=c["text_secondary"], size=11),
            x=0, xanchor="left", y=1.16, yanchor="top",
        )
    )
    return fig


# ========== 排行榜表格样式 ==========
def style_ranking(df: pd.DataFrame):
    """
    把排行榜 DataFrame 包装为 pandas Styler：
    - 涨跌幅(%)：红绿文字 + 以 0 为中心的单元格条形（直观正负与幅度）
    - 数值列：等宽字体、单位格式化（最新价 2 位、成交额→亿、换手率→%）

    参数 df 的列名应已中文化：代码/名称/最新价/涨跌幅(%)/成交额/换手率(%)。
    返回 Styler，可直接传给 st.dataframe（保留 on_select 行选择能力）。
    """
    c = COLORS
    fmt: dict = {}
    if "最新价" in df.columns:
        fmt["最新价"] = "{:.2f}"
    if "涨跌幅(%)" in df.columns:
        fmt["涨跌幅(%)"] = "{:+.2f}%"
    if "成交额" in df.columns:
        fmt["成交额"] = lambda v: f"{v / 1e8:.2f} 亿" if pd.notna(v) else "—"
    if "换手率(%)" in df.columns:
        fmt["换手率(%)"] = "{:.2f}%"

    def _pct_color(v):
        if pd.isna(v) or v == 0:
            return f"color: {c['text_secondary']}"
        return f"color: {c['up']}" if v > 0 else f"color: {c['down']}"

    styler = df.style.format(fmt)

    # 数值列等宽
    num_cols = [col for col in ["最新价", "涨跌幅(%)", "成交额", "换手率(%)"] if col in df.columns]
    if num_cols:
        styler = styler.set_properties(subset=num_cols, **{"font-family": _FONT_MONO})

    if "涨跌幅(%)" in df.columns:
        styler = styler.map(_pct_color, subset=["涨跌幅(%)"])
        styler = styler.bar(
            subset=["涨跌幅(%)"], align=0,
            color=[c["down"], c["up"]],
        )
    return styler
