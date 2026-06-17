import os
import re
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.widgets import Button

# ========= 配置（你只改这里）=========
OUT_DIR = r"parquet_A股_2007-2024_Q4"
SELECTED_CSV = os.path.join(OUT_DIR, "selected_stocks_2013-2025.csv")

ANALYSIS_YEAR = 2019  # 选择要分析的年份（自然年）
MA_N = 20             # n日均线
USE_PROFIT_PUBDATE = True  # True: 用本地profit.parquet标注pubDate并用于截断

# ✅ 你的本地股价数据目录（按你记忆结构）
LOCAL_BASE_DIR = r"股价数据_parquet_fq"
LOCAL_KLINE_DIR = os.path.join(LOCAL_BASE_DIR, "kline_fq")  # 前复权日线 parquet

# 起始日期固定为当年年初
DATE_START = f"{ANALYSIS_YEAR}-01-01"

# 图形
FIGSIZE = (14, 8)
MAX_PUBDATE_LABELS = 10
EXPAND_DAYS_AFTER_NEXT = 3

# ✅ 上证指数（上海综合指数）
INDEX_CODE = "sh.000001"
INDEX_NAME = "上证指数"

# 中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False
# ===================================


def read_selected_pool(csv_path: str, year: int) -> pd.DataFrame:
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"找不到：{csv_path}")

    df = pd.read_csv(csv_path, dtype={"code": str})
    if not {"year", "code"}.issubset(df.columns):
        raise ValueError(f"{csv_path} 缺少 year/code 列，实际列：{df.columns.tolist()}")

    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    sub = df[df["year"] == year].copy()
    sub = sub.dropna(subset=["code"]).drop_duplicates("code").reset_index(drop=True)

    if "name" not in sub.columns:
        sub["name"] = ""
    sub["name"] = sub["name"].fillna("")
    return sub[["code", "name"]]


def normalize_code_to_local(code: str) -> str:
    """
    尽量把各种常见code格式转成你本地文件用的 baostock 风格：
    - sh.600000 / sz.000001  (目标)
    兼容输入：
    - 600000 / 000001
    - 600000.SH / 000001.SZ / SH600000 / SZ000001
    """
    if code is None:
        return ""

    s = str(code).strip()
    if not s:
        return ""

    s = s.replace(" ", "").replace("_", "").replace("-", "")

    # 已是 sh.600000 / sz.000001
    if re.match(r"^(sh|sz)\.\d{6}$", s, flags=re.I):
        return s.lower()

    # 600000.SH / 000001.SZ
    m = re.match(r"^(\d{6})\.(SH|SZ)$", s, flags=re.I)
    if m:
        num, ex = m.group(1), m.group(2).lower()
        return f"{ex}.{num}"

    # SH600000 / SZ000001
    m = re.match(r"^(SH|SZ)(\d{6})$", s, flags=re.I)
    if m:
        ex, num = m.group(1).lower(), m.group(2)
        return f"{ex}.{num}"

    # 纯数字：按主板规则粗略判断（60->sh，其它默认sz）
    m = re.match(r"^\d{6}$", s)
    if m:
        num = s
        ex = "sh" if num.startswith("60") else "sz"
        return f"{ex}.{num}"

    return s.lower()


def get_price_line_local(code: str, start_date: str, end_date: str | None, ma_n: int) -> pd.DataFrame:
    """
    从本地 parquet 读取前复权日线（你存储结构：股价数据_parquet_fq/kline_fq/{code}.parquet）
    只用 date + close，并计算 MA。
    """
    code_local = normalize_code_to_local(code)
    path = os.path.join(LOCAL_KLINE_DIR, f"{code_local}.parquet")
    if not os.path.exists(path):
        # 兼容：万一你的文件名大小写不同
        alt = os.path.join(LOCAL_KLINE_DIR, f"{code_local.lower()}.parquet")
        if os.path.exists(alt):
            path = alt
        else:
            return pd.DataFrame(columns=["date", "close", "ma"])

    df = pd.read_parquet(path, columns=["date", "close"])
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "close", "ma"])

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)

    start_dt = pd.to_datetime(start_date)
    df = df[df["date"] >= start_dt]

    if end_date:
        end_dt = pd.to_datetime(end_date)
        df = df[df["date"] <= end_dt]

    df["ma"] = df["close"].rolling(window=ma_n).mean()
    return df.reset_index(drop=True)


