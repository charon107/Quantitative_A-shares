"""财报季每日增量抓取（runner 侧）：按披露日取当日名单，只补名单内股票。

三类披露来源（受代理能力约束，见各 fetcher 注）：
  - 业绩预告：forecast 支持 ann_date 按日全市场查询，单次直取（零逐股调用）
  - 正式财报：代理不支持 income/fina_indicator 的 ann_date 按日查询，改走
    disclosure_date（披露计划表）按 actual_date 精确查询（含 2 天回溯尾，
    吸收披露日与代理数据入库的时间差），名单内股票逐只补四张报表 raw
    （每股 7 次调用，与全量 _fetch_one 相同）
  - 业绩快报：代理仅支持逐股查询。--express-sweep 开启时全主板逐股扫描
    （快报非强制披露、集中在 1-2 月 / 7-8 月，workflow 只在这两个窗口开启；
    约 3200 次调用 @90次/分 ≈ 36 分钟）

产出文件名与全量模式一致（fundamental.parquet / fundamental_quarterly.parquet /
dividend.parquet / forecast.parquet / express.parquet），服务器侧
load_fundamentals.py --incremental 只做 upsert（跳过逐年选股与退市清理）。
预告/快报产物 = 按日/扫描行 + 名单内股票全历史行，合并去重后 upsert（旧行不动）。

用法（runner）：
  uv run --no-project --with tushare --with pandas --with pyarrow --with numpy \
    python scripts/fetch_earnings_daily.py --outdir artifacts_fund --date 2026-07-24 \
    --express-sweep
--date 缺省为北京时间当天。需环境变量 TUSHARE_TOKEN / TUSHARE_API_URL。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from src.data_collection import tushare_client as tsc  # noqa: E402
# 复用全量脚本的单股抓取（7 次调用产出五种产物）、主板名单与限流器状态类，避免逻辑漂移
from fetch_fundamentals_parquet import _NextAllowed, _fetch_one, _mainboard_codes  # noqa: E402

# 并发线程数：只用于隐藏网络往返延迟，总速率由 tushare_client 的全局限速器
# 钉死在 MAX_CALLS_PER_MIN*0.9（默认 90 次/分），并发数再大也不会超限频。
WORKERS = int(os.environ.get("FETCH_EARNINGS_WORKERS", "8"))


def _beijing_today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _run_pool(label: str, codes: list[str], fn) -> list:
    """并发执行 fn(code)，单只失败跳过。返回结果列表（顺序不保证）。"""
    results = []
    n_done = 0
    start_ts = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(fn, c): c for c in codes}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # 单只失败不致命，跳过继续
                print(f"[fetch_earnings] {label} {code} 失败：{e}")
            n_done += 1
            if n_done % 200 == 0 or n_done == len(codes):
                rate = n_done / max(time.time() - start_ts, 1e-9)
                eta_min = (len(codes) - n_done) / max(rate, 1e-9) / 60
                print(
                    f"[fetch_earnings] {label} 进度 {n_done}/{len(codes)}，预计剩余 {eta_min:.0f} 分钟",
                    flush=True,
                )
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="artifacts_fund")
    ap.add_argument("--date", default="", help="披露日 YYYY-MM-DD（缺省=北京时间当天）")
    ap.add_argument("--express-sweep", action="store_true",
                    help="全主板逐股扫描业绩快报（快报密集窗口开启；代理不支持按日查询）")
    ap.add_argument("--limit", type=int, default=0, help="调试：正式财报名单只抓前 N 只（0=全部）")
    args = ap.parse_args()
    date = args.date or _beijing_today()
    od = args.outdir
    os.makedirs(od, exist_ok=True)

    # 全局限速器：所有线程共享同一个"下一次允许调用"时间戳，总速率恒为 90 次/分
    tsc.configure_rate_limiter(threading.Lock(), _NextAllowed())

    # 1) 业绩预告：按公告日单次全市场查询
    forecast_day = tsc.fetch_forecast_by_date(date)
    print(f"[fetch_earnings] {date} 预告 {len(forecast_day)} 行", flush=True)

    # 2) 正式财报：disclosure_date 按 actual_date 精确查询（含 2 天回溯尾）
    report_codes = tsc.fetch_disclosed_report_codes(date, tail_days=2)
    if args.limit:
        report_codes = report_codes[: args.limit]
    print(f"[fetch_earnings] {date} 正式财报（含回溯尾）{len(report_codes)} 只", flush=True)
    if not report_codes:
        # 交易日却拿不到任何正式财报名单，通常是上游名单停更而非当日真无披露，显式告警
        try:
            is_trading_day = bool(tsc.fetch_trade_dates(date, date))
        except Exception:
            is_trading_day = False
        if is_trading_day:
            print(
                f"[fetch_earnings] 警告：{date} 为交易日但正式财报名单为空（含回溯 {tail_days} 天）。"
                f"若非财报季空窗，请检查代理 disclosure_date(actual_date=...) 是否停更。",
                file=sys.stderr,
                flush=True,
            )

    per_stock: dict[str, list[pd.DataFrame]] = {
        k: [] for k in ("annual", "quarterly", "dividend", "forecast", "express")
    }
    for frames in _run_pool("正式财报", report_codes, _fetch_one):
        for key in per_stock:
            per_stock[key].append(frames[key])

    # 3) 业绩快报：可选的全主板逐股扫描（快报密集窗口由 workflow 开启）
    sweep_express: list[pd.DataFrame] = []
    if args.express_sweep:
        codes = _mainboard_codes()
        print(f"[fetch_earnings] 快报逐股扫描 {len(codes)} 只", flush=True)
        sweep_express = _run_pool("快报扫描", codes, tsc.fetch_express)

    # 4) 合并落盘（文件名与全量模式一致；预告/快报 = 按日行/扫描行 + 名单内全历史行，去重）
    forecast = _concat([forecast_day, *per_stock["forecast"]])
    if not forecast.empty:
        forecast = forecast.drop_duplicates(["code", "end_date", "ann_date"], keep="last")
    express = _concat([*sweep_express, *per_stock["express"]])
    if not express.empty:
        express = express.drop_duplicates(["code", "end_date"], keep="last")

    outputs = {
        "fundamental.parquet": _concat(per_stock["annual"]),
        "fundamental_quarterly.parquet": _concat(per_stock["quarterly"]),
        "dividend.parquet": _concat(per_stock["dividend"]),
        "forecast.parquet": forecast,
        "express.parquet": express,
    }
    for name, frame in outputs.items():
        frame.to_parquet(os.path.join(od, name), index=False)

    summary = " ".join(f"{name.split('.')[0]}={len(frame)}" for name, frame in outputs.items())
    print(f"[fetch_earnings] {date} {summary} -> {os.path.abspath(od)}")


if __name__ == "__main__":
    main()
