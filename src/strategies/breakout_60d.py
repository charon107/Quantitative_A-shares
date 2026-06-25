import os
import pandas as pd
from tqdm import tqdm

from src.data_collection import tushare_client as tsc

# =========================
# 配置区
# =========================
BASE_DIR = "股价数据_parquet_fq"
DATA_DIR = os.path.join(BASE_DIR, "kline_fq")

START_YEAR = 2021
END_YEAR = 2025

N_HIGH = 60
MA_FAST = 7
MA_MID = 20
MA_SLOW = 60
COOLDOWN_DAYS = 20

LOOKBACK_DAYS = 30
MAX_RUNUP = 0.20

# 突破当日涨幅阈值（%）
PCTCHG_MIN = 9.9

# =========================
# 工具函数
# =========================
def ensure_exists():
    if not os.path.isdir(DATA_DIR):
        raise FileNotFoundError(f"找不到数据目录: {DATA_DIR}\n请确认 BASE_DIR / DATA_DIR 配置正确。")


def code_to_pure(code: str) -> str:
    if not isinstance(code, str):
        return ""
    if code.startswith(("sh.", "sz.", "bj.")):
        return code.split(".", 1)[1]
    return code


def get_code_name_map() -> dict:
    df = tsc.fetch_stock_basic()
    return dict(zip(df["code"].astype(str), df["code_name"].astype(str)))


