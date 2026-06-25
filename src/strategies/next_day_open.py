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

HIGH_BREAKOUT_MIN = 9.9  # 要求：当日最高价相对昨日收盘涨幅 > 9.9%

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


def add_signal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    增加用于判断信号的列：
    1. prev_close: 昨日收盘价
    2. high_breakout_pct: 当日最高价相对昨日收盘价涨幅
       = high / prev_close - 1
    """
    if df.empty:
        return df

    d = df.copy()
    d["prev_close"] = pd.to_numeric(d["close"], errors="coerce").shift(1)
    d["high_breakout_pct"] = (d["high"] / d["prev_close"] - 1.0) * 100.0
    return d


def get_signal_indices(df: pd.DataFrame) -> list:
    """
    取出满足信号条件的行索引：
    1. 当日最高价相比昨日收盘价涨幅 > HIGH_BREAKOUT_MIN
    2. 日期在 START_YEAR ~ END_YEAR 内
    """
    if df.empty:
        return []

    d = add_signal_columns(df)

    mask_year = (
        (d["date"] >= pd.Timestamp(f"{START_YEAR}-01-01")) &
        (d["date"] <= pd.Timestamp(f"{END_YEAR}-12-31"))
    )
    base_cond = (d["high_breakout_pct"] > HIGH_BREAKOUT_MIN) & mask_year

    return base_cond[base_cond].index.tolist()


def compute_trades_buyclose_sellnextopen(df: pd.DataFrame) -> pd.DataFrame:
    """
    信号条件：
        当日最高价相对昨日收盘价涨幅 > HIGH_BREAKOUT_MIN

    交易规则：
        信号日收盘价买入，次日开盘价卖出
        收益率 ret = sell_open / buy_close - 1
    """
    if df.empty:
        return pd.DataFrame()

    d = add_signal_columns(df)
    idxs = get_signal_indices(d)
    if not idxs:
        return pd.DataFrame()

    rows = []
    n = len(d)

    for i in idxs:
        buy_close = d.at[i, "close"]
        sell_i = i + 1
        if sell_i >= n:
            continue

        sell_open = d.at[sell_i, "open"]
        prev_close = d.at[i, "prev_close"]
        high_breakout_pct = d.at[i, "high_breakout_pct"]

        if (
            pd.isna(buy_close) or pd.isna(sell_open) or
            pd.isna(prev_close) or pd.isna(high_breakout_pct) or
            buy_close <= 0 or sell_open <= 0 or prev_close <= 0
        ):
            continue

        ret = sell_open / buy_close - 1.0

        rows.append({
            "strategy": "buy_close_sell_next_open",
            "buy_date": d.at[i, "date"].strftime("%Y-%m-%d"),
            "sell_date": d.at[sell_i, "date"].strftime("%Y-%m-%d"),
            "buy_price": float(buy_close),
            "sell_price": float(sell_open),
            "ret": float(ret),
            "prev_close": float(prev_close),
            "high_signal": float(d.at[i, "high"]),
            "high_breakout_pct": float(high_breakout_pct),
        })

    return pd.DataFrame(rows)


def compute_trades_buyclose_sellnextclose(df: pd.DataFrame) -> pd.DataFrame:
    """
    信号条件：
        当日最高价相对昨日收盘价涨幅 > HIGH_BREAKOUT_MIN

    交易规则：
        信号日收盘价买入，次日收盘价卖出
        收益率 ret = sell_close / buy_close - 1
    """
    if df.empty:
        return pd.DataFrame()

    d = add_signal_columns(df)
    idxs = get_signal_indices(d)
    if not idxs:
        return pd.DataFrame()

    rows = []
    n = len(d)

    for i in idxs:
        buy_close = d.at[i, "close"]
        sell_i = i + 1
        if sell_i >= n:
            continue

        sell_close = d.at[sell_i, "close"]
        prev_close = d.at[i, "prev_close"]
        high_breakout_pct = d.at[i, "high_breakout_pct"]

        if (
            pd.isna(buy_close) or pd.isna(sell_close) or
            pd.isna(prev_close) or pd.isna(high_breakout_pct) or
            buy_close <= 0 or sell_close <= 0 or prev_close <= 0
        ):
            continue

        ret = sell_close / buy_close - 1.0

        rows.append({
            "strategy": "buy_close_sell_next_close",
            "buy_date": d.at[i, "date"].strftime("%Y-%m-%d"),
            "sell_date": d.at[sell_i, "date"].strftime("%Y-%m-%d"),
            "buy_price": float(buy_close),
            "sell_price": float(sell_close),
            "ret": float(ret),
            "prev_close": float(prev_close),
            "high_signal": float(d.at[i, "high"]),
            "high_breakout_pct": float(high_breakout_pct),
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
    avg_loss = float(losses.mean()) if not losses.empty else 0.0
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
    files = [f for f in os.listdir(DATA_DIR) if f.endswith(".parquet")]
    if not files:
        raise FileNotFoundError(f"{DATA_DIR} 下没有 parquet 文件。")

    for year in range(START_YEAR, END_YEAR + 1):
        print(f"\n开始回测 {year} 年：")

        all_trades_next_open = []
        all_trades_next_close = []

        for fn in tqdm(files, desc=f"Backtesting {year}"):
            code_raw = fn.replace(".parquet", "")
            code_p = code_to_pure(code_raw)
            path = os.path.join(DATA_DIR, fn)

            df = read_kline_parquet(path)
            if df.empty:
                continue

            # 保留 year 年数据 + 下一年数据，避免年末信号取不到次日卖出价
            df = df[(df["date"].dt.year == year) | (df["date"].dt.year == year + 1)].reset_index(drop=True)
            if df.empty:
                continue

            # 策略1：当日收盘买，次日开盘卖
            trades_open = compute_trades_buyclose_sellnextopen(df)
            if not trades_open.empty:
                trades_open = trades_open[
                    pd.to_datetime(trades_open["buy_date"]).dt.year == year
                ].reset_index(drop=True)
                if not trades_open.empty:
                    trades_open.insert(0, "code", code_p)
                    trades_open.insert(1, "name", name_map.get(code_raw, ""))
                    all_trades_next_open.append(trades_open)

            # 策略2：当日收盘买，次日收盘卖
            trades_close = compute_trades_buyclose_sellnextclose(df)
            if not trades_close.empty:
                trades_close = trades_close[
                    pd.to_datetime(trades_close["buy_date"]).dt.year == year
                ].reset_index(drop=True)
                if not trades_close.empty:
                    trades_close.insert(0, "code", code_p)
                    trades_close.insert(1, "name", name_map.get(code_raw, ""))
                    all_trades_next_close.append(trades_close)

        # 输出策略1结果
        if all_trades_next_open:
            out_open = pd.concat(all_trades_next_open, ignore_index=True)
            perf_open = summarize_performance(out_open)
            print_performance("策略1：当日收盘买入，次日开盘卖出", perf_open)
        else:
            print("\n策略1：当日收盘买入，次日开盘卖出")
            print(f"{year} 年没有找到满足条件的交易。")

        # 输出策略2结果
        if all_trades_next_close:
            out_close = pd.concat(all_trades_next_close, ignore_index=True)
            perf_close = summarize_performance(out_close)
            print_performance("策略2：当日收盘买入，次日收盘卖出", perf_close)
        else:
            print("\n策略2：当日收盘买入，次日收盘卖出")
            print(f"{year} 年没有找到满足条件的交易。")



if __name__ == "__main__":
    main()
