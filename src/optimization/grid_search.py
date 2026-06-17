import os
import math
import itertools
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List

import numpy as np
import pandas as pd
from tqdm import tqdm

import baostock as bs


# =========================
# 配置区
# =========================
EXCEL_PATH = "results/2025/full_year_backtest_enhanced.xlsx"
SHEET_NAME = "pools_all"
CACHE_DIR = "./bs_cache"          # K线缓存目录（强烈建议开启）
os.makedirs(CACHE_DIR, exist_ok=True)

# 交易成本（可自行调整）
FEE_RATE = 0.0003   # 单边手续费 0.03%（示例）
SLIPPAGE = 0.0000   # 滑点（示例设0）

# 入场规则：用 target_date 的“下一交易日开盘价”作为买入价（更贴近可交易）
ENTRY_MODE = "next_open"  # 可选: "close" / "next_open"


# =========================
# 工具函数
# =========================
def to_bs_code(code: str | int) -> str:
    """把 600xxx / 000xxx 这种转成 baostock 代码：sh.600xxx / sz.000xxx"""
    s = str(code).strip()
    # 防止 excel 里是 582 这种，补齐到6位
    if s.isdigit():
        s = s.zfill(6)
    # 6开头/9开头通常上交所，0/3开头深交所（可按你数据微调）
    if s.startswith(("6", "9")):
        return f"sh.{s}"
    else:
        return f"sz.{s}"


def load_pool(excel_path: str) -> pd.DataFrame:
    df = pd.read_excel(excel_path, sheet_name=SHEET_NAME)
    # reason 里同时包含 C2 和 C3 的记录
    mask = df["reason"].astype(str).str.contains("C2", na=False) & df["reason"].astype(str).str.contains("C3", na=False)
    out = df.loc[mask].copy()
    out["target_date"] = pd.to_datetime(out["target_date"])
    out["bs_code"] = out["code"].apply(to_bs_code)
    return out


def bs_login():
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_code} {lg.error_msg}")


def bs_logout():
    bs.logout()


def fetch_kline_daily(bs_code: str, start: str, end: str, adjustflag: str = "2") -> pd.DataFrame:
    """
    拉取日线并缓存。
    adjustflag: 2=前复权，1=后复权，3=不复权（baostock常用：2）
    字段用 open/high/low/close/volume/amount
    """
    cache_path = os.path.join(CACHE_DIR, f"{bs_code}_{start}_{end}_adj{adjustflag}.parquet")
    if os.path.exists(cache_path):
        return pd.read_parquet(cache_path)

    fields = "date,open,high,low,close,volume,amount,tradestatus,isST"
    rs = bs.query_history_k_data_plus(
        bs_code,
        fields,
        start_date=start,
        end_date=end,
        frequency="d",
        adjustflag=adjustflag
    )
    if rs.error_code != "0":
        raise RuntimeError(f"query_history_k_data_plus failed {bs_code}: {rs.error_code} {rs.error_msg}")

    data_list = []
    while rs.next():
        data_list.append(rs.get_row_data())
    if not data_list:
        return pd.DataFrame()

    df = pd.DataFrame(data_list, columns=fields.split(","))
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # tradestatus=1 可交易
    df["tradestatus"] = pd.to_numeric(df["tradestatus"], errors="coerce")
    df["isST"] = pd.to_numeric(df["isST"], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)

    df.to_parquet(cache_path, index=False)
    return df


def next_trading_row(kdf: pd.DataFrame, dt: pd.Timestamp) -> Optional[int]:
    """返回 dt 之后第一个交易日的行号（含/不含看 ENTRY_MODE 使用）"""
    # 找到 date == dt 的位置
    idx = kdf.index[kdf["date"] == dt]
    if len(idx) == 0:
        # dt 不是交易日：找下一个 > dt
        idx2 = kdf.index[kdf["date"] > dt]
        return int(idx2[0]) if len(idx2) else None
    return int(idx[0])


# =========================
# 回测逻辑
# =========================
@dataclass
class TradeResult:
    bs_code: str
    name: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_px: float
    exit_date: pd.Timestamp
    exit_px: float
    exit_reason: str
    ret: float
    pnl: float  # 这里 pnl = ret（按1单位资金）也行，你可改成乘以资金


def apply_costs(gross_ret: float) -> float:
    # 简化：买卖各一次手续费 + 滑点（可按你的交易模型改）
    cost = 2 * FEE_RATE + 2 * SLIPPAGE
    return gross_ret - cost


