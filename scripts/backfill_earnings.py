"""财报季回补脚本：对 [start, end] 逐日取披露名单，并集去重后逐股补正式财报。

一次性回补因"每日财报名单失效"漏抓的正式财报（例：2026-07-25 ~ 2026-08-13）。
复用 fetch_earnings_daily 的单股抓取（_fetch_one，每股 7 次调用）与全局限速/
并发，产出文件名与全量/每日模式一致，服务器侧 load_fundamentals.py --incremental
只做 upsert（旧行不动），幂等。

与 fetch_earnings_daily 的差异：按披露日 tail_days=0 精确取名单（回补区间已覆盖
全部披露日，无需回溯尾），跨日并集去重后每股只抓一次，避免多日回溯重复抓取。

用法（runner）：
  uv run --no-project --with tushare --with pandas --with pyarrow --with numpy \
    python scripts/backfill_earnings.py --outdir artifacts_fund \
    --start 2026-07-25 --end 2026-08-13
需环境变量 TUSHARE_TOKEN / TUSHARE_API_URL。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from datetime import datetime, timedelta

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from src.data_collection import tushare_client as tsc  # noqa: E402
from fetch_earnings_daily import _concat, _run_pool, _fetch_one  # noqa: E402
from fetch_fundamentals_parquet import _NextAllowed  # noqa: E402


def _iter_dates(start: str, end: str):
    d = datetime.strptime(start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d")
    while d <= stop:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="artifacts_fund")
    ap.add_argument("--start", required=True, help="回补起始披露日 YYYY-MM-DD（含）")
    ap.add_argument("--end", required=True, help="回补结束披露日 YYYY-MM-DD（含）")
    ap.add_argument("--limit", type=int, default=0, help="调试：只抓前 N 只（0=全部）")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    tsc.configure_rate_limiter(threading.Lock(), _NextAllowed())

    # 逐日取名单（tail_days=0：回补区间已覆盖全部披露日），跨日并集去重
    codes: set[str] = set()
    for d in _iter_dates(args.start, args.end):
        codes.update(tsc.fetch_disclosed_report_codes(d, tail_days=0))
    codes = sorted(codes)
    print(f"[backfill] {args.start} ~ {args.end} 正式财报名单（去重）{len(codes)} 只", flush=True)
    if args.limit:
        codes = codes[: args.limit]

    per_stock: dict[str, list[pd.DataFrame]] = {
        k: [] for k in ("annual", "quarterly", "dividend", "forecast", "express")
    }
    for frames in _run_pool("回补正式财报", codes, _fetch_one):
        for key in per_stock:
            per_stock[key].append(frames[key])

    outputs = {
        "fundamental.parquet": _concat(per_stock["annual"]),
        "fundamental_quarterly.parquet": _concat(per_stock["quarterly"]),
        "dividend.parquet": _concat(per_stock["dividend"]),
        "forecast.parquet": _concat(per_stock["forecast"]),
        "express.parquet": _concat(per_stock["express"]),
    }
    for name, frame in outputs.items():
        frame.to_parquet(os.path.join(args.outdir, name), index=False)

    summary = " ".join(f"{name.split('.')[0]}={len(frame)}" for name, frame in outputs.items())
    print(f"[backfill] {args.start} ~ {args.end} {summary} -> {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()