def load_pubdates_profit_two_years(out_dir: str, codes: list[str], year: int) -> pd.DataFrame:
    profit_dir = os.path.join(out_dir, "profit")
    if not os.path.exists(profit_dir):
        print(f"[WARN] 找不到 profit 目录：{profit_dir}，将不标注pubDate，也无法用年报日截断")
        return pd.DataFrame(columns=["code", "pubDate"])

    profit = pd.read_parquet(profit_dir, columns=["code", "pubDate"])
    # 注意：profit里的code格式可能与selected不同，这里也 normalize 一下更稳
    profit["code"] = profit["code"].astype(str).map(normalize_code_to_local)
    codes_norm = [normalize_code_to_local(c) for c in codes]

    profit = profit[profit["code"].isin(codes_norm)].copy()
    profit["pubDate"] = pd.to_datetime(profit["pubDate"], errors="coerce")
    profit = profit.dropna(subset=["pubDate"])

    start = pd.Timestamp(year=year, month=1, day=1)
    end = pd.Timestamp(year=year + 2, month=1, day=1)
    profit = profit[(profit["pubDate"] >= start) & (profit["pubDate"] < end)]
    return profit[["code", "pubDate"]]


def build_pubdate_maps(profit_pub: pd.DataFrame, year: int) -> tuple[
    dict[str, list[pd.Timestamp]], dict[str, pd.Timestamp | None]]:
    pubdates_in_year = {}
    next_pubdate = {}

    if profit_pub.empty:
        return pubdates_in_year, next_pubdate

    profit_pub = profit_pub.copy()
    profit_pub["pubDate"] = pd.to_datetime(profit_pub["pubDate"]).dt.normalize()

    y_start = pd.Timestamp(year=year, month=1, day=1)
    y_end = pd.Timestamp(year=year + 1, month=1, day=1)
    y1_start = y_end
    y1_end = pd.Timestamp(year=year + 2, month=1, day=1)

    for code, g in profit_pub.groupby("code"):
        ds = sorted(g["pubDate"].unique())

        ds_y = [pd.Timestamp(d) for d in ds if y_start <= d < y_end]
        pubdates_in_year[code] = ds_y

        ds_y1 = [pd.Timestamp(d) for d in ds if y1_start <= d < y1_end]
        next_pubdate[code] = ds_y1[0] if ds_y1 else None

    return pubdates_in_year, next_pubdate


def _draw_vlines_and_labels(ax, df_ref: pd.DataFrame,
                            pubdates_year: list[pd.Timestamp],
                            next_pub: pd.Timestamp | None,
                            max_labels: int):
    if df_ref is None or df_ref.empty:
        return

    if pubdates_year:
        min_d = df_ref["date"].min().normalize()
        max_d = df_ref["date"].max().normalize()
        in_range = [d for d in pubdates_year if min_d <= d <= max_d]

        if len(in_range) > max_labels:
            in_range = in_range[:max_labels]

        y_top = df_ref["close"].max()
        y_bottom = df_ref["close"].min()
        y_span = (y_top - y_bottom) if y_top > y_bottom else 1.0

        for i, d in enumerate(in_range):
            ax.axvline(d, linestyle="--", linewidth=1)
            y_text = y_top - (i % 5) * (0.06 * y_span)
            ax.text(d, y_text, d.strftime("%m-%d"), rotation=90, va="top", fontsize=9)

    if isinstance(next_pub, pd.Timestamp):
        ax.axvline(next_pub, linestyle="-.", linewidth=1.5)
        y_top = df_ref["close"].max()
        ax.text(next_pub, y_top, f"Next:{next_pub.strftime('%Y-%m-%d')}",
                rotation=90, va="top", fontsize=10)


