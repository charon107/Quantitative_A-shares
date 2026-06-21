"""
MA5 > MA10 持续时长分布统计（一次性分析脚本）

口径：
- 对全市场每只股票按收盘价算 MA5/MA10。
- 一个「样本」= MA5 从 ≤ 上穿 MA10 到回落的一段完整金叉区间，
  且**上穿日 >= START_DATE**（2025-01-01 时已在持续中的区间属左截断，不计入）。
- 时长 = 该区间内 MA5 > MA10 的交易日数（严格 >，相等即区间结束）。
- 一直延伸到**全市场最新交易日**（MA5 仍 > MA10）的区间标 ongoing=True，
  仍计入分布，但时长仅为下限（右删失）。停牌/退市导致序列提前结束的末段
  不算 ongoing（其 end_date < 市场最新日），改判为已结束。

产出（OUT_DIR 下）：
- samples_detail.csv   每个样本一行：code/start_date/end_date/duration/ongoing
- duration_summary.csv 汇总：总体/分桶/分位数（全部 与 仅已结束 各一份）
- duration_hist.png    直方图：已结束 vs 未结束 堆叠两色

运行：
    cd WechatNum && .venv\\Scripts\\python -m src.analysis.ma5_above_ma10_duration
"""
import glob
from pathlib import Path

import numpy as np
import pandas as pd

# 注意：matplotlib 仅在 plot_histogram() 内惰性导入，
# 这样看板等只需 compute_duration_samples 的调用方 import 本模块时不会拉起 matplotlib。


# =========================
# 配置区
# =========================
DATA_DIR = "股价数据_parquet_fq"
KLINE_SUBDIR = "kline_fq"

START_DATE = "2025-01-01"   # 只计入上穿日 >= 此日期的样本
MA_SHORT = 5
MA_LONG = 10

OUT_DIR = "ma5_ma10_duration_out"
OUT_DETAIL_CSV = "samples_detail.csv"
OUT_SUMMARY_CSV = "duration_summary.csv"
OUT_HIST_PNG = "duration_hist.png"

PROGRESS_EVERY = 200  # 每处理多少文件打印一次进度

# 分桶展示（含下界，含上界；上界 None 表示开区间到 +∞）
BUCKETS = [(1, 1), (2, 2), (3, 4), (5, 7), (8, 12), (13, 20), (21, 34), (35, None)]

# 品牌配色（深色终端绿；不 import ui_theme 以免引入 streamlit 依赖）
COLOR_CLOSED = "#22C55E"   # 已结束样本（上涨绿）
COLOR_ONGOING = "#FBBF24"  # 未结束样本（涨停黄）


# =========================
# 工具函数（纯函数，便于单测）
# =========================
def load_kline(path: str) -> pd.DataFrame:
    """读单只 parquet：解析日期、按 date 升序、丢弃无收盘价的行。失败返回空 DataFrame。"""
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty or "date" not in df.columns or "close" not in df.columns:
        return pd.DataFrame()

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return df


def add_ma(df: pd.DataFrame, short: int = MA_SHORT, long: int = MA_LONG) -> pd.DataFrame:
    """用收盘价计算 MA{short}/MA{long}（不足窗口的行为 NaN）。"""
    df = df.copy()
    df["MA5"] = df["close"].rolling(short, min_periods=short).mean()
    df["MA10"] = df["close"].rolling(long, min_periods=long).mean()
    return df


def extract_samples(df: pd.DataFrame, code: str, start_date) -> list[dict]:
    """
    从含 MA5/MA10 的 df 中抽取「MA5 > MA10 连续区间」样本。

    仅保留**上穿日 >= start_date** 的完整金叉区间（排除左截断）。
    延伸到序列末尾的区间标 ongoing=True（右删失，时长为下限）。

    返回 list[dict]：{code, start_date, end_date, duration, ongoing}
    """
    if df.empty or "MA5" not in df.columns or "MA10" not in df.columns:
        return []

    start_ts = pd.Timestamp(start_date)

    # 严格 >；NaN（前 long-1 日无 MA10）视为 False，不会误判区间起点
    above = (df["MA5"] > df["MA10"]).to_numpy()
    above = np.where(np.isnan(df["MA5"].to_numpy()) | np.isnan(df["MA10"].to_numpy()),
                     False, above)

    n = len(above)
    if n == 0:
        return []

    # 成对边界法：在两端补 0，对 0/1 序列求差，+1 处为段起点、-1 处为段终点的下一位
    flags = above.astype(np.int8)
    diff = np.diff(np.concatenate(([0], flags, [0])))
    starts = np.flatnonzero(diff == 1)        # 段起始下标（含）
    ends = np.flatnonzero(diff == -1) - 1      # 段结束下标（含）

    dates = df["date"].to_numpy()
    samples = []
    for s, e in zip(starts, ends):
        start_d = pd.Timestamp(dates[s])
        if start_d < start_ts:
            continue  # 左截断：上穿日早于统计窗口，不计入
        samples.append({
            "code": code,
            "start_date": start_d.strftime("%Y-%m-%d"),
            "end_date": pd.Timestamp(dates[e]).strftime("%Y-%m-%d"),
            "duration": int(e - s + 1),
            "ongoing": bool(e == n - 1),
        })
    return samples


