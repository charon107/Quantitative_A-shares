"""
统一封装 tushare 访问：token 配置、code 格式互转、重试/限流、永久性错误熔断，
以及业务层 fetch 函数（股票列表 / 原始日线 / 复权因子 / 前复权K线 / 交易日历）。

返回的列名尽量贴近原来 baostock 的命名习惯（code 用 "sh.600000" 风格，
列名沿用 code/code_name/date/pctChg/turn/adjustflag），让调用方迁移成本最低，
也不破坏现有本地 parquet 文件按 "sh.600000" 命名的约定。
"""
import os
import re
import time
import random

import pandas as pd
import tushare as ts

# 个人 tushare token（去 tushare.pro 注册获取，或第三方代理分配的 token），
# 由调用方自行配置环境变量
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# 第三方代理的 API 地址（留空则用 tushare 官方默认地址 http://api.tushare.pro）。
# 部分代理服务为了控量会分配独立 token + 独立网关地址，需要覆盖
# DataApi 实例的私有属性 __http_url（Python 名称改写后是 _DataApi__http_url）。
TUSHARE_API_URL = os.environ.get("TUSHARE_API_URL", "")

# 重试配置：瞬时错误（网络波动/限流）指数退避重试
MAX_RETRIES = int(os.environ.get("TUSHARE_MAX_RETRIES", "6"))
BACKOFF_BASE = float(os.environ.get("TUSHARE_BACKOFF_BASE", "1.6"))
BACKOFF_CAP = float(os.environ.get("TUSHARE_BACKOFF_CAP", "120"))

# 单进程场景下没有跨进程限流器时的退化节流（随机小延迟）
MIN_PACE = float(os.environ.get("TUSHARE_MIN_PACE", "0.2"))
MAX_PACE = float(os.environ.get("TUSHARE_MAX_PACE", "0.4"))

# 账号限频：每分钟最多调用次数。多进程场景下用跨进程共享的全局间隔强制
# 限速（不是每个进程各自独立限速，那样并发越多总速率越超标）。留 10% 余量。
MAX_CALLS_PER_MIN = float(os.environ.get("TUSHARE_MAX_CALLS_PER_MIN", "100"))
MIN_CALL_INTERVAL = 60.0 / (MAX_CALLS_PER_MIN * 0.9)

# 命中这些关键字视为永久性错误（token 无效/权限不足/积分不够等），重试无意义，立即熔断；
# 限流类报错（"频率"/"每分钟"）不在此列，仍走指数退避重试。
FATAL_KEYWORDS = ("token", "权限", "积分")


class TushareFatalError(RuntimeError):
    """tushare 返回永久性错误（token 无效/权限不足/积分不够等），重试无意义，需立即熔断。"""


_pro_client = None

# 跨进程共享的限流锁/状态（由 configure_rate_limiter 注入；未注入时退化为
# 单进程随机延迟，见 _throttle）。
_rate_lock = None
_rate_next_allowed = None  # multiprocessing.Value('d', ...)：下一次允许调用的时间戳


def configure_rate_limiter(lock, next_allowed):
    """多进程场景下注入跨进程共享的限流锁 + 状态，让所有 worker 共用同一个全局速率。

    lock/next_allowed 应为 multiprocessing.Lock()/multiprocessing.Value('d', 0.0)，
    通过 Pool 的 initializer/initargs 传给每个 worker 进程（创建方式见
    stock_price.py 的 main()）。
    """
    global _rate_lock, _rate_next_allowed
    _rate_lock = lock
    _rate_next_allowed = next_allowed


def _pro():
    """懒加载的 tushare pro 客户端单例（纯 HTTP 客户端，无需登录/登出）。"""
    global _pro_client
    if _pro_client is None:
        if not TUSHARE_TOKEN:
            raise RuntimeError("环境变量 TUSHARE_TOKEN 未配置，无法调用 tushare。")
        ts.set_token(TUSHARE_TOKEN)
        _pro_client = ts.pro_api()
        if TUSHARE_API_URL:
            _pro_client._DataApi__http_url = TUSHARE_API_URL
    return _pro_client