def draw_page(ax_stock, ax_index,
              code: str, name: str,
              df_stock: pd.DataFrame,
              df_index: pd.DataFrame,
              pubdates_year: list[pd.Timestamp],
              next_pub: pd.Timestamp | None,
              ma_n: int):
    ax_stock.clear()
    ax_index.clear()

    end_str = next_pub.strftime("%Y-%m-%d") if isinstance(next_pub, pd.Timestamp) else "最新"
    title = f"{name}（{code}）  {ANALYSIS_YEAR}起 收盘价  截止:{end_str}  (本地parquet)  MA{ma_n}"
    ax_stock.set_title(title, fontsize=14, fontweight="bold", pad=10)

    # 上图：个股
    if df_stock is None or df_stock.empty:
        ax_stock.text(0.5, 0.5, "区间无价格数据", ha="center", va="center", transform=ax_stock.transAxes)
        ax_stock.grid(True, alpha=0.3, linestyle="--")
    else:
        ax_stock.plot(df_stock["date"], df_stock["close"], linewidth=1.2, label="收盘价")
        if df_stock["ma"].notna().any():
            ax_stock.plot(df_stock["date"], df_stock["ma"], linewidth=1.2, linestyle="--", label=f"MA{ma_n}")
        ax_stock.legend(loc="best")
        ax_stock.set_ylabel("Close")
        ax_stock.grid(True, alpha=0.3, linestyle="--")
        _draw_vlines_and_labels(ax_stock, df_stock, pubdates_year, next_pub, MAX_PUBDATE_LABELS)

    # 下图：上证指数
    ax_index.set_title(f"{INDEX_NAME}（{INDEX_CODE}）", fontsize=11, pad=6)
    if df_index is None or df_index.empty:
        ax_index.text(0.5, 0.5, "指数无价格数据", ha="center", va="center", transform=ax_index.transAxes)
        ax_index.grid(True, alpha=0.3, linestyle="--")
    else:
        ax_index.plot(df_index["date"], df_index["close"], linewidth=1.0, label=INDEX_NAME)
        if df_index["ma"].notna().any():
            ax_index.plot(df_index["date"], df_index["ma"], linewidth=1.0, linestyle="--", label=f"MA{ma_n}")
        ax_index.legend(loc="best", fontsize=9)
        ax_index.set_ylabel("Index")
        ax_index.grid(True, alpha=0.3, linestyle="--")
        _draw_vlines_and_labels(ax_index, df_index, pubdates_year, next_pub, MAX_PUBDATE_LABELS)

    ax_index.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax_index.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(ax_index.xaxis.get_majorticklabels(), rotation=45, ha='right')
    ax_index.set_xlabel("Date")
    plt.setp(ax_stock.get_xticklabels(), visible=False)

    if isinstance(next_pub, pd.Timestamp) and df_stock is not None and not df_stock.empty:
        last_trade = df_stock["date"].max()
        if next_pub > last_trade:
            ax_index.set_xlim(df_stock["date"].min(), next_pub + pd.Timedelta(days=EXPAND_DAYS_AFTER_NEXT))


def main():
    pool = read_selected_pool(SELECTED_CSV, ANALYSIS_YEAR)
    if pool.empty:
        print(f"[WARN] {ANALYSIS_YEAR} 年股票池为空")
        return

    # 规范化 code（保证能对上本地文件名）
    pool = pool.copy()
    pool["code"] = pool["code"].astype(str).map(normalize_code_to_local)

    codes = pool["code"].tolist()

    # 读取 pubDate（year & year+1）
    pubdates_year_map = {}
    next_pub_map = {}
    if USE_PROFIT_PUBDATE:
        profit_pub = load_pubdates_profit_two_years(OUT_DIR, codes, ANALYSIS_YEAR)
        pubdates_year_map, next_pub_map = build_pubdate_maps(profit_pub, ANALYSIS_YEAR)

    pages = []
    for _, r in pool.iterrows():
        code = r["code"]
        name = r["name"]

        end_dt = next_pub_map.get(code) if USE_PROFIT_PUBDATE else None
        end_date_str = end_dt.strftime("%Y-%m-%d") if isinstance(end_dt, pd.Timestamp) else None

        df_stock = get_price_line_local(code, DATE_START, end_date_str, MA_N)
        df_index = get_price_line_local(INDEX_CODE, DATE_START, end_date_str, MA_N)

        pubdates_year = pubdates_year_map.get(code, [])
        pages.append((code, name, df_stock, df_index, pubdates_year, end_dt))

    if not pages:
        print("[WARN] 没有可展示的股票页")
        return

    idx = 0
    total_pages = len(pages)

    fig, (ax_stock, ax_index) = plt.subplots(
        2, 1,
        figsize=FIGSIZE,
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08}
    )
    plt.subplots_adjust(bottom=0.20)

    status_text = fig.text(0.02, 0.03, "", ha="left", va="center", fontsize=12)

    def update_status():
        code, name, _, _, _, end_dt = pages[idx]
        end_str = end_dt.strftime("%Y-%m-%d") if isinstance(end_dt, pd.Timestamp) else "最新"
        status_text.set_text(
            f"Page {idx + 1} / {total_pages}   |   {code} {name}   |   截止:{end_str}   |   指数:{INDEX_CODE}   |   MA{MA_N}"
        )

    def render():
        code, name, df_stock, df_index, pubdates_year, end_dt = pages[idx]
        draw_page(ax_stock, ax_index, code, name, df_stock, df_index, pubdates_year, end_dt, MA_N)
        update_status()
        fig.canvas.draw_idle()

    def next_page(event):
        nonlocal idx
        idx = (idx + 1) % total_pages
        render()

    def prev_page(event):
        nonlocal idx
        idx = (idx - 1) % total_pages
        render()

    ax_prev = plt.axes([0.70, 0.03, 0.12, 0.08])
    ax_next = plt.axes([0.84, 0.03, 0.12, 0.08])
    bprev = Button(ax_prev, "Prev")
    bnext = Button(ax_next, "Next")
    bprev.on_clicked(prev_page)
    bnext.on_clicked(next_page)

    render()
    plt.show()


if __name__ == "__main__":
    main()