def read_kline_parquet(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame()

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    for c in ["open", "high", "low", "close", "pctChg"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    need = {"open", "high", "low", "close"}
    if not need.issubset(set(df.columns)):
        return pd.DataFrame()

    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def runup_ok_on_signal_date(d: pd.DataFrame, i: int) -> bool:
    """不过热过滤：信号日 close 相对过去LOOKBACK_DAYS(不含当日) base 涨幅 < MAX_RUNUP"""
    start = i - LOOKBACK_DAYS
    end = i
    if start < 0:
        return False

    close_t = d.at[i, "close"]
    if pd.isna(close_t) or close_t <= 0:
        return False

    hist = d.iloc[start:end]
    if hist.empty:
        return False

    min_close = hist["close"].min()
    min_ma20 = hist["ma20"].min()
    if pd.isna(min_close) or pd.isna(min_ma20):
        return False

    base_price = min(min_close, min_ma20)
    if pd.isna(base_price) or base_price <= 0:
        return False

    runup = (close_t / base_price) - 1.0
    return runup < MAX_RUNUP


def prepare_signal_df(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """
    统一准备信号计算字段，避免两套卖出逻辑重复写指标与条件。
    """
    if df.empty:
        return pd.DataFrame()

    d = df.copy()

    d["ma7"] = d["close"].rolling(MA_FAST, min_periods=MA_FAST).mean()
    d["ma20"] = d["close"].rolling(MA_MID, min_periods=MA_MID).mean()
    d["ma60"] = d["close"].rolling(MA_SLOW, min_periods=MA_SLOW).mean()

    # pctChg（%）
    if "pctChg" not in d.columns:
        d["pctChg"] = (d["close"] / d["close"].shift(1) - 1.0) * 100.0
    else:
        d["pctChg"] = pd.to_numeric(d["pctChg"], errors="coerce")

    # break60：收盘价突破过去60日(不含当日)的close最高 & open最高
    d["high60_prev_close"] = d["close"].rolling(N_HIGH, min_periods=N_HIGH).max().shift(1)
    d["high60_prev_open"] = d["open"].rolling(N_HIGH, min_periods=N_HIGH).max().shift(1)
    d["break60"] = (d["close"] > d["high60_prev_close"]) & (d["close"] > d["high60_prev_open"])

    # 冷却：过去COOLDOWN_DAYS天（不含当日）是否出现过break60
    d["break60_cnt_prev20"] = d["break60"].rolling(COOLDOWN_DAYS, min_periods=COOLDOWN_DAYS).sum().shift(1)

    # 多头排列
    d["ma_bull"] = (d["ma7"] > d["ma20"]) & (d["ma20"] > d["ma60"])

    # 年内过滤
    mask_year = (d["date"].dt.year == year)

    d["signal"] = (
        d["break60"] &
        d["ma_bull"] &
        (d["break60_cnt_prev20"] == 0) &
        (d["pctChg"] > PCTCHG_MIN) &
        mask_year
    )

    return d


def compute_trades_buyclose_sellnextopen(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """
    信号日收盘价买入，次日开盘价卖出。
    输出每笔交易的收益率 ret = sell_open / buy_close - 1
    """
    if df.empty:
        return pd.DataFrame()

    d = prepare_signal_df(df, year)
    if d.empty:
        return pd.DataFrame()

    idxs = d.index[d["signal"]].tolist()
    if not idxs:
        return pd.DataFrame()

    rows = []
    n = len(d)

    for i in idxs:
        # 不过热过滤（用信号日 ma20/close）
        if not runup_ok_on_signal_date(d, i):
            continue

        buy_close = d.at[i, "close"]
        sell_i = i + 1
        if sell_i >= n:
            continue

        sell_open = d.at[sell_i, "open"]
        if pd.isna(buy_close) or pd.isna(sell_open) or buy_close <= 0 or sell_open <= 0:
            continue

        ret = sell_open / buy_close - 1.0

        rows.append({
            "buy_date": d.at[i, "date"].strftime("%Y-%m-%d"),
            "sell_date": d.at[sell_i, "date"].strftime("%Y-%m-%d"),
            "buy_close": float(buy_close),
            "sell_open": float(sell_open),
            "ret": float(ret),
            "pctChg_signal": float(d.at[i, "pctChg"]),
        })

    return pd.DataFrame(rows)


def compute_trades_buyclose_sellnextclose(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """
    信号日收盘价买入，次日收盘价卖出。
    输出每笔交易的收益率 ret = sell_close / buy_close - 1
    """
    if df.empty:
        return pd.DataFrame()

    d = prepare_signal_df(df, year)
    if d.empty:
        return pd.DataFrame()

    idxs = d.index[d["signal"]].tolist()
    if not idxs:
        return pd.DataFrame()

    rows = []
    n = len(d)

    for i in idxs:
        # 不过热过滤（和原策略保持一致）
        if not runup_ok_on_signal_date(d, i):
            continue

        buy_close = d.at[i, "close"]
        sell_i = i + 1
        if sell_i >= n:
            continue

        sell_close = d.at[sell_i, "close"]
        if pd.isna(buy_close) or pd.isna(sell_close) or buy_close <= 0 or sell_close <= 0:
            continue

        ret = sell_close / buy_close - 1.0

        rows.append({
            "buy_date": d.at[i, "date"].strftime("%Y-%m-%d"),
            "sell_date": d.at[sell_i, "date"].strftime("%Y-%m-%d"),
            "buy_close": float(buy_close),
            "sell_close": float(sell_close),
            "ret": float(ret),
            "pctChg_signal": float(d.at[i, "pctChg"]),
        })

    return pd.DataFrame(rows)


def summarize_performance(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {
            "n_trades": 0,
            "win_rate": None,
            "avg_profit": None,
            "avg_loss": None,
            "avg_return": None,
        }

    r = trades["ret"].astype(float).dropna()
    if r.empty:
        return {
            "n_trades": 0,
            "win_rate": None,
            "avg_profit": None,
            "avg_loss": None,
            "avg_return": None,
        }

    wins = r[r > 0]
    losses = r[r < 0]

    n = int(r.shape[0])
    win_rate = float((r > 0).mean())

    avg_profit = float(wins.mean()) if not wins.empty else 0.0
    avg_loss = float(losses.mean()) if not losses.empty else 0.0  # 负数
    avg_return = float(r.mean())

    return {
        "n_trades": n,
        "win_rate": win_rate,
        "avg_profit": avg_profit,
        "avg_loss": avg_loss,
        "avg_return": avg_return,
    }


def print_performance(title: str, perf: dict):
    def fmt_pct(x):
        return "NA" if x is None else f"{x*100:.2f}%"

    print(f"\n{title}")
    print(f"Trades: {perf['n_trades']}")
    print(f"Win rate: {fmt_pct(perf['win_rate'])}")
    print(f"Avg profit (wins): {fmt_pct(perf['avg_profit'])}")
    print(f"Avg loss (losses): {fmt_pct(perf['avg_loss'])}")
    print(f"Avg return (all): {fmt_pct(perf['avg_return'])}")


# =========================
# 主流程
# =========================
def main():
    ensure_exists()

    name_map = get_code_name_map()

    for year in range(START_YEAR, END_YEAR + 1):
        print(f"\n开始回测 {year} 年：")

        files = [f for f in os.listdir(DATA_DIR) if f.endswith(".parquet")]
        if not files:
            raise FileNotFoundError(f"{DATA_DIR} 下没有 parquet 文件。")

        all_trades_next_open = []
        all_trades_next_close = []

        for fn in tqdm(files, desc=f"Backtesting {year}"):
            code_raw = fn.replace(".parquet", "")
            code_p = code_to_pure(code_raw)
            path = os.path.join(DATA_DIR, fn)

            df = read_kline_parquet(path)
            if df.empty:
                continue

            # 策略1：当日收盘买，次日开盘卖
            trades_open = compute_trades_buyclose_sellnextopen(df, year)
            if not trades_open.empty:
                trades_open.insert(0, "code", code_p)
                trades_open.insert(1, "name", name_map.get(code_raw, ""))
                all_trades_next_open.append(trades_open)

            # 策略2：当日收盘买，次日收盘卖
            trades_close = compute_trades_buyclose_sellnextclose(df, year)
            if not trades_close.empty:
                trades_close.insert(0, "code", code_p)
                trades_close.insert(1, "name", name_map.get(code_raw, ""))
                all_trades_next_close.append(trades_close)

        if all_trades_next_open:
            out_open = pd.concat(all_trades_next_open, ignore_index=True)
            perf_open = summarize_performance(out_open)
            print_performance("策略1：当日收盘买入，次日开盘卖出", perf_open)
        else:
            print("\n策略1：当日收盘买入，次日开盘卖出")
            print(f"{year} 年没有找到满足条件的交易。")

        if all_trades_next_close:
            out_close = pd.concat(all_trades_next_close, ignore_index=True)
            perf_close = summarize_performance(out_close)
            print_performance("策略2：当日收盘买入，次日收盘卖出", perf_close)
        else:
            print("\n策略2：当日收盘买入，次日收盘卖出")
            print(f"{year} 年没有找到满足条件的交易。")



if __name__ == "__main__":
    main()