def _throttle():
    """请求节流：配置了跨进程限流器时强制全局间隔，否则退化为单进程随机延迟。"""
    if _rate_lock is None or _rate_next_allowed is None:
        time.sleep(random.uniform(MIN_PACE, MAX_PACE))
        return
    with _rate_lock:
        now = time.time()
        wait = _rate_next_allowed.value - now
        _rate_next_allowed.value = max(now, _rate_next_allowed.value) + MIN_CALL_INTERVAL
    if wait > 0:
        time.sleep(wait)


def _to_ts_code(code: str) -> str:
    """"sh.600000" -> "600000.SH"；"sz.000001" -> "000001.SZ"。"""
    m = re.match(r"^(sh|sz|bj)\.(\d{6})$", code)
    if not m:
        raise ValueError(f"无法识别的 code 格式: {code}")
    exch, num = m.group(1), m.group(2)
    return f"{num}.{exch.upper()}"


def _from_ts_code(ts_code: str) -> str:
    """"600000.SH" -> "sh.600000"。"""
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", str(ts_code))
    if not m:
        raise ValueError(f"无法识别的 ts_code 格式: {ts_code}")
    num, exch = m.group(1), m.group(2)
    return f"{exch.lower()}.{num}"


def _to_ts_date(yyyy_mm_dd: str) -> str:
    """"2026-06-25" -> "20260625"；空字符串原样返回（表示不限制/到今天）。"""
    if not yyyy_mm_dd:
        return ""
    return yyyy_mm_dd.replace("-", "")


def _call_with_retry(label: str, fn, *args, **kwargs):
    """统一的重试 + 请求节流 + 永久性错误熔断包装。"""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _throttle()
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in FATAL_KEYWORDS):
                raise TushareFatalError(msg) from e
            last_err = e
            if attempt < MAX_RETRIES:
                wait = min(BACKOFF_CAP, (BACKOFF_BASE ** (attempt - 1)) + random.uniform(0, 1.5))
                print(f"[Retry {attempt}/{MAX_RETRIES}] {label}, wait {wait:.1f}s: {e}")
                time.sleep(wait)
    raise RuntimeError(f"{label} failed after {MAX_RETRIES} retries: {last_err}")


def fetch_stock_basic() -> pd.DataFrame:
    """获取全市场上市股票列表，返回 code（sh.600000 风格）/ code_name 两列。"""
    df = _call_with_retry(
        "fetch_stock_basic",
        _pro().stock_basic,
        exchange="", list_status="L",
        fields="ts_code,symbol,name",
    )
    if df is None or df.empty:
        raise RuntimeError("stock_basic returned empty DataFrame.")
    df = df.copy()
    df["code"] = df["ts_code"].apply(_from_ts_code)
    df = df.rename(columns={"name": "code_name"})
    return df[["code", "code_name"]]


