"""A股看板 — 统计计算层（DuckDB 版）。

纯计算：输入来自 DuckDB（`kline` / `stock_meta` 表），输出统计指标。
无 UI、无 HTTP，便于单测与缓存。被 FastAPI 路由复用。

所有读取走 `src.db.query_df`（只读短连接）。`path=None` 时用 `DUCKDB_PATH`。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import config, db
from src.analysis.ma5_above_ma10_duration import (
    add_ma,
    extract_samples,
    apply_strict_ongoing,
)

MA_WINDOWS = (5, 10, 20, 60)


# ========== 全市场最新一日 ==========
def load_all_latest_day(path: str | None = None) -> pd.DataFrame:
    """全市场最新交易日的 OHLCV 快照。无数据返回空 DataFrame。

    锚定到全库最新一日（MAX(date)），而非每只票各自的最后一行——否则退市/长期停牌
    股票那条冻结的旧行情会被当成"最新一日"混进涨跌幅榜。
    """
    return db.query_df(
        """
        SELECT * FROM kline
        WHERE date = (SELECT MAX(date) FROM kline)
        """,
        path=path,
    )


def market_breadth(df: pd.DataFrame) -> dict:
    """市场宽度：涨/跌/平家数与涨跌比。纯 pandas，作用于已加载 df。"""
    if df.empty or "pctChg" not in df.columns:
        return {"up": 0, "down": 0, "flat": 0, "ratio": np.nan}
    up = int((df["pctChg"] > 0).sum())
    down = int((df["pctChg"] < 0).sum())
    flat = int((df["pctChg"] == 0).sum())
    if down == 0:
        ratio = np.inf if up > 0 else 0.0
    else:
        ratio = up / down
    return {"up": up, "down": down, "flat": flat, "ratio": ratio}


# ========== 等权指数 ==========
def _equal_weighted_index_sql(code_filter: str = "") -> str:
    # CTE 内先按日期截窗（多留 30 天日历缓冲供 LAG 取前收），避免窗口函数
    # 扫全表——库存 13 年历史后全表扫会拖垮小服务器。
    extra = f"AND {code_filter}" if code_filter else ""
    return f"""
        WITH r AS (
            SELECT date,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM kline
            WHERE date >= CAST(? AS DATE) - INTERVAL 30 DAY {extra}
        )
        SELECT date, AVG(ret) AS daily_return
        FROM r
        WHERE ret IS NOT NULL AND date >= ?
        GROUP BY date
        ORDER BY date
    """


def equal_weighted_index(
    start_date: str = config.DASHBOARD_START_DATE, path: str | None = None
) -> pd.Series:
    """等权组合累计收益（全市场主版）。index=日期，value=累计收益率。"""
    daily = db.query_df(_equal_weighted_index_sql(), [start_date, start_date], path=path)
    if daily.empty:
        return pd.Series(dtype=float)
    s = daily.set_index("date")["daily_return"].astype(float)
    return (1 + s).cumprod() - 1


def shanghai_equal_weighted_index(
    start_date: str = config.DASHBOARD_START_DATE, path: str | None = None
) -> pd.Series:
    """上证主板等权组合累计收益（仅 sh.60xxxx）。"""
    daily = db.query_df(
        _equal_weighted_index_sql("code LIKE 'sh.6%'"), [start_date, start_date], path=path
    )
    if daily.empty:
        return pd.Series(dtype=float)
    s = daily.set_index("date")["daily_return"].astype(float)
    return (1 + s).cumprod() - 1


# ========== 市场宽度（涨跌家数 + 涨跌停家数） ==========
def breadth_series(
    up_threshold: float = 9.9,
    down_threshold: float = -9.9,
    start_date: str = config.DASHBOARD_START_DATE,
    path: str | None = None,
) -> pd.DataFrame:
    """每日市场涨跌家数走势（看板窗口内，一条 SQL 同时给出 上涨/下跌/涨停/跌停）。

    列：date / up / down / limit_up / limit_down。
    """
    df = db.query_df(
        """
        SELECT date,
               SUM(CASE WHEN pctChg > 0 THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN pctChg < 0 THEN 1 ELSE 0 END) AS down,
               SUM(CASE WHEN pctChg >= ? THEN 1 ELSE 0 END) AS limit_up,
               SUM(CASE WHEN pctChg <= ? THEN 1 ELSE 0 END) AS limit_down
        FROM kline
        WHERE date >= ?
        GROUP BY date
        ORDER BY date
        """,
        [up_threshold, down_threshold, start_date],
        path=path,
    )
    if df.empty:
        return df
    for c in ("up", "down", "limit_up", "limit_down"):
        df[c] = df[c].astype(int)
    return df


def company_info_df(code: str, path: str | None = None) -> pd.DataFrame:
    """单只股票的公司信息（stock_info 一行）。表不存在/无数据返回空 DataFrame。"""
    try:
        return db.query_df("SELECT * FROM stock_info WHERE code = ?", [code], path=path)
    except Exception:
        return pd.DataFrame()


# ========== 个股财务扩展（季度基本面 / 估值日频 / 分红 / 业绩预告与快报） ==========
def _per_code_table(table: str, code: str, order_by: str, path: str | None = None) -> pd.DataFrame:
    """单只股票按主键查一张表（升序）。表不存在/无数据返回空 DataFrame（前端整卡隐藏）。"""
    try:
        return db.query_df(
            f"SELECT * FROM {table} WHERE code = ? ORDER BY {order_by}", [code], path=path
        )
    except Exception:
        return pd.DataFrame()


def quarterly_fundamental(code: str, path: str | None = None) -> pd.DataFrame:
    """单只股票季度基本面（按报告期升序，列见 db.QUARTERLY_FUNDAMENTAL_COLUMNS）。"""
    return _per_code_table("stock_fundamental_quarterly", code, "end_date", path=path)


def valuation_daily(code: str, path: str | None = None) -> pd.DataFrame:
    """单只股票估值日频（PE/PB/PS/股息率/市值，按日期升序）。"""
    return _per_code_table("stock_valuation_daily", code, "date", path=path)


def dividend_history(code: str, path: str | None = None) -> pd.DataFrame:
    """单只股票分红送股历史（已实施，按分红年度升序）。"""
    return _per_code_table("stock_dividend", code, "end_date", path=path)


def forecast_history(code: str, path: str | None = None) -> pd.DataFrame:
    """单只股票业绩预告历史（按报告期+公告日升序）。"""
    return _per_code_table("stock_forecast", code, "end_date, ann_date", path=path)


def express_history(code: str, path: str | None = None) -> pd.DataFrame:
    """单只股票业绩快报历史（按报告期升序）。"""
    return _per_code_table("stock_express", code, "end_date", path=path)


def hot_stocks(limit: int = 12, path: str | None = None) -> pd.DataFrame:
    """同花顺人气榜（按人气排名升序）。表不存在/无数据返回空 DataFrame。"""
    try:
        return db.query_df(
            "SELECT * FROM ths_hot ORDER BY rank_no LIMIT ?", [limit], path=path
        )
    except Exception:
        return pd.DataFrame()


def day_movers(date: str, path: str | None = None) -> pd.DataFrame:
    """某交易日全部上涨/下跌个股（含名称、开盘价、收盘价、涨跌幅），按涨跌幅降序。

    列：code / code_name / open / close / pctChg。
    """
    return db.query_df(
        """
        SELECT k.code, m.code_name, k.open, k.close, k.pctChg
        FROM kline k
        LEFT JOIN stock_meta m ON k.code = m.code
        WHERE k.date = CAST(? AS DATE) AND k.pctChg <> 0
        ORDER BY k.pctChg DESC
        """,
        [date],
        path=path,
    )


# ========== 个股 K线 ==========
def load_stock_kline(code: str, path: str | None = None) -> pd.DataFrame:
    """单只股票完整 K线（按日期升序）。无数据抛 LookupError。"""
    df = db.query_df(
        "SELECT * FROM kline WHERE code = ? ORDER BY date",
        [code],
        path=path,
    )
    if df.empty:
        raise LookupError(f"No data for {code}")
    return df.reset_index(drop=True)


def add_moving_averages(df: pd.DataFrame, windows=MA_WINDOWS) -> pd.DataFrame:
    """在 K线 df 上追加 MA{w} 列（基于收盘价）。"""
    out = df.copy()
    for w in windows:
        out[f"MA{w}"] = out["close"].rolling(window=w, min_periods=w).mean()
    return out


def volatility_frame(code: str, window: int = 20, path: str | None = None) -> pd.DataFrame:
    """带日期的滚动波动率。列：date / value（单次查询）。"""
    df = load_stock_kline(code, path=path)
    ret = df["close"].pct_change()
    vol = ret.rolling(window=window).std() * np.sqrt(252)
    return pd.DataFrame({"date": df["date"].to_numpy(), "value": vol.to_numpy()})


# ========== 排行榜 ==========
def top_movers(
    df: pd.DataFrame,
    n: int = 10,
    metric: str = "pctChg",
    ascending: bool = False,
) -> pd.DataFrame:
    """排行榜（涨幅/跌幅/成交额/换手率等）。纯 pandas，作用于已加载快照 df。"""
    if df.empty or metric not in df.columns:
        return pd.DataFrame()
    return df.sort_values(metric, ascending=ascending).head(n).reset_index(drop=True)


# ========== 名称映射 / 搜索 ==========
def latest_quotes(codes: list[str], path: str | None = None) -> pd.DataFrame:
    """多只股票各自最新一行的行情快照。列：code / code_name / date / close / pctChg。

    按每只股票各自的最后一条记录取（不用全库 MAX(date) 锚定）——收藏列表
    场景下停牌/退市股也要展示其最后已知行情，由前端对缺失 code 显示 "—"。
    """
    if not codes:
        return pd.DataFrame(columns=["code", "code_name", "date", "close", "pctChg"])
    placeholders = ", ".join(["?"] * len(codes))
    return db.query_df(
        f"""
        SELECT k.code, m.code_name, k.date, k.close, k.pctChg
        FROM kline k
        LEFT JOIN stock_meta m ON m.code = k.code
        WHERE k.code IN ({placeholders})
        QUALIFY row_number() OVER (PARTITION BY k.code ORDER BY k.date DESC) = 1
        ORDER BY k.code
        """,
        codes,
        path=path,
    )


def name_map(path: str | None = None) -> dict:
    """代码 -> 公司名称映射。无表/无数据返回空字典。"""
    try:
        df = db.query_df("SELECT code, code_name FROM stock_meta", path=path)
    except Exception:
        return {}
    if df.empty:
        return {}
    return dict(zip(df["code"], df["code_name"]))


def search_stocks(query: str, limit: int = 50, path: str | None = None) -> pd.DataFrame:
    """按代码或名称模糊搜索。列：code / code_name。"""
    like = f"%{query}%"
    return db.query_df(
        """
        SELECT code, code_name FROM stock_meta
        WHERE code ILIKE ? OR code_name ILIKE ?
        ORDER BY code
        LIMIT ?
        """,
        [like, like, limit],
        path=path,
    )


# ========== MA5 > MA20 多头时长 ==========
def ma_duration_samples(
    start_date: str = config.DASHBOARD_START_DATE,
    ma_short: int = 5,
    ma_long: int = 20,
    path: str | None = None,
) -> pd.DataFrame:
    """全市场 MA{short}>MA{long} 金叉区间样本（DuckDB 一次拉取 + 复用纯函数）。

    只拉取窗口起点前 90 天（日历）以来的行——MA{long} 需要窗口起点前的
    前置数据，90 天足够覆盖 20 个交易日 + 长假；此前是全表拉取，库存
    13 年历史后会把 758 万行灌进 pandas 直接耗尽小服务器内存。
    extract_samples 仍按 start_date 过滤上穿日，结果与全量拉取一致。

    返回 detail：code/start_date/end_date/duration/ongoing，按时长降序。
    """
    cols = ["code", "start_date", "end_date", "duration", "ongoing"]
    allrows = db.query_df(
        "SELECT code, date, close FROM kline "
        "WHERE date >= CAST(? AS DATE) - INTERVAL 90 DAY ORDER BY code, date",
        [start_date],
        path=path,
    )
    if allrows.empty:
        return pd.DataFrame(columns=cols)

    samples: list[dict] = []
    market_last = None
    for code, group in allrows.groupby("code", sort=False):
        g = group[["date", "close"]].reset_index(drop=True)
        g = add_ma(g, ma_short, ma_long)
        last_d = g["date"].iloc[-1]
        if market_last is None or last_d > market_last:
            market_last = last_d
        samples.extend(extract_samples(g, code, start_date))

    detail = pd.DataFrame(samples, columns=cols)
    detail = apply_strict_ongoing(detail, market_last)
    if not detail.empty:
        detail = detail.sort_values("duration", ascending=False).reset_index(drop=True)
    return detail


# ========== 基本面选股（逐年回测） ==========
INDEX_CODE = "sh.000001"  # 上证综指，选股股价图对照


def selection_years(path: str | None = None) -> list[int]:
    """有选股结果的年份列表（升序）。表不存在/无数据返回空列表。"""
    try:
        df = db.query_df("SELECT DISTINCT year FROM selected_stocks ORDER BY year", path=path)
    except Exception:
        return []
    if df.empty:
        return []
    return [int(y) for y in df["year"].tolist()]


def selected_stocks_by_year(year: int, path: str | None = None) -> pd.DataFrame:
    """某年选出的股票池，带上该股选股窗口末年(year-1)的展示财务指标。

    列：code / code_name / roe / netprofit_yoy / debt_ratio / net_profit / cfo_np_ratio。
    cfo_np_ratio 是选股五年窗口 [year-5, year-1] 的 SUM(cfo)/SUM(net_profit)
    （等价条件4 的五年均值之比；入选股 cfo 五年齐全，与筛选口径一致）。
    """
    try:
        return db.query_df(
            """
            SELECT s.code, s.code_name,
                   f.roe, f.netprofit_yoy, f.debt_ratio, f.net_profit,
                   cf.cfo_np_ratio
            FROM selected_stocks s
            LEFT JOIN stock_fundamental f
              ON s.code = f.code AND f.year = ? - 1
            LEFT JOIN (
                SELECT code, SUM(cfo) / NULLIF(SUM(net_profit), 0) AS cfo_np_ratio
                FROM stock_fundamental
                WHERE year BETWEEN ? - 5 AND ? - 1
                GROUP BY code
            ) cf ON s.code = cf.code
            WHERE s.year = ?
            ORDER BY s.code
            """,
            [year, year, year, year],
            path=path,
        )
    except Exception:
        return pd.DataFrame()


def _window_line(df: pd.DataFrame, start: str, end: str | None, ma_n: int) -> pd.DataFrame:
    """截取 [start, end] 收盘价并在窗口内算 MA。列：date/close/ma。"""
    if df.empty:
        return pd.DataFrame(columns=["date", "close", "ma"])
    out = df[["date", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["date", "close"]).sort_values("date")
    out = out[out["date"] >= pd.to_datetime(start)]
    if end:
        out = out[out["date"] <= pd.to_datetime(end)]
    out["ma"] = out["close"].rolling(window=ma_n).mean()
    return out.reset_index(drop=True)


def screening_chart(year: int, code: str, ma_n: int = 20, path: str | None = None) -> dict:
    """复刻原 matplotlib 三特性所需的数据：

    - 个股/指数收盘价:从 {year}-01-01 截断到「次年首个财报公布日」(next_pub)
    - pub_dates:价格数据实际覆盖区间 [year-01-01, next_pub] 内的所有财报公布日竖线
    - next_pub:模拟持有到下次财报的截断终点

    返回 dict：code/code_name/next_pub/pub_dates/stock[]/index[]。
    """
    # 该 code 各财年的公布日（ann_date 为 'YYYY-MM-DD'）
    try:
        anns = db.query_df(
            "SELECT ann_date FROM stock_fundamental WHERE code = ? AND ann_date IS NOT NULL",
            [code], path=path,
        )
    except Exception:
        anns = pd.DataFrame()
    ann_dates = sorted(pd.to_datetime(anns["ann_date"], errors="coerce").dropna().tolist()) if not anns.empty else []

    y_start = pd.Timestamp(year=year, month=1, day=1)
    y_end = pd.Timestamp(year=year + 1, month=1, day=1)
    y1_end = pd.Timestamp(year=year + 2, month=1, day=1)
    next_list = [d for d in ann_dates if y_end <= d < y1_end]
    next_pub = next_list[0] if next_list else None

    # pub_dates: 价格数据实际覆盖区间内的所有公告日（而非仅限日历年）
    # 区间为 [year-01-01, next_pub]（next_pub 为 None 时不设上界）
    if next_pub is not None:
        pub_dates = [d for d in ann_dates if y_start <= d <= next_pub]
    else:
        pub_dates = [d for d in ann_dates if y_start <= d]

    start = f"{year}-01-01"
    end = next_pub.strftime("%Y-%m-%d") if next_pub is not None else None

    try:
        stock_raw = load_stock_kline(code, path=path)
    except LookupError:
        stock_raw = pd.DataFrame(columns=["date", "close"])
    idx_raw = db.query_df(
        "SELECT date, close FROM index_daily WHERE code = ? ORDER BY date", [INDEX_CODE], path=path,
    )

    stock_line = _window_line(stock_raw, start, end, ma_n)
    index_line = _window_line(idx_raw, start, end, ma_n)

    nm = name_map(path=path)
    return {
        "code": code,
        "code_name": nm.get(code),
        "next_pub": end,
        "pub_dates": [d.strftime("%Y-%m-%d") for d in pub_dates],
        "stock": stock_line,
        "index": index_line,
    }


# ========== 数据状态 ==========
def data_status(path: str | None = None) -> dict:
    """数据新鲜度与覆盖：最新交易日、覆盖股票数、总行数。"""
    if not db.database_exists(path):
        return {"latest_date": None, "n_codes": 0, "n_rows": 0}
    df = db.query_df(
        "SELECT MAX(date) AS latest_date, COUNT(DISTINCT code) AS n_codes, COUNT(*) AS n_rows FROM kline",
        path=path,
    )
    row = df.iloc[0]
    latest = row["latest_date"]
    return {
        "latest_date": None if pd.isna(latest) else pd.Timestamp(latest).strftime("%Y-%m-%d"),
        "n_codes": int(row["n_codes"]),
        "n_rows": int(row["n_rows"]),
    }