def backtest_one_trade(
    kdf: pd.DataFrame,
    bs_code: str,
    name: str,
    signal_date: pd.Timestamp,
    mode: str,
    hold_n: int = 5,
    tp: Optional[float] = None,
    sl: Optional[float] = None,
    trail: Optional[float] = None,
) -> Optional[TradeResult]:
    """
    mode:
      - "hold": 持有N天，到第N天收盘卖
      - "fixed": 固定止盈止损（用日内 high/low 触发，触发则当日按触发价成交）
      - "trail": 移动止损（最高价回撤 trail 触发；也可同时叠加 tp）
    """
    if kdf.empty:
        return None

    i0 = next_trading_row(kdf, signal_date)
    if i0 is None:
        return None

    # 入场
    if ENTRY_MODE == "close":
        entry_i = i0
        entry_px = kdf.loc[entry_i, "close"]
        entry_date = kdf.loc[entry_i, "date"]
        start_i = entry_i + 1
    elif ENTRY_MODE == "next_open":
        entry_i = i0 + 1
        if entry_i >= len(kdf):
            return None
        entry_px = kdf.loc[entry_i, "open"]
        entry_date = kdf.loc[entry_i, "date"]
        start_i = entry_i
    else:
        raise ValueError("ENTRY_MODE must be close or next_open")

    if not np.isfinite(entry_px) or entry_px <= 0:
        return None

    # 遍历持仓窗口
    end_i = min(start_i + hold_n - 1, len(kdf) - 1)

    # 预设：如果没有触发任何规则，按到期日收盘价退出
    default_exit_i = end_i
    default_exit_px = float(kdf.loc[default_exit_i, "close"])
    default_exit_date = kdf.loc[default_exit_i, "date"]
    default_exit_reason = f"hold_{hold_n}d_close"

    # 固定止盈止损 / 移动止损 使用日内 high/low 模拟触发（不做更细分钟级）
    max_since_entry = entry_px

    for i in range(start_i, end_i + 1):
        o = float(kdf.loc[i, "open"])
        h = float(kdf.loc[i, "high"])
        l = float(kdf.loc[i, "low"])
        c = float(kdf.loc[i, "close"])
        d = kdf.loc[i, "date"]

        # 更新最高价（用于移动止损）
        if np.isfinite(h):
            max_since_entry = max(max_since_entry, h)

        if mode == "hold":
            continue

        # 固定止盈止损
        if mode == "fixed":
            # 先判断止损还是止盈的先后问题：日线无法知道先触发哪个
            # 保守做法：对你更不利的先触发（避免高估）
            # 你也可以改成：先止盈、或按开盘价方向判断。
            hit_tp = (tp is not None) and (h >= entry_px * (1 + tp))
            hit_sl = (sl is not None) and (l <= entry_px * (1 - sl))

            if hit_tp and hit_sl:
                # 同日同时触发：保守取更差结果（通常对多头更差是先止损）
                exit_px = entry_px * (1 - sl)
                return TradeResult(bs_code, name, signal_date, entry_date, entry_px, d, exit_px,
                                   "both_hit->sl_first", apply_costs(exit_px / entry_px - 1), apply_costs(exit_px / entry_px - 1))
            if hit_sl:
                exit_px = entry_px * (1 - sl)
                return TradeResult(bs_code, name, signal_date, entry_date, entry_px, d, exit_px,
                                   "stop_loss", apply_costs(exit_px / entry_px - 1), apply_costs(exit_px / entry_px - 1))
            if hit_tp:
                exit_px = entry_px * (1 + tp)
                return TradeResult(bs_code, name, signal_date, entry_date, entry_px, d, exit_px,
                                   "take_profit", apply_costs(exit_px / entry_px - 1), apply_costs(exit_px / entry_px - 1))

        # 移动止损（可叠加tp）
        if mode == "trail":
            # 先看止盈（可选）
            if tp is not None and h >= entry_px * (1 + tp):
                exit_px = entry_px * (1 + tp)
                return TradeResult(bs_code, name, signal_date, entry_date, entry_px, d, exit_px,
                                   "take_profit", apply_costs(exit_px / entry_px - 1), apply_costs(exit_px / entry_px - 1))

            if trail is not None:
                trail_stop_px = max_since_entry * (1 - trail)
                if l <= trail_stop_px:
                    # 同样保守：按止损触发价成交
                    exit_px = trail_stop_px
                    return TradeResult(bs_code, name, signal_date, entry_date, entry_px, d, exit_px,
                                       "trailing_stop", apply_costs(exit_px / entry_px - 1), apply_costs(exit_px / entry_px - 1))

    # 没触发 → 到期退出
    gross_ret = default_exit_px / entry_px - 1
    net_ret = apply_costs(gross_ret)
    return TradeResult(bs_code, name, signal_date, entry_date, entry_px, default_exit_date, default_exit_px,
                       default_exit_reason, net_ret, net_ret)


