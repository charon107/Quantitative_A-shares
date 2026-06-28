"""API 序列化辅助：DataFrame → JSON 安全的 records（处理 NaN/Inf/日期）。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _clean_scalar(v):
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if v is None or v is pd.NaT:
        return None
    # numpy 标量 -> python 原生
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def df_to_records(df: pd.DataFrame, date_cols=("date", "start_date", "end_date")) -> list[dict]:
    """把 DataFrame 转成 JSON 安全的 list[dict]：日期列 -> 'YYYY-MM-DD'，NaN/Inf -> None。"""
    if df is None or df.empty:
        return []
    out = df.copy()
    for c in date_cols:
        if c in out.columns:
            out[c] = pd.to_datetime(out[c], errors="coerce").dt.strftime("%Y-%m-%d")
    out = out.replace([np.inf, -np.inf], np.nan)
    records = out.to_dict("records")
    return [{k: _clean_scalar(v) for k, v in r.items()} for r in records]


def series_to_points(s: pd.Series, value_key: str = "value") -> list[dict]:
    """把 index=日期 的 Series 转成 [{date, value}, ...]。"""
    if s is None or s.empty:
        return []
    out = []
    for idx, val in s.items():
        date = pd.to_datetime(idx).strftime("%Y-%m-%d")
        out.append({"date": date, value_key: _clean_scalar(float(val))})
    return out


def safe_ratio(ratio: float) -> float | None:
    """涨跌比：inf/nan -> None（JSON 不支持）。"""
    if ratio is None:
        return None
    if isinstance(ratio, float) and (math.isnan(ratio) or math.isinf(ratio)):
        return None
    return float(ratio)
