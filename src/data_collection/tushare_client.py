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

# 导入即触发仓库根 .env 加载（TUSHARE_TOKEN/TUSHARE_API_URL 等补进环境变量）
from src import config as _config  # noqa: F401

# 个人 tushare token（去 tushare.pro 注册获取，或第三方代理分配的 token），
# 由调用方自行配置环境变量（或放在仓库根目录 .env，已 gitignore）
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


def _from_ts_code_batch(df: pd.DataFrame, ts_code_col: str = "ts_code") -> pd.DataFrame:
    """批量转换 ts_code -> code，丢掉不符合 sh/sz/bj 6 位数字格式的行（按日批量
    接口偶尔会混入非普通股票的代码，丢弃比因为一行格式不对就整批报错更稳妥）。"""
    mask = df[ts_code_col].astype(str).str.match(r"^\d{6}\.(SH|SZ|BJ)$")
    df = df[mask].copy()
    df["code"] = df[ts_code_col].apply(_from_ts_code)
    return df


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

    def _fetch_nonempty():
        # 网关抖动时可能返回空结果而非报错，空也视为失败进入重试
        df = _pro().stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name")
        if df is None or df.empty:
            raise RuntimeError("stock_basic returned empty DataFrame.")
        return df

    df = _call_with_retry("fetch_stock_basic", _fetch_nonempty)
    df = df.copy()
    df["code"] = df["ts_code"].apply(_from_ts_code)
    df = df.rename(columns={"name": "code_name"})
    return df[["code", "code_name"]]


def fetch_delisted_codes() -> list[str]:
    """全市场已退市股票 code（sh.600000 风格）。用 list_status='D'。

    显式取已退市清单：即使响应不完整也只是"少删几个"，绝不会误删在市股
    （比"用在市列表取差集"更安全）。
    """
    df = _call_with_retry(
        "fetch_delisted_codes",
        _pro().stock_basic,
        exchange="", list_status="D",
        fields="ts_code,name,delist_date",
    )
    if df is None or df.empty:
        return []
    codes = df["ts_code"].apply(_safe_from_ts_code).dropna()
    return codes.tolist()


def _safe_from_ts_code(ts_code) -> str | None:
    try:
        return _from_ts_code(ts_code)
    except Exception:
        return None


def _fmt_date8(x):
    """'20260626' -> '2026-06-26'；其他返回 None。"""
    return f"{x[:4]}-{x[4:6]}-{x[6:8]}" if isinstance(x, str) and len(x) == 8 and x.isdigit() else None


def fetch_company_info() -> pd.DataFrame:
    """全市场公司信息：stock_basic（扩展字段）+ stock_company 合并，按 code 关联。

    返回列：code/code_name/fullname/area/industry/market/list_date +
            chairman/manager/secretary/reg_capital/setup_date/province/city/
            employees/website/email/office/main_business/introduction/business_scope。
    """
    basic = _call_with_retry(
        "stock_basic_ext",
        _pro().stock_basic,
        exchange="", list_status="L",
        fields="ts_code,name,fullname,area,industry,market,list_date",
    )
    if basic is None or basic.empty:
        raise RuntimeError("stock_basic 返回空。")
    basic = basic.copy()
    basic["code"] = basic["ts_code"].apply(_safe_from_ts_code)
    basic = basic.dropna(subset=["code"]).rename(columns={"name": "code_name"})
    basic["list_date"] = basic["list_date"].apply(_fmt_date8)

    comp_fields = ("ts_code,chairman,manager,secretary,reg_capital,setup_date,"
                   "province,city,introduction,website,email,office,employees,"
                   "main_business,business_scope")
    comps = []
    for exch in ("SSE", "SZSE", "BSE"):
        try:
            c = _call_with_retry(f"stock_company_{exch}", _pro().stock_company,
                                 exchange=exch, fields=comp_fields)
            if c is not None and not c.empty:
                comps.append(c)
        except Exception as e:
            print(f"[fetch_company_info] {exch} 失败，跳过：{e}")
    comp = pd.concat(comps, ignore_index=True) if comps else pd.DataFrame(columns=["ts_code"])
    if not comp.empty:
        comp = comp.copy()
        comp["code"] = comp["ts_code"].apply(_safe_from_ts_code)
        comp = comp.dropna(subset=["code"]).drop(columns=["ts_code"])
        comp["setup_date"] = comp["setup_date"].apply(_fmt_date8)
        comp["reg_capital"] = pd.to_numeric(comp["reg_capital"], errors="coerce")
        comp["employees"] = pd.to_numeric(comp["employees"], errors="coerce").astype("Int64")

    df = basic.merge(comp, on="code", how="left") if not comp.empty else basic
    return df


