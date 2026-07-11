"""在 runner（境外，直连 tushare 代理稳定）抓全市场估值日频历史，存为 parquet（一次性回填）。

日常增量由每日入库链路维护（fetch_all_parquet.py 产出 valuation_recent.parquet，
与换手率同一 daily_basic 响应零新增调用量）；本脚本只在上线时回填历史，
窗口与 K线一致（KLINE_START_DATE，默认 2013-01-01）。

逐股拉 daily_basic 历史，按 8 年日期窗口分页（防单次返回行数上限），
每股 2 次调用，~3200 只 @90次/分 ≈ 72 分钟；parts_v/ 断点续传。

产出（--outdir 下）：
  valuation.parquet  全市场估值日频（code/date + PE/PB/PS/股息率/市值，
                     列见 tushare_client.VALUATION_METRIC_COLUMNS）

用法（runner）：
  uv run --no-project --with tushare --with pandas --with pyarrow --with numpy \
    python scripts/backfill_valuation_parquet.py --outdir artifacts_val
需环境变量 TUSHARE_TOKEN / TUSHARE_API_URL。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

try:
    from tqdm import tqdm
except ImportError:  # runner 环境未装 tqdm 时退化为普通循环
    tqdm = None

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.config import START_DATE  # noqa: E402
from src.data_collection import tushare_client as tsc  # noqa: E402

_MB = re.compile(r"^(sh\.60|sz\.00)\d{4}$")

# 单次请求的日期窗口跨度（年）。8 年 ≈ 1950 个交易日，远低于 daily_basic
# 单次返回行数上限，防止长历史股票被截断
_WINDOW_YEARS = 8

WORKERS = int(os.environ.get("FETCH_VAL_WORKERS", "8"))


class _NextAllowed:
    """threading 版的 next_allowed 状态（鸭子类型兼容 multiprocessing.Value 的 .value）。"""

    def __init__(self) -> None:
        self.value = 0.0


def _mainboard_codes() -> list[str]:
    basic = tsc.fetch_stock_basic()
    codes = basic["code"].astype(str)
    return codes[codes.str.match(_MB, na=False)].tolist()


def _date_windows(start_date: str) -> list[tuple[str, str]]:
    """[start_date, 今天] 切成 _WINDOW_YEARS 年一段的 (start, end) 列表，末段 end 为空=至今。"""
    start_year = int(start_date[:4])
    this_year = datetime.today().year
    windows = []
    y = start_year
    while y <= this_year:
        w_start = f"{y}-01-01" if y != start_year else start_date
        y_end = y + _WINDOW_YEARS - 1
        w_end = "" if y_end >= this_year else f"{y_end}-12-31"
        windows.append((w_start, w_end))
        y = y_end + 1
    return windows


def _fetch_one(code: str, windows: list[tuple[str, str]]) -> pd.DataFrame:
    """单只股票估值日频历史（分窗口拉取后拼接，drop 换手率列）。"""
    frames = []
    for w_start, w_end in windows:
        df = tsc.fetch_daily_basic_series(code, w_start, w_end)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values("date").drop_duplicates("date", keep="last")
    out["code"] = code
    return out[["code", "date", *tsc.VALUATION_METRIC_COLUMNS]].reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="artifacts_val")
    ap.add_argument("--limit", type=int, default=0, help="调试：只抓前 N 只股票（0=全部）")
    args = ap.parse_args()
    od = args.outdir
    os.makedirs(od, exist_ok=True)

    codes = _mainboard_codes()
    if args.limit:
        codes = codes[: args.limit]
    windows = _date_windows(START_DATE)
    print(f"[backfill_val] 主板股票 {len(codes)} 只，窗口 {windows}")

    parts_dir = os.path.join(od, "parts_v")
    os.makedirs(parts_dir, exist_ok=True)
    done = {fn[:-8] for fn in os.listdir(parts_dir) if fn.endswith(".parquet")}
    if done:
        print(f"[backfill_val] 续传：已有 {len(done)} 只，跳过")

    tsc.configure_rate_limiter(threading.Lock(), _NextAllowed())

    def _fetch_and_save(code: str) -> None:
        df = _fetch_one(code, windows)
        out = df if not df.empty else pd.DataFrame()
        out.to_parquet(os.path.join(parts_dir, f"{code}.parquet"), index=False)

    todo = [c for c in codes if c not in done]
    print(f"[backfill_val] 并发 {WORKERS} 线程，全局限速 {tsc.MAX_CALLS_PER_MIN * 0.9:.0f} 次/分")
    bar = tqdm(total=len(todo), desc="[backfill_val] 抓取", unit="只") if tqdm else None
    n_done = 0
    start_ts = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_fetch_and_save, c): c for c in todo}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                fut.result()
            except Exception as e:  # 单只失败不致命，跳过继续
                print(f"[backfill_val] {code} 失败：{e}")
            n_done += 1
            if bar:
                bar.update(1)
            elif n_done % 20 == 0 or n_done == len(todo):
                rate = n_done / max(time.time() - start_ts, 1e-9)
                eta_min = (len(todo) - n_done) / max(rate, 1e-9) / 60
                print(
                    f"[backfill_val] 进度 {n_done}/{len(todo)} "
                    f"({n_done / len(todo):.1%})，预计剩余 {eta_min:.0f} 分钟",
                    flush=True,
                )
    if bar:
        bar.close()

    frames = []
    for fn in sorted(os.listdir(parts_dir)):
        if fn.endswith(".parquet"):
            part = pd.read_parquet(os.path.join(parts_dir, fn))
            if not part.empty:
                frames.append(part)
    val = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    val.to_parquet(f"{od}/valuation.parquet", index=False)
    print(f"[backfill_val] valuation={len(val)} -> {os.path.abspath(od)}")


if __name__ == "__main__":
    main()