def code_from_path(path: str) -> str:
    """从 parquet 文件名取股票代码（去扩展名）。"""
    return Path(path).stem


# =========================
# 汇总与绘图
# =========================
def _describe(durations: pd.Series) -> dict:
    """一组时长的统计指标。"""
    if durations.empty:
        return {k: np.nan for k in
                ["n", "mean", "median", "std", "min", "max",
                 "p25", "p50", "p75", "p90", "p95"]}
    return {
        "n": int(durations.size),
        "mean": round(float(durations.mean()), 2),
        "median": float(durations.median()),
        "std": round(float(durations.std(ddof=1)) if durations.size > 1 else 0.0, 2),
        "min": int(durations.min()),
        "max": int(durations.max()),
        "p25": float(durations.quantile(0.25)),
        "p50": float(durations.quantile(0.50)),
        "p75": float(durations.quantile(0.75)),
        "p90": float(durations.quantile(0.90)),
        "p95": float(durations.quantile(0.95)),
    }


def _bucket_label(lo: int, hi) -> str:
    if hi is None:
        return f"{lo}+"
    return f"{lo}" if lo == hi else f"{lo}-{hi}"


def apply_strict_ongoing(detail: pd.DataFrame, market_last_date) -> pd.DataFrame:
    """
    收紧 ongoing 口径（市场级）：仅当样本 end_date == 全市场最新交易日，才算未结束（右删失）。

    extract_samples 判定的是「延伸到该股自身最后一根 K 线」；对停牌/退市导致序列
    提前结束的股票，其末段并非「截至今天仍在持续」，在此改判为已结束（ongoing=False）。
    """
    if detail.empty:
        return detail
    detail = detail.copy()
    last = pd.Timestamp(market_last_date)
    detail["ongoing"] = pd.to_datetime(detail["end_date"]) == last
    return detail


def build_summary(detail: pd.DataFrame) -> pd.DataFrame:
    """生成长表汇总：总体计数 + 全部/已结束 两套分位数 + 分桶计数占比。"""
    rows = []
    n_total = len(detail)
    n_ongoing = int(detail["ongoing"].sum()) if n_total else 0
    n_closed = n_total - n_ongoing
    rows.append({"metric": "n_total_samples", "scope": "-", "value": n_total})
    rows.append({"metric": "n_ongoing(右删失,时长为下限)", "scope": "-", "value": n_ongoing})
    rows.append({"metric": "n_closed(已结束)", "scope": "-", "value": n_closed})

    for scope, dur in [("all", detail["duration"]),
                       ("closed_only", detail.loc[~detail["ongoing"], "duration"])]:
        for k, v in _describe(dur).items():
            rows.append({"metric": f"duration_{k}", "scope": scope, "value": v})

    # 分桶（基于全部样本）
    for lo, hi in BUCKETS:
        upper = hi if hi is not None else 10**9
        cnt = int(((detail["duration"] >= lo) & (detail["duration"] <= upper)).sum())
        pct = round(cnt / n_total * 100, 2) if n_total else 0.0
        rows.append({"metric": f"bucket_{_bucket_label(lo, hi)}天",
                     "scope": "count/pct%", "value": f"{cnt} / {pct}%"})

    return pd.DataFrame(rows, columns=["metric", "scope", "value"])


def plot_histogram(detail: pd.DataFrame, out_path: Path) -> None:
    """直方图：已结束 vs 未结束 堆叠两色。x=持续交易日数，y=样本数（log）。"""
    if detail.empty:
        return

    # matplotlib 惰性导入（仅出 PNG 时才需要），避免拖累纯计算调用方
    import matplotlib
    matplotlib.use("Agg")  # 无界面后端，纯出图
    import matplotlib.pyplot as plt

    # 中文字体回退（Windows 常见黑体），避免方块
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    max_d = int(detail["duration"].max())
    bins = np.arange(1, max_d + 2)  # 每个整数天一个 bin

    closed = detail.loc[~detail["ongoing"], "duration"]
    ongoing = detail.loc[detail["ongoing"], "duration"]

    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=130)
    fig.patch.set_facecolor("#0F172A")
    ax.set_facecolor("#0F172A")

    ax.hist(
        [closed, ongoing], bins=bins, stacked=True,
        color=[COLOR_CLOSED, COLOR_ONGOING],
        label=[f"已结束 ({closed.size})", f"未结束/右删失 ({ongoing.size})"],
        edgecolor="#0F172A", linewidth=0.3,
    )
    ax.set_yscale("log")  # 右偏严重，log 轴看清长尾
    ax.set_xlabel("持续交易日数", color="#94A3B8")
    ax.set_ylabel("样本数（对数）", color="#94A3B8")

    median = detail["duration"].median()
    p90 = detail["duration"].quantile(0.90)
    ax.axvline(median, color="#38BDF8", ls="--", lw=1.2, label=f"中位数 {median:.0f}天")
    ax.axvline(p90, color="#C084FC", ls=":", lw=1.2, label=f"P90 {p90:.0f}天")

    ax.set_title(
        f"MA5>MA10 持续时长分布（{START_DATE} 起，共 {len(detail)} 个样本，"
        f"其中 {int(detail['ongoing'].sum())} 个未结束）",
        color="#F8FAFC", fontsize=12, pad=12,
    )
    ax.tick_params(colors="#94A3B8")
    for spine in ax.spines.values():
        spine.set_color("#334155")
    ax.grid(axis="y", color="#1E293B", lw=0.6)
    ax.legend(facecolor="#1E293B", edgecolor="#334155", labelcolor="#F8FAFC", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)