def fetch_ths_hot(trade_date: str) -> pd.DataFrame:
    """同花顺个股人气榜（data_type=='热股'，筛沪深主板）。

    返回列：code/code_name/rank_no/current_price/pct_change/hot/concept/rank_reason/trade_date。
    """
    df = _call_with_retry("ths_hot", _pro().ths_hot, trade_date=_to_ts_date(trade_date))
    if df is None or df.empty or "data_type" not in df.columns:
        return pd.DataFrame()
    df = df[df["data_type"].astype(str) == "热股"].copy()
    if df.empty:
        return pd.DataFrame()
    df["code"] = df["ts_code"].apply(_safe_from_ts_code)
    df = df.dropna(subset=["code"])
    df = df[df["code"].str.match(r"^(sh\.60|sz\.00)\d{4}$", na=False)]
    if df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"ts_name": "code_name", "rank": "rank_no"})
    for c in ("current_price", "pct_change", "hot"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["rank_no"] = pd.to_numeric(df["rank_no"], errors="coerce").astype("Int64")
    df["trade_date"] = trade_date
    cols = ["code", "code_name", "rank_no", "current_price", "pct_change",
            "hot", "concept", "rank_reason", "trade_date"]
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    return df[cols].sort_values("rank_no").reset_index(drop=True)


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


# daily_basic 的估值指标列（PE/PB/PS/股息率/市值）。与换手率同一响应返回，
# 扩 fields 即可拿到，零新增调用量。total_mv/circ_mv 单位万元，dv_* 为百分数，
# 均按 tushare 原单位入库（前端换算展示）。
VALUATION_METRIC_COLUMNS = (
    "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm", "total_mv", "circ_mv",
)


def fetch_daily_basic_series(code: str, start_date: str, end_date: str = "") -> pd.DataFrame:
    """单只股票每日指标历史（换手率 + 估值）。返回 date/turn + VALUATION_METRIC_COLUMNS。"""
    ts_code = _to_ts_code(code)
    cols = ["date", "turn", *VALUATION_METRIC_COLUMNS]
    df = _call_with_retry(
        f"fetch_daily_basic_series({code})",
        _pro().daily_basic,
        ts_code=ts_code,
        start_date=_to_ts_date(start_date),
        end_date=_to_ts_date(end_date),
        fields="trade_date,turnover_rate," + ",".join(VALUATION_METRIC_COLUMNS),
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    df["turn"] = pd.to_numeric(df["turnover_rate"], errors="coerce")
    for c in VALUATION_METRIC_COLUMNS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.dropna(subset=["date"])
    return df[cols].sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def fetch_turnover(code: str, start_date: str, end_date: str = "") -> pd.DataFrame:
    """拉取换手率：date/turn（百分比）。fetch_daily_basic_series 的薄壳，保持原输出契约。"""
    df = fetch_daily_basic_series(code, start_date, end_date)
    if df.empty:
        return pd.DataFrame()
    return df[["date", "turn"]]


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
    cols = ["date", "code", "open", "high", "low", "close", "volume", "amount", "pctChg", "turn", "adjustflag"]
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


def fetch_daily_by_date(trade_date: str) -> pd.DataFrame:
    """
    一次请求拿全市场某一天的未复权日线（比逐股票查效率高得多——这也是
    tushare/代理官方文档推荐的用法）。

    返回 code/date/open/high/low/close/volume/amount/pctChg，trade_date
    用 "YYYY-MM-DD"。
    """
    df = _call_with_retry(
        f"fetch_daily_by_date({trade_date})",
        _pro().daily,
        trade_date=_to_ts_date(trade_date),
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = _from_ts_code_batch(df)
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    df = df.rename(columns={"vol": "volume", "pct_chg": "pctChg"})
    for col in ["open", "high", "low", "close", "volume", "amount", "pctChg"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date"])
    return df[["code", "date", "open", "high", "low", "close", "volume", "amount", "pctChg"]]


def fetch_adj_factor_by_date(trade_date: str) -> pd.DataFrame:
    """一次请求拿全市场某一天的复权因子。返回 code/trade_date/adj_factor。"""
    df = _call_with_retry(
        f"fetch_adj_factor_by_date({trade_date})",
        _pro().adj_factor,
        trade_date=_to_ts_date(trade_date),
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = _from_ts_code_batch(df)
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce")
    df = df.dropna(subset=["trade_date"])
    return df[["code", "trade_date", "adj_factor"]]


def fetch_daily_basic_by_date(trade_date: str) -> pd.DataFrame:
    """一次请求拿全市场某一天的每日指标（换手率 + 估值）。
    返回 code/date/turn + VALUATION_METRIC_COLUMNS。"""
    cols = ["code", "date", "turn", *VALUATION_METRIC_COLUMNS]
    df = _call_with_retry(
        f"fetch_daily_basic_by_date({trade_date})",
        _pro().daily_basic,
        trade_date=_to_ts_date(trade_date),
        fields="ts_code,trade_date,turnover_rate," + ",".join(VALUATION_METRIC_COLUMNS),
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = _from_ts_code_batch(df)
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    df["turn"] = pd.to_numeric(df["turnover_rate"], errors="coerce")
    for c in VALUATION_METRIC_COLUMNS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.dropna(subset=["date"])
    return df[cols]


def fetch_turnover_by_date(trade_date: str) -> pd.DataFrame:
    """一次请求拿全市场某一天的换手率。返回 code/date/turn。
    fetch_daily_basic_by_date 的薄壳，保持原输出契约。"""
    return fetch_daily_basic_by_date(trade_date)[["code", "date", "turn"]]


def _year_from_end_date(s: pd.Series) -> pd.Series:
    """从 tushare 报告期 end_date（'YYYYMMDD'）取年份 Int64；非年报(非 1231)返回 NA 交由调用方过滤。"""
    end = s.astype(str)
    year = pd.to_numeric(end.str.slice(0, 4), errors="coerce").astype("Int64")
    is_annual = end.str.slice(4, 8) == "1231"
    return year.where(is_annual, other=pd.NA)


def _ann_date_iso(s: pd.Series) -> pd.Series:
    """tushare 公告日 'YYYYMMDD' -> 'YYYY-MM-DD'（无法解析返回 NA）。"""
    return s.astype(str).map(_fmt_date8)


# ============ 财务报表原始报告期帧（全部季度报告期，年度/季度组装共用） ============
# 四个 fetch_*_raw 各发一次 API 请求、保留全部报告期行；fetch_fina_indicator 等
# 年度 fetcher 变为其薄壳（_annual_slice 过滤年报行），输出契约不变。
# 同一响应双喂 assemble_annual_from_raw / assemble_quarterly_fundamental，零新增调用量。

# fina_indicator 中按百分数返回、统一 ÷100 归一为小数的列
_FINA_PCT_COLS = (
    "roe", "netprofit_yoy", "roe_dt", "roa", "netprofit_margin", "grossprofit_margin",
    "debt_to_assets", "or_yoy", "dt_netprofit_yoy",
    "q_roe", "q_dt_roe", "q_netprofit_margin", "q_gsprofit_margin",
    "q_sales_yoy", "q_sales_qoq", "q_netprofit_yoy", "q_netprofit_qoq",
)
# fina_indicator 中的绝对值/每股列（元），仅做数值化
_FINA_ABS_COLS = ("profit_dedt", "eps", "bps")

# 年度组装从各 raw 帧取用的指标列（_annual_slice 用）
_ANNUAL_FINA_COLS = ["roe", "netprofit_yoy"]
_ANNUAL_INCOME_COLS = ["net_profit"]
_ANNUAL_CASHFLOW_COLS = ["cfo"]
_ANNUAL_BALANCE_COLS = ["st_borr", "lt_borr", "bond_payable", "total_assets", "equity"]


def fetch_fina_raw(code: str) -> pd.DataFrame:
    """单只股票 fina_indicator 全部报告期原始帧，一次请求拿全部。

    百分数列已统一 ÷100 转小数（让下游选股阈值常量 0.15 风格不必改）。

    注意：本项目所用代理的 fina_indicator 数据缺失 2006-2011 整段（income/cashflow/
    balancesheet 全历史齐全），缺失报告期由组装函数用三大报表推算补齐。

    返回列：code/end_date('YYYYMMDD')/ann_date + _FINA_PCT_COLS + _FINA_ABS_COLS。
    """
    ts_code = _to_ts_code(code)
    cols = ["code", "end_date", "ann_date", *_FINA_PCT_COLS, *_FINA_ABS_COLS]
    df = _call_with_retry(
        f"fetch_fina_raw({code})",
        _pro().fina_indicator,
        ts_code=ts_code,
        fields="ann_date,end_date," + ",".join(_FINA_PCT_COLS + _FINA_ABS_COLS),
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["end_date"] = df["end_date"].astype(str)
    df["ann_date"] = _ann_date_iso(df["ann_date"])
    for c in _FINA_PCT_COLS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce") / 100.0
    for c in _FINA_ABS_COLS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["code"] = code
    return df[cols].reset_index(drop=True)


def fetch_income_raw(code: str) -> pd.DataFrame:
    """单只股票利润表全部报告期原始帧（金额为年初累计值 YTD）。

    net_profit 归母优先（n_income_attr_p），退回 n_income；revenue/oper_cost
    供季度组装推算净利率/毛利率。返回列：code/end_date/ann_date/net_profit/revenue/oper_cost。
    """
    ts_code = _to_ts_code(code)
    cols = ["code", "end_date", "ann_date", "net_profit", "revenue", "oper_cost"]
    df = _call_with_retry(
        f"fetch_income_raw({code})",
        _pro().income,
        ts_code=ts_code,
        fields="ann_date,end_date,n_income,n_income_attr_p,revenue,oper_cost",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["end_date"] = df["end_date"].astype(str)
    df["ann_date"] = _ann_date_iso(df["ann_date"])
    attr = pd.to_numeric(df.get("n_income_attr_p"), errors="coerce")
    total = pd.to_numeric(df.get("n_income"), errors="coerce")
    df["net_profit"] = attr.fillna(total)
    for c in ("revenue", "oper_cost"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["code"] = code
    return df[cols].reset_index(drop=True)


def fetch_cashflow_raw(code: str) -> pd.DataFrame:
    """单只股票现金流量表全部报告期原始帧（cfo 为年初累计值 YTD）。
    返回列：code/end_date/ann_date/cfo。"""
    ts_code = _to_ts_code(code)
    cols = ["code", "end_date", "ann_date", "cfo"]
    df = _call_with_retry(
        f"fetch_cashflow_raw({code})",
        _pro().cashflow,
        ts_code=ts_code,
        fields="ann_date,end_date,n_cashflow_act",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["end_date"] = df["end_date"].astype(str)
    df["ann_date"] = _ann_date_iso(df["ann_date"])
    df["cfo"] = pd.to_numeric(df.get("n_cashflow_act"), errors="coerce")
    df["code"] = code
    return df[cols].reset_index(drop=True)


def fetch_balancesheet_raw(code: str) -> pd.DataFrame:
    """单只股票资产负债表全部报告期原始帧（均为期末时点值）。

    st_borr/lt_borr/bond_payable 用于算总债务（策略条件3 的有息负债口径），
    equity（归母净资产 total_hldr_eqy_exc_min_int）用于推算平均净资产 ROE，
    total_liab 用于季度表的总负债与资产负债率兜底。
    返回列：code/end_date/ann_date/st_borr/lt_borr/bond_payable/total_assets/total_liab/equity。
    """
    ts_code = _to_ts_code(code)
    cols = ["code", "end_date", "ann_date", "st_borr", "lt_borr", "bond_payable",
            "total_assets", "total_liab", "equity"]
    df = _call_with_retry(
        f"fetch_balancesheet_raw({code})",
        _pro().balancesheet,
        ts_code=ts_code,
        fields="ann_date,end_date,st_borr,lt_borr,bond_payable,total_assets,total_liab,"
               "total_hldr_eqy_exc_min_int",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["end_date"] = df["end_date"].astype(str)
    df["ann_date"] = _ann_date_iso(df["ann_date"])
    df = df.rename(columns={"total_hldr_eqy_exc_min_int": "equity"})
    for c in ("st_borr", "lt_borr", "bond_payable", "total_assets", "total_liab", "equity"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["code"] = code
    return df[cols].reset_index(drop=True)


def _annual_slice(raw: pd.DataFrame, metric_cols: list[str]) -> pd.DataFrame:
    """从原始报告期帧取年报行：提取 year、同年多行按公告日保留最新，缺失指标列补 NA。
    返回列：code/year/ann_date + metric_cols。"""
    cols = ["code", "year", "ann_date", *metric_cols]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=cols)
    df = raw.copy()
    df["year"] = _year_from_end_date(df["end_date"])
    df = df.dropna(subset=["year"])
    for c in metric_cols:
        if c not in df.columns:
            df[c] = pd.NA
    df = df.sort_values(["year", "ann_date"]).drop_duplicates("year", keep="last")
    return df[cols].reset_index(drop=True)


def fetch_fina_indicator(code: str) -> pd.DataFrame:
    """单只股票的财务指标（年报口径）。返回 code/year/ann_date/roe/netprofit_yoy（仅年报行）。"""
    return _annual_slice(fetch_fina_raw(code), _ANNUAL_FINA_COLS)


def fetch_income(code: str) -> pd.DataFrame:
    """单只股票的利润表（年报口径）净利润。返回 code/year/ann_date/net_profit（归母优先，退回 n_income）。"""
    return _annual_slice(fetch_income_raw(code), _ANNUAL_INCOME_COLS)


def fetch_cashflow(code: str) -> pd.DataFrame:
    """单只股票的现金流量表（年报口径）经营活动现金流净额。返回 code/year/ann_date/cfo。"""
    return _annual_slice(fetch_cashflow_raw(code), _ANNUAL_CASHFLOW_COLS)


def fetch_balancesheet(code: str) -> pd.DataFrame:
    """单只股票的资产负债表（年报口径）。
    返回列：code/year/ann_date/st_borr/lt_borr/bond_payable/total_assets/equity。"""
    return _annual_slice(fetch_balancesheet_raw(code), _ANNUAL_BALANCE_COLS)


# assemble_annual_fundamental 产出的指标列（与 db.FUNDAMENTAL_COLUMNS 的指标部分一致）
METRIC_COLUMNS = ("roe", "netprofit_yoy", "debt_ratio", "net_profit", "cfo")


def assemble_annual_fundamental(
    code: str,
    fina: pd.DataFrame,
    inc: pd.DataFrame,
    cf: pd.DataFrame,
    bal: pd.DataFrame,
) -> pd.DataFrame:
    """把单只股票四个接口的年报数据拼成选股面板行（按 year 外连接）。

    指标口径（对应策略规格的三条件）：
      roe            条件1：优先取 fina_indicator.roe；缺失年份用
                     归母净利润 / 平均归母净资产 推算（tushare roe 即平均口径，
                     经 2012+ 重叠年份校准两者误差在千分位内）。
      netprofit_yoy  条件2：优先取 fina_indicator.netprofit_yoy；缺失年份用
                     (净利润 - 上年净利润) / |上年净利润| 推算（要求年份连续）。
      debt_ratio     条件3：总债务(短期借款+长期借款+应付债券)/总资产，
                     全部年份由资产负债表计算（缺项视为 0，无资产负债表行则为 NaN）。

    fina 缺失年份需要推算，是因为代理的 fina_indicator 缺 2006-2011 整段，
    而三大报表全历史齐全（见 fetch_fina_indicator 注）。

    ann_date 四级回退：fina_indicator → income → balancesheet → cashflow，
    确保尽可能多的年份有公告日（用于前端股价图财报竖线）。

    返回列：code/year/ann_date/roe/netprofit_yoy/debt_ratio/net_profit/cfo。
    """
    out_cols = ["code", "year", "ann_date", *METRIC_COLUMNS]

    def _prep(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=["year", *cols])
        available = ["year"] + [c for c in cols if c in df.columns]
        result = df[available].copy()
        for c in cols:
            if c not in df.columns:
                result[c] = pd.NA
        return result

    f = _prep(fina, ["ann_date", "roe", "netprofit_yoy"])
    i = _prep(inc, ["ann_date", "net_profit"]).rename(columns={"ann_date": "ann_date_inc"})
    c = _prep(cf, ["ann_date", "cfo"]).rename(columns={"ann_date": "ann_date_cf"})
    b = _prep(bal, ["ann_date", "st_borr", "lt_borr", "bond_payable", "total_assets", "equity"]).rename(
        columns={"ann_date": "ann_date_bal"}
    )

    df = (
        f.merge(i, on="year", how="outer")
        .merge(c, on="year", how="outer")
        .merge(b, on="year", how="outer")
    )
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df = df.dropna(subset=["year"])
    df["year"] = df["year"].astype(int)
    df = df.sort_values("year").reset_index(drop=True)
    if "ann_date" not in df.columns:
        df["ann_date"] = pd.NA
    if "ann_date_inc" not in df.columns:
        df["ann_date_inc"] = pd.NA
    if "ann_date_bal" not in df.columns:
        df["ann_date_bal"] = pd.NA
    if "ann_date_cf" not in df.columns:
        df["ann_date_cf"] = pd.NA

    # 上一年的值只在年份连续时可用（yoy 与平均净资产都依赖上年）
    consecutive = df["year"].diff().eq(1)
    np_prev = df["net_profit"].shift(1).where(consecutive)
    eq_prev = df["equity"].shift(1).where(consecutive)

    total_debt = (
        df["st_borr"].fillna(0.0) + df["lt_borr"].fillna(0.0) + df["bond_payable"].fillna(0.0)
    )
    df["debt_ratio"] = total_debt / df["total_assets"].where(df["total_assets"] > 0)

    yoy_calc = (df["net_profit"] - np_prev) / np_prev.abs().where(np_prev != 0)
    df["netprofit_yoy"] = df["netprofit_yoy"].fillna(yoy_calc)

    avg_eq = (df["equity"] + eq_prev) / 2
    df["roe"] = df["roe"].fillna(df["net_profit"] / avg_eq.where(avg_eq > 0))

    df["ann_date"] = df["ann_date"].fillna(df["ann_date_inc"]).fillna(df["ann_date_bal"]).fillna(df["ann_date_cf"])
    df["code"] = code
    df = df.dropna(subset=list(METRIC_COLUMNS), how="all")
    return df[out_cols].reset_index(drop=True)


def assemble_annual_from_raw(
    code: str,
    fina_raw: pd.DataFrame,
    inc_raw: pd.DataFrame,
    cf_raw: pd.DataFrame,
    bal_raw: pd.DataFrame,
) -> pd.DataFrame:
    """从原始报告期帧组装年度选股面板。

    等价于「四个年度 fetch_* + assemble_annual_fundamental」但不发请求——
    供 fetch_fundamentals_parquet 用同一批 raw 帧双喂年度/季度组装。
    """
    return assemble_annual_fundamental(
        code,
        _annual_slice(fina_raw, _ANNUAL_FINA_COLS),
        _annual_slice(inc_raw, _ANNUAL_INCOME_COLS),
        _annual_slice(cf_raw, _ANNUAL_CASHFLOW_COLS),
        _annual_slice(bal_raw, _ANNUAL_BALANCE_COLS),
    )


# ============ 季度基本面组装 ============

_MMDD_TO_QUARTER = {"0331": 1, "0630": 2, "0930": 3, "1231": 4}
_QUARTER_END_MMDD = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}

# assemble_quarterly_fundamental 产出的指标列（与 db.QUARTERLY_FUNDAMENTAL_COLUMNS 的指标部分一致）
QUARTERLY_METRIC_COLUMNS = (
    # 累计(YTD)口径；比率为小数，金额为元
    "roe", "roe_dt", "roa", "netprofit_margin", "grossprofit_margin",
    "net_profit", "profit_dedt", "revenue", "eps", "bps", "debt_to_assets",
    "or_yoy", "netprofit_yoy", "dt_netprofit_yoy", "cfo",
    # 期末时点值（total_debt=短借+长借+应付债券，与年度选股 debt_ratio 分子同口径）
    "total_assets", "total_liab", "total_debt",
    # 单季口径
    "q_roe", "q_dt_roe", "q_netprofit_margin", "q_gsprofit_margin",
    "q_net_profit", "q_revenue", "q_cfo",
    "q_sales_yoy", "q_sales_qoq", "q_netprofit_yoy", "q_netprofit_qoq",
)


def _year_quarter_from_end_date(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    """从报告期 end_date('YYYYMMDD')取 (year, quarter) Int64；
    非标准季末日期返回 NA 交由调用方过滤。"""
    end = s.astype(str)
    quarter = end.str.slice(4, 8).map(_MMDD_TO_QUARTER).astype("Int64")
    year = pd.to_numeric(end.str.slice(0, 4), errors="coerce").astype("Int64").where(quarter.notna())
    return year, quarter


def assemble_quarterly_fundamental(
    code: str,
    fina: pd.DataFrame,
    inc: pd.DataFrame,
    cf: pd.DataFrame,
    bal: pd.DataFrame,
) -> pd.DataFrame:
    """把单只股票四个 raw 帧（fetch_*_raw 的输出）拼成季度基本面宽表（按 year+quarter 外连接）。

    口径：
      - 利润表/现金流量表季报为年初累计值(YTD)直接入库；单季值 q_net_profit/q_revenue/
        q_cfo 由相邻累计值差分预计算：Q1=累计；Qn 仅当同年上一季存在才差分，否则 NULL
        （老年份只披露半年报/年报时不造假数据）。资产负债表项为期末时点值，无此问题。
      - 比率优先取 fina_indicator 官方值；缺失（代理缺 2006-2011 整段）用报表推算兜底：
          netprofit_margin = net_profit/revenue
          grossprofit_margin = (revenue-oper_cost)/revenue
          roe(YTD) = net_profit/平均归母净资产（期初=上年末），roa 同理用总资产
          debt_to_assets = total_liab/total_assets
          or_yoy/netprofit_yoy = 与去年同期累计值差分；q_* 同比/环比用单季差分值同法推算
        每股类(eps/bps)与扣非类(roe_dt/q_dt_roe/profit_dedt/dt_netprofit_yoy)无兜底来源，
        缺失保持 NULL（前端断线呈现）。
      - ann_date 四级回退：fina → income → balancesheet → cashflow（同年度组装）。

    返回列：code/end_date('YYYY-MM-DD')/year/quarter/ann_date + QUARTERLY_METRIC_COLUMNS。
    """
    out_cols = ["code", "end_date", "year", "quarter", "ann_date", *QUARTERLY_METRIC_COLUMNS]

    fina_cols = [*_FINA_PCT_COLS, *_FINA_ABS_COLS]
    inc_cols = ["net_profit", "revenue", "oper_cost"]
    cf_cols = ["cfo"]
    bal_cols = ["st_borr", "lt_borr", "bond_payable", "total_assets", "total_liab", "equity"]

    def _prep(df: pd.DataFrame, cols: list[str], ann_name: str) -> pd.DataFrame:
        """提取 year/quarter，同报告期多行（更正公告）按公告日保留最新。"""
        if df is None or df.empty:
            empty = pd.DataFrame(columns=["year", "quarter", ann_name, *cols])
            # merge 键 dtype 须与非空帧一致（object vs Int64 在 pandas 2.x 直接报错）
            empty["year"] = empty["year"].astype("Int64")
            empty["quarter"] = empty["quarter"].astype("Int64")
            return empty
        d = df.copy()
        d["year"], d["quarter"] = _year_quarter_from_end_date(d["end_date"])
        d = d.dropna(subset=["year", "quarter"])
        for c in cols:
            if c not in d.columns:
                d[c] = pd.NA
        if "ann_date" not in d.columns:
            d["ann_date"] = pd.NA
        d = d.sort_values(["year", "quarter", "ann_date"]).drop_duplicates(["year", "quarter"], keep="last")
        d = d.rename(columns={"ann_date": ann_name})
        return d[["year", "quarter", ann_name, *cols]]

    df = (
        _prep(fina, fina_cols, "ann_date")
        .merge(_prep(inc, inc_cols, "ann_date_inc"), on=["year", "quarter"], how="outer")
        .merge(_prep(cf, cf_cols, "ann_date_cf"), on=["year", "quarter"], how="outer")
        .merge(_prep(bal, bal_cols, "ann_date_bal"), on=["year", "quarter"], how="outer")
    )
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["year"] = df["year"].astype(int)
    df["quarter"] = df["quarter"].astype(int)
    df = df.sort_values(["year", "quarter"]).reset_index(drop=True)
    # 空输入帧带来的 object 列统一转数值，保证后续算术/parquet dtype 稳定
    for c in [*fina_cols, *inc_cols, *cf_cols, *bal_cols]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    is_q1 = df["quarter"].eq(1)
    # 同年上一季（YTD 差分基期）；跨年相邻（上年Q4→本年Q1，供单季比率/环比用）
    same_year_prev = df["year"].diff().eq(0) & df["quarter"].diff().eq(1)
    prev_adjacent = (df["year"] * 4 + df["quarter"]).diff().eq(1)

    def _q_diff(ytd: pd.Series) -> pd.Series:
        prev = ytd.shift(1).where(same_year_prev)
        return ytd.where(is_q1, ytd - prev)

    df["q_net_profit"] = _q_diff(df["net_profit"])
    df["q_revenue"] = _q_diff(df["revenue"])
    df["q_cfo"] = _q_diff(df["cfo"])
    q_oper_cost = _q_diff(df["oper_cost"])  # 仅供毛利率兜底，不入库

    # 去年同期值（YoY 兜底基期）：按 (year-1, quarter) 对齐
    prev_y = df[["year", "quarter", "net_profit", "revenue", "q_net_profit", "q_revenue"]].copy()
    prev_y["year"] = prev_y["year"] + 1
    prev_y = prev_y.rename(columns={
        "net_profit": "_np_ly", "revenue": "_rev_ly",
        "q_net_profit": "_qnp_ly", "q_revenue": "_qrev_ly",
    })
    df = df.merge(prev_y, on=["year", "quarter"], how="left")

    def _growth(cur: pd.Series, base: pd.Series) -> pd.Series:
        return (cur - base) / base.abs().where(base != 0)

    def _avg_positive(a: pd.Series, b: pd.Series) -> pd.Series:
        avg = (a + b) / 2
        return avg.where(avg > 0)

    # 总债务（有息口径：短借+长借+应付债券，缺项视 0，与年度 debt_ratio 分子一致；
    # 该报告期无资产负债表行则 NULL，不把"没数据"画成 0）
    has_bal = df[bal_cols].notna().any(axis=1)
    df["total_debt"] = (
        df["st_borr"].fillna(0.0) + df["lt_borr"].fillna(0.0) + df["bond_payable"].fillna(0.0)
    ).where(has_bal)

    # 期初值：YTD 比率的期初 = 上年末（上年Q4）时点值；单季比率的期初 = 上一报告期末
    q4_rows = df[df["quarter"].eq(4)]
    year_begin_equity = (df["year"] - 1).map(q4_rows.set_index("year")["equity"])
    year_begin_assets = (df["year"] - 1).map(q4_rows.set_index("year")["total_assets"])
    prev_q_equity = df["equity"].shift(1).where(prev_adjacent)

    # —— fina 缺失时的报表推算兜底 ——
    rev = df["revenue"].where(df["revenue"] != 0)
    df["netprofit_margin"] = df["netprofit_margin"].fillna(df["net_profit"] / rev)
    df["grossprofit_margin"] = df["grossprofit_margin"].fillna((df["revenue"] - df["oper_cost"]) / rev)
    df["roe"] = df["roe"].fillna(df["net_profit"] / _avg_positive(year_begin_equity, df["equity"]))
    df["roa"] = df["roa"].fillna(df["net_profit"] / _avg_positive(year_begin_assets, df["total_assets"]))
    df["debt_to_assets"] = df["debt_to_assets"].fillna(
        df["total_liab"] / df["total_assets"].where(df["total_assets"] > 0)
    )
    df["netprofit_yoy"] = df["netprofit_yoy"].fillna(_growth(df["net_profit"], df["_np_ly"]))
    df["or_yoy"] = df["or_yoy"].fillna(_growth(df["revenue"], df["_rev_ly"]))

    qrev = df["q_revenue"].where(df["q_revenue"] != 0)
    df["q_netprofit_margin"] = df["q_netprofit_margin"].fillna(df["q_net_profit"] / qrev)
    df["q_gsprofit_margin"] = df["q_gsprofit_margin"].fillna((df["q_revenue"] - q_oper_cost) / qrev)
    df["q_roe"] = df["q_roe"].fillna(df["q_net_profit"] / _avg_positive(prev_q_equity, df["equity"]))
    df["q_netprofit_yoy"] = df["q_netprofit_yoy"].fillna(_growth(df["q_net_profit"], df["_qnp_ly"]))
    df["q_sales_yoy"] = df["q_sales_yoy"].fillna(_growth(df["q_revenue"], df["_qrev_ly"]))
    df["q_netprofit_qoq"] = df["q_netprofit_qoq"].fillna(
        _growth(df["q_net_profit"], df["q_net_profit"].shift(1).where(prev_adjacent))
    )
    df["q_sales_qoq"] = df["q_sales_qoq"].fillna(
        _growth(df["q_revenue"], df["q_revenue"].shift(1).where(prev_adjacent))
    )

    df["ann_date"] = df["ann_date"].fillna(df["ann_date_inc"]).fillna(df["ann_date_bal"]).fillna(df["ann_date_cf"])
    df["end_date"] = df["year"].astype(str) + "-" + df["quarter"].map(_QUARTER_END_MMDD)
    df["code"] = code
    df = df.dropna(subset=list(QUARTERLY_METRIC_COLUMNS), how="all")
    return df[out_cols].reset_index(drop=True)


# ============ 分红送股 / 业绩预告 / 业绩快报 ============

# 三张表的指标列（与 db 侧列常量的指标部分一致）
DIVIDEND_COLUMNS = (
    "ann_date", "div_proc", "stk_div", "cash_div", "cash_div_tax",
    "record_date", "ex_date", "pay_date",
)
FORECAST_COLUMNS = (
    "type", "p_change_min", "p_change_max", "net_profit_min", "net_profit_max", "change_reason",
)
EXPRESS_COLUMNS = (
    "revenue", "operate_profit", "total_profit", "n_income",
    "diluted_eps", "bps", "diluted_roe", "yoy_sales", "yoy_op", "yoy_dedu_np",
)


def fetch_dividend(code: str) -> pd.DataFrame:
    """单只股票分红送股历史（仅保留已实施的记录，预案/股东大会阶段会变更不入库）。

    stk_div=每股送转合计（股），cash_div/cash_div_tax=每股分红 税后/税前（元）。
    end_date 为分红年度报告期（'YYYY-MM-DD'，中期分红为 06-30 等）。
    返回列：code/end_date + DIVIDEND_COLUMNS。
    """
    ts_code = _to_ts_code(code)
    cols = ["code", "end_date", *DIVIDEND_COLUMNS]
    df = _call_with_retry(
        f"fetch_dividend({code})",
        _pro().dividend,
        ts_code=ts_code,
        fields="ts_code,end_date,ann_date,div_proc,stk_div,cash_div,cash_div_tax,"
               "record_date,ex_date,pay_date",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df = df[df["div_proc"].astype(str).str.strip() == "实施"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    for c in ("end_date", "ann_date", "record_date", "ex_date", "pay_date"):
        df[c] = _ann_date_iso(df[c])
    for c in ("stk_div", "cash_div", "cash_div_tax"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["code"] = code
    df = df.dropna(subset=["end_date"])
    df = df.sort_values(["end_date", "ann_date"]).drop_duplicates("end_date", keep="last")
    return df[cols].reset_index(drop=True)


def _normalize_forecast_frame(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """forecast 响应归一化：日期 ISO、p_change ÷100、net_profit 万元×1e4 转元。

    调用前 df 须已带 code 列（逐股由调用方补、按日由 _from_ts_code_batch 转换）。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["end_date"] = _ann_date_iso(df["end_date"])
    df["ann_date"] = _ann_date_iso(df["ann_date"])
    for c in ("p_change_min", "p_change_max"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce") / 100.0
    for c in ("net_profit_min", "net_profit_max"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce") * 1e4
    df = df.dropna(subset=["code", "end_date", "ann_date"])
    df = df.sort_values(["end_date", "ann_date"]).drop_duplicates(["code", "end_date", "ann_date"], keep="last")
    return df[cols].reset_index(drop=True)


def fetch_forecast(code: str) -> pd.DataFrame:
    """单只股票业绩预告历史。同一报告期的多次修正预告全部保留（按公告日区分）。

    p_change_min/max 预告净利润变动幅度，÷100 归一为小数；net_profit_min/max
    tushare 单位为万元，×1e4 转元（与利润表口径一致）。
    返回列：code/end_date('YYYY-MM-DD')/ann_date + FORECAST_COLUMNS（去 ann_date）。
    """
    ts_code = _to_ts_code(code)
    cols = ["code", "end_date", "ann_date", *FORECAST_COLUMNS]
    df = _call_with_retry(
        f"fetch_forecast({code})",
        _pro().forecast,
        ts_code=ts_code,
        fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
               "net_profit_min,net_profit_max,change_reason",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["code"] = code
    return _normalize_forecast_frame(df, cols)


def fetch_forecast_by_date(ann_date: str) -> pd.DataFrame:
    """一次请求拿全市场某公告日的业绩预告（输出契约同 fetch_forecast，多 code 混合）。

    ann_date 用 'YYYY-MM-DD'。财报季每日增量抓取用（免逐股调用）。
    """
    cols = ["code", "end_date", "ann_date", *FORECAST_COLUMNS]
    df = _call_with_retry(
        f"fetch_forecast_by_date({ann_date})",
        _pro().forecast,
        ann_date=_to_ts_date(ann_date),
        fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
               "net_profit_min,net_profit_max,change_reason",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = _from_ts_code_batch(df)
    df = df[df["code"].str.match(r"^(sh\.60|sz\.00)\d{4}$", na=False)]
    return _normalize_forecast_frame(df, cols)


def _normalize_express_frame(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """express 响应归一化：日期 ISO、金额数值化、diluted_roe/yoy_* ÷100。

    调用前 df 须已带 code 列（同 _normalize_forecast_frame）。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["end_date"] = _ann_date_iso(df["end_date"])
    df["ann_date"] = _ann_date_iso(df["ann_date"])
    for c in ("revenue", "operate_profit", "total_profit", "n_income", "diluted_eps", "bps"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    for c in ("diluted_roe", "yoy_sales", "yoy_op", "yoy_dedu_np"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce") / 100.0
    df = df.dropna(subset=["code", "end_date"])
    df = df.sort_values(["end_date", "ann_date"]).drop_duplicates(["code", "end_date"], keep="last")
    return df[cols].reset_index(drop=True)


def fetch_express(code: str) -> pd.DataFrame:
    """单只股票业绩快报历史。金额为元；diluted_roe/yoy_* 百分数 ÷100 归一。

    yoy_dedu_np 为扣非净利润同比。同一报告期多次公告按公告日保留最新。
    返回列：code/end_date('YYYY-MM-DD')/ann_date + EXPRESS_COLUMNS。
    """
    ts_code = _to_ts_code(code)
    cols = ["code", "end_date", "ann_date", *EXPRESS_COLUMNS]
    df = _call_with_retry(
        f"fetch_express({code})",
        _pro().express,
        ts_code=ts_code,
        fields="ts_code,ann_date,end_date," + ",".join(EXPRESS_COLUMNS),
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["code"] = code
    return _normalize_express_frame(df, cols)


# disclosure_date 分页大小（代理单页上限 6000 行；period 参数被代理忽略，只能全量翻页）
_DISCLOSURE_PAGE = 6000


def fetch_disclosed_report_codes(date: str, tail_days: int = 2) -> list[str]:
    """实际披露日落在 [date-tail_days, date] 内的主板 code 列表（去重、升序）。

    基于 disclosure_date（财报披露计划表）的 actual_date 实际披露日过滤——本代理
    不支持 income/fina_indicator 的 ann_date 按日查询（返回空），只能全表分页拉取
    后客户端过滤。tail_days 回溯几天重抓，吸收"披露日与代理报表数据入库存在 1-2 天
    时间差"导致的当日抓空（漏抓代价高：次日名单就变了）。

    date 用 'YYYY-MM-DD'。财报季每日增量抓取用。
    """
    frames: list[pd.DataFrame] = []
    offset = 0
    while True:
        df = _call_with_retry(
            f"fetch_disclosure_date(offset={offset})",
            _pro().disclosure_date,
            limit=_DISCLOSURE_PAGE,
            offset=offset,
            fields="ts_code,end_date,actual_date",
        )
        if df is None or df.empty:
            break
        frames.append(df)
        if len(df) < _DISCLOSURE_PAGE:
            break
        offset += _DISCLOSURE_PAGE
    if not frames:
        return []
    all_df = pd.concat(frames, ignore_index=True)
    all_df = _from_ts_code_batch(all_df)
    actual = pd.to_datetime(all_df["actual_date"], format="%Y%m%d", errors="coerce")
    target = pd.Timestamp(date)
    lo = target - pd.Timedelta(days=tail_days)
    mask = (actual >= lo) & (actual <= target)
    codes = all_df.loc[mask, "code"].astype(str)
    mainboard = codes[codes.str.match(r"^(sh\.60|sz\.00)\d{4}$", na=False)]
    return sorted(mainboard.unique().tolist())


def fetch_index_daily(index_code: str = "sh.000001", start_date: str = "", end_date: str = "") -> pd.DataFrame:
    """指数日线收盘价（官方 daily 不含指数，须用 index_daily）。返回 code/date/close。

    index_code 用项目内部 sh.000001 风格，内部转 000001.SH 调用。
    """
    ts_code = _to_ts_code(index_code)
    df = _call_with_retry(
        f"fetch_index_daily({index_code})",
        _pro().index_daily,
        ts_code=ts_code,
        start_date=_to_ts_date(start_date),
        end_date=_to_ts_date(end_date),
        fields="trade_date,close",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "date", "close"])
    df = df.copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["code"] = index_code
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
    return df[["code", "date", "close"]].reset_index(drop=True)


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
