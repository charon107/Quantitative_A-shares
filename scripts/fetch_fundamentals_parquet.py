"""在 runner（境外，直连 tushare 代理稳定）抓取基本面财务 + 指数日线，存为 parquet。

随后由 workflow scp 到服务器，服务器用 load_fundamentals.py 只做 DB 活（入库 + 跑选股）。
财务数据按年报口径逐股抓取（fina_indicator 一次拿全部报告期），变动频率低，
默认一次性全历史回填；财报季用 --years 增量刷新最近若干年即可。

产出（--outdir 下）：
  fundamental.parquet  全市场年报财务（code/year/ann_date/roe/netprofit_yoy/
                       debt_ratio/net_profit/cfo；debt_ratio=总债务/总资产，
                       总债务=短期借款+长期借款+应付债券）
  index.parquet        指数日线收盘（默认上证综指 sh.000001；code/date/close）

用法（runner）：
  uv run --no-project --with tushare --with pandas --with pyarrow --with numpy \
    python scripts/fetch_fundamentals_parquet.py --outdir artifacts_fund --full
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

import pandas as pd

try:
    from tqdm import tqdm
except ImportError:  # runner 环境未装 tqdm 时退化为普通循环
    tqdm = None

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.data_collection import tushare_client as tsc  # noqa: E402

_MB = re.compile(r"^(sh\.60|sz\.00)\d{4}$")
INDEX_CODE = "sh.000001"

# 并发线程数：只用于隐藏网络往返延迟，总速率由 tushare_client 的全局限速器
# 钉死在 MAX_CALLS_PER_MIN*0.9（默认 90 次/分），并发数再大也不会超限频。
WORKERS = int(os.environ.get("FETCH_FUND_WORKERS", "8"))


class _NextAllowed:
    """threading 版的 next_allowed 状态（鸭子类型兼容 multiprocessing.Value 的 .value）。"""

    def __init__(self) -> None:
        self.value = 0.0


def _mainboard_codes() -> list[str]:
    basic = tsc.fetch_stock_basic()
    codes = basic["code"].astype(str)
    return codes[codes.str.match(_MB, na=False)].tolist()


def _fetch_one(code: str) -> pd.DataFrame:
    """拼装单只股票的年报财务宽表（fina/income/cashflow/balancesheet 四表按 year 外连接，
    fina 缺失年份用报表推算，见 assemble_annual_fundamental）。"""
    fina = tsc.fetch_fina_indicator(code)
    inc = tsc.fetch_income(code)
    cf = tsc.fetch_cashflow(code)
    bal = tsc.fetch_balancesheet(code)
    return tsc.assemble_annual_fundamental(code, fina, inc, cf, bal)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="artifacts_fund")
    ap.add_argument("--full", action="store_true", help="全历史回填（默认）")
    ap.add_argument("--years", type=int, default=0, help="只保留最近 N 年（增量刷新用；0=全部）")
    ap.add_argument("--limit", type=int, default=0, help="调试：只抓前 N 只股票（0=全部）")
    args = ap.parse_args()
    od = args.outdir
    os.makedirs(od, exist_ok=True)

    codes = _mainboard_codes()
    if args.limit:
        codes = codes[: args.limit]
    total = len(codes)
    print(f"[fetch_fund] 主板股票 {total} 只")

    # 逐股落盘到 parts/，进程中断后重跑可跳过已抓的（断点续传）
    parts_dir = os.path.join(od, "parts")
    os.makedirs(parts_dir, exist_ok=True)
    done = {fn[:-8] for fn in os.listdir(parts_dir) if fn.endswith(".parquet")}
    if done:
        print(f"[fetch_fund] 续传：已有 {len(done)} 只，跳过")

    # 全局限速器：所有线程共享同一个"下一次允许调用"时间戳，总速率恒为 90 次/分
    tsc.configure_rate_limiter(threading.Lock(), _NextAllowed())

    def _fetch_and_save(code: str) -> None:
        df = _fetch_one(code)
        # 空结果也占位，避免重跑时反复请求无数据的股票
        out = df if not df.empty else pd.DataFrame()
        out.to_parquet(os.path.join(parts_dir, f"{code}.parquet"), index=False)

    todo = [c for c in codes if c not in done]
    print(f"[fetch_fund] 并发 {WORKERS} 线程，全局限速 {tsc.MAX_CALLS_PER_MIN * 0.9:.0f} 次/分")
    bar = tqdm(total=len(todo), desc="[fetch_fund] 抓取", unit="只") if tqdm else None
    n_done = 0
    start_ts = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_fetch_and_save, c): c for c in todo}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                fut.result()
            except Exception as e:  # 单只失败不致命，跳过继续
                print(f"[fetch_fund] {code} 失败：{e}")
            n_done += 1
            if bar:
                bar.update(1)
            elif n_done % 20 == 0 or n_done == len(todo):
                rate = n_done / max(time.time() - start_ts, 1e-9)  # 只/秒
                eta_min = (len(todo) - n_done) / max(rate, 1e-9) / 60
                print(
                    f"[fetch_fund] 进度 {n_done}/{len(todo)} "
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
    fund = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["code", "year", "ann_date", "roe", "netprofit_yoy", "debt_ratio", "net_profit", "cfo"]
    )
    if args.years and not fund.empty:
        max_year = int(pd.to_numeric(fund["year"], errors="coerce").max())
        fund = fund[fund["year"] >= max_year - args.years + 1]
    fund.to_parquet(f"{od}/fundamental.parquet", index=False)

    # 指数日线（全历史）
    try:
        idx = tsc.fetch_index_daily(INDEX_CODE)
    except Exception as e:
        print(f"[fetch_fund] index 失败：{e}")
        idx = pd.DataFrame(columns=["code", "date", "close"])
    idx.to_parquet(f"{od}/index.parquet", index=False)

    print(f"[fetch_fund] fundamental={len(fund)} index={len(idx)} -> {os.path.abspath(od)}")


if __name__ == "__main__":
    main()