def backtest_pool(
    pool: pd.DataFrame,
    mode: str,
    hold_n: int,
    tp: Optional[float],
    sl: Optional[float],
    trail: Optional[float],
    adjustflag: str = "2"
) -> pd.DataFrame:
    """
    返回每一笔交易明细
    """
    # 为了拿到未来N天，需要把end往后扩展
    min_date = pool["target_date"].min().strftime("%Y-%m-%d")
    max_date = (pool["target_date"].max() + pd.Timedelta(days=60)).strftime("%Y-%m-%d")

    trade_rows: List[TradeResult] = []

    # 按股票分组拉数据（缓存后非常快）
    for bs_code, g in tqdm(pool.groupby("bs_code"), desc=f"Backtest {mode}"):
        kdf = fetch_kline_daily(bs_code, min_date, max_date, adjustflag=adjustflag)
        if kdf.empty:
            continue

        for _, r in g.iterrows():
            tr = backtest_one_trade(
                kdf=kdf,
                bs_code=bs_code,
                name=str(r.get("name", "")),
                signal_date=pd.to_datetime(r["target_date"]),
                mode=mode,
                hold_n=hold_n,
                tp=tp,
                sl=sl,
                trail=trail
            )
            if tr is not None:
                trade_rows.append(tr)

    if not trade_rows:
        return pd.DataFrame()

    out = pd.DataFrame([t.__dict__ for t in trade_rows])
    return out


def summarize(trades: pd.DataFrame) -> Dict[str, float]:
    if trades.empty:
        return {"n": 0}

    rets = trades["ret"].astype(float)
    win = (rets > 0).mean()

    return {
        "n": int(len(trades)),
        "total_ret": float(rets.sum()),            # 这里是“每笔等权1单位资金”的总和
        "avg_ret": float(rets.mean()),
        "median_ret": float(rets.median()),
        "win_rate": float(win),
        "p95": float(np.percentile(rets, 95)),
        "p05": float(np.percentile(rets, 5)),
        "max_ret": float(rets.max()),
        "min_ret": float(rets.min()),
    }


# =========================
# 网格搜索
# =========================
def grid_search(pool: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    返回：
      - grid结果汇总表
      - 最佳参数下的交易明细表
    """
    # 你可以按需扩展搜索空间
    hold_days_grid = [10, 15, 20, 25, 30]
    tp_grid = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 6]
    sl_grid = [0.05, 0.07, 0.10, 0.15]
    trail_grid = [0.03, 0.05, 0.07, 0.10]

    grid_rows = []
    best_key = None
    best_total = -1e18
    best_trades = None

    # 1) 纯持有N天
    for n in hold_days_grid:
        trades = backtest_pool(pool, mode="hold", hold_n=n, tp=None, sl=None, trail=None)
        stats = summarize(trades)
        row = {"mode": "hold", "hold_n": n, "tp": None, "sl": None, "trail": None, **stats}
        grid_rows.append(row)
        if stats.get("n", 0) > 0 and stats["total_ret"] > best_total:
            best_total = stats["total_ret"]
            best_key = row
            best_trades = trades

    # 2) 固定止盈止损（仍然带一个最大持有N天兜底）
    for n, tp, sl in itertools.product(hold_days_grid, tp_grid, sl_grid):
        trades = backtest_pool(pool, mode="fixed", hold_n=n, tp=tp, sl=sl, trail=None)
        stats = summarize(trades)
        row = {"mode": "fixed", "hold_n": n, "tp": tp, "sl": sl, "trail": None, **stats}
        grid_rows.append(row)
        if stats.get("n", 0) > 0 and stats["total_ret"] > best_total:
            best_total = stats["total_ret"]
            best_key = row
            best_trades = trades

    # 3) 移动止损（可叠加tp，可选：这里也把tp放进网格）
    for n, tp, trail in itertools.product(hold_days_grid, tp_grid, trail_grid):
        trades = backtest_pool(pool, mode="trail", hold_n=n, tp=tp, sl=None, trail=trail)
        stats = summarize(trades)
        row = {"mode": "trail", "hold_n": n, "tp": tp, "sl": None, "trail": trail, **stats}
        grid_rows.append(row)
        if stats.get("n", 0) > 0 and stats["total_ret"] > best_total:
            best_total = stats["total_ret"]
            best_key = row
            best_trades = trades

    grid_df = pd.DataFrame(grid_rows).sort_values(["total_ret", "win_rate"], ascending=[False, False]).reset_index(drop=True)

    # 最佳交易明细：补充“每只股票累计盈亏”
    if best_trades is not None and not best_trades.empty:
        per_stock = best_trades.groupby("bs_code")["ret"].sum().rename("ret_sum_by_stock").reset_index()
        best_trades = best_trades.merge(per_stock, on="bs_code", how="left")

    return grid_df, best_trades


def main():
    pool = load_pool(EXCEL_PATH)
    print(f"Filtered pool size (C2 & C3): {len(pool)}")

    bs_login()
    try:
        grid_df, best_trades = grid_search(pool)
    finally:
        bs_logout()

    # 输出结果
    os.makedirs("results/2025", exist_ok=True)
    grid_df.to_csv("results/2025/grid_results.csv", index=False, encoding="utf-8-sig")
    if best_trades is not None:
        best_trades.to_csv("results/2025/grid_best_trades.csv", index=False, encoding="utf-8-sig")

    print("\nTop 10 parameter sets:")
    print(grid_df.head(10))

    if best_trades is not None and not best_trades.empty:
        best_row = grid_df.iloc[0].to_dict()
        print("\nBest params:", best_row)
        print("Saved: results/2025/grid_results.csv, results/2025/grid_best_trades.csv")


if __name__ == "__main__":
    main()