# =========================
# 编排（计算层，可被看板复用）
# =========================
def compute_duration_samples(
    data_dir: str = DATA_DIR,
    kline_subdir: str = KLINE_SUBDIR,
    start_date: str = START_DATE,
    ma_short: int = MA_SHORT,
    ma_long: int = MA_LONG,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    遍历全市场，抽取 MA{short}>MA{long} 金叉区间样本，并应用市场级 ongoing 口径。

    返回 detail DataFrame（列 code/start_date/end_date/duration/ongoing，按时长降序）。
    纯计算、无文件 IO；verbose=True 时打印进度与口径收紧说明。空目录返回空 DataFrame。
    """
    cols = ["code", "start_date", "end_date", "duration", "ongoing"]
    kline_dir = Path(data_dir) / kline_subdir
    files = sorted(glob.glob(str(kline_dir / "*.parquet")))
    if not files:
        return pd.DataFrame(columns=cols)

    if verbose:
        print(f"[INFO] 共 {len(files)} 只股票，开始统计 MA{ma_short}>MA{ma_long} 区间（{start_date} 起）…")

    samples: list[dict] = []
    market_last = None  # 全市场最新交易日（所有股票末根 K 线日期的最大值）
    for i, path in enumerate(files, start=1):
        df = load_kline(path)
        if df.empty:
            continue
        df = add_ma(df, ma_short, ma_long)
        last_d = df["date"].iloc[-1]
        if market_last is None or last_d > market_last:
            market_last = last_d
        samples.extend(extract_samples(df, code_from_path(path), start_date))
        if verbose and i % PROGRESS_EVERY == 0:
            print(f"  进度 {i}/{len(files)}，累计样本 {len(samples)}")

    detail = pd.DataFrame(samples, columns=cols)
    # 收紧 ongoing 口径：仅 end_date == 全市场最新交易日 才算未结束
    n_own = int(detail["ongoing"].sum()) if not detail.empty else 0
    detail = apply_strict_ongoing(detail, market_last)
    n_strict = int(detail["ongoing"].sum()) if not detail.empty else 0
    if verbose and market_last is not None:
        print(
            f"[INFO] 全市场最新交易日 {pd.Timestamp(market_last).date()}；"
            f"ongoing 由「到各股末根 K 线」{n_own} 收紧为「到市场最新日」{n_strict}"
            f"（{n_own - n_strict} 个属停牌/退市的提前结束序列，改判已结束）"
        )

    if not detail.empty:
        detail = detail.sort_values(
            ["duration", "code"], ascending=[False, True]
        ).reset_index(drop=True)
    return detail


# =========================
# 主流程
# =========================
def main():
    detail = compute_duration_samples(verbose=True)

    out_dir = Path(OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    detail.to_csv(out_dir / OUT_DETAIL_CSV, index=False, encoding="utf-8-sig")
    print(f"[OK] 明细：{out_dir / OUT_DETAIL_CSV}（{len(detail)} 个样本）")

    if detail.empty:
        print("[WARN] 无样本，跳过汇总与绘图。")
        return

    summary = build_summary(detail)
    summary.to_csv(out_dir / OUT_SUMMARY_CSV, index=False, encoding="utf-8-sig")
    print(f"[OK] 汇总：{out_dir / OUT_SUMMARY_CSV}")

    plot_histogram(detail, out_dir / OUT_HIST_PNG)
    print(f"[OK] 直方图：{out_dir / OUT_HIST_PNG}")

    # 控制台关键汇总
    n_ongoing = int(detail["ongoing"].sum())
    print(
        f"\n[汇总] 样本总数 {len(detail)} | 未结束 {n_ongoing} | "
        f"中位时长 {detail['duration'].median():.0f} 天 | "
        f"P90 {detail['duration'].quantile(0.9):.0f} 天 | "
        f"最长 {int(detail['duration'].max())} 天"
    )


if __name__ == "__main__":
    main()