def fetch_daily_raw(code: str, start_date: str, end_date: str = "") -> pd.DataFrame:
    """拉取未复权日线原始价：date/open/high/low/close/volume/amount/pctChg。"""
    ts_code = _to_ts_code(code)
    df = _call_with_retry(
        f"fetch_daily_raw({code})",
        _pro().daily,
        ts_code=ts_code,
        start_date=_to_ts_date(start_date),
        end_date=_to_ts_date(end_date),
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    df = df.rename(columns={"vol": "volume", "pct_chg": "pctChg"})
    for col in ["open", "high", "low", "close", "volume", "amount", "pctChg"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date"])
    df = df.sort_values(["date"]).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume", "amount", "pctChg"]]


def fetch_turnover(code: str, start_date: str, end_date: str = "") -> pd.DataFrame:
    """拉取换手率：date/turn（百分比）。"""
    ts_code = _to_ts_code(code)
    df = _call_with_retry(
        f"fetch_turnover({code})",
        _pro().daily_basic,
        ts_code=ts_code,
        start_date=_to_ts_date(start_date),
        end_date=_to_ts_date(end_date),
        fields="trade_date,turnover_rate",
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    df["turn"] = pd.to_numeric(df["turnover_rate"], errors="coerce")
    df = df.dropna(subset=["date"])
    return df[["date", "turn"]].drop_duplicates(subset=["date"], keep="last")


def fetch_adj_factor_series(code: str, start_date: str, end_date: str = "") -> pd.DataFrame:
    """拉取复权因子序列：code/trade_date/adj_factor（trade_date 为 Timestamp）。"""
    ts_code = _to_ts_code(code)
    df = _call_with_retry(
        f"fetch_adj_factor_series({code})",
        _pro().adj_factor,
        ts_code=ts_code,
        start_date=_to_ts_date(start_date),
        end_date=_to_ts_date(end_date),
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce")
    df["code"] = code
    df = df.dropna(subset=["trade_date"])
    df = df.sort_values(["trade_date"]).drop_duplicates(subset=["trade_date"], keep="last").reset_index(drop=True)
    return df[["code", "trade_date", "adj_factor"]]


def compute_qfq(raw_df: pd.DataFrame, factor_df: pd.DataFrame, code: str) -> pd.DataFrame:
    """
    用未复权日线 + 复权因子计算前复权K线：qfq_price = price * adj_factor / 最新adj_factor。

    "最新" 必须取 factor_df 里覆盖到的最新交易日（要求调用方传入覆盖到"今天"的
    factor_df，而不是只覆盖 raw_df 的日期区间），否则前复权基准会和已有历史数据
    用的基准不一致——前复权语义本身是"相对当前最新一天"，不是"相对查询区间末尾"。
    """
    if raw_df.empty:
        return pd.DataFrame()
    if factor_df.empty:
        raise RuntimeError(f"compute_qfq({code}): 缺少复权因子，无法计算前复权价格。")

    merged = raw_df.merge(
        factor_df[["trade_date", "adj_factor"]].rename(columns={"trade_date": "date"}),
        on="date", how="left",
    )
    merged["adj_factor"] = merged["adj_factor"].ffill().bfill()
    latest_factor = factor_df.sort_values("trade_date")["adj_factor"].iloc[-1]

    out = merged.copy()
    for col in ["open", "high", "low", "close"]:
        out[col] = out[col] * out["adj_factor"] / latest_factor

    out["code"] = code
    out["adjustflag"] = "2"
    cols = ["date", "code", "open", "high", "low", "close", "volume", "amount", "pctChg", "adjustflag"]
    out = out[[c for c in cols if c in out.columns]]
    return out.sort_values(["date"]).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def fetch_kline_qfq(code: str, start_date: str, end_date: str = "", fields: list[str] | None = None) -> pd.DataFrame:
    """
    给只要"现成前复权K线"的脚本用：内部拼好 daily + adj_factor（必要时再加 turn）。

    fields 为空时返回全部列（含 turn）；传入时只在需要 "turn" 才额外查
    daily_basic（换手率不需要复权调整，省一次请求）。
    """
    raw = fetch_daily_raw(code, start_date, end_date)
    if raw.empty:
        return pd.DataFrame()
    # 复权基准固定查到"今天"，不能用 raw 的 end_date（见 compute_qfq 的说明）。
    factor = fetch_adj_factor_series(code, start_date, end_date="")
    df = compute_qfq(raw, factor, code)

    need_turn = fields is None or "turn" in fields
    if need_turn:
        turn = fetch_turnover(code, start_date, end_date)
        if not turn.empty:
            df = df.merge(turn, on="date", how="left")
        else:
            df["turn"] = pd.NA

    if fields:
        keep = [c for c in fields if c in df.columns]
        df = df[keep]
    return df


def fetch_trade_dates(start_date: str, end_date: str) -> list:
    """返回 [start_date, end_date] 区间内的实际交易日（按 cal_date 升序的 Timestamp 列表）。"""
    df = _call_with_retry(
        "fetch_trade_dates",
        _pro().trade_cal,
        exchange="",
        start_date=_to_ts_date(start_date),
        end_date=_to_ts_date(end_date),
    )
    if df is None or df.empty:
        return []
    df = df[df["is_open"].astype(str) == "1"].copy()
    dates = pd.to_datetime(df["cal_date"], format="%Y%m%d", errors="coerce").dropna()
    return sorted(dates.tolist())
