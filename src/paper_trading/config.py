"""模拟盘交易规则与费率配置（全部参数化，撮合与下单校验共用）。

涨跌停幅度按 code 前缀 + 名称推断（评审结论 §11.2：库中无板块维表，
数据集无北交所股票；ST 状态按 stock_meta.code_name 当前名称推断，
历史 ST 状态不可知，属已知偏差并在 UI 披露）。
"""
from __future__ import annotations

# ---- 费用 ----
COMMISSION_RATE = 0.00025      # 佣金 万2.5，买卖双向
COMMISSION_MIN = 5.0           # 佣金最低 5 元
STAMP_TAX_RATE = 0.0005        # 印花税 0.05%，仅卖出
TRANSFER_FEE_RATE = 0.0        # 过户费：MVP 并入佣金（预留参数位）

# ---- 数量 ----
LOT_SIZE = 100                 # 整手：买卖均须 100 股整数倍（MVP 不产生零股）
MAX_ORDER_QTY = 1_000_000      # 单笔上限 100 万股

# ---- 涨跌停 ----
LIMIT_EPS = 0.3                # pctChg 判定涨跌停的 epsilon（百分点，防浮点/ tick 取整误差）
MAIN_BOARD_LIMIT = 10.0        # 沪深主板 ±10%
ST_LIMIT = 5.0                 # 主板 ST/*ST ±5%
CHINEXT_STAR_LIMIT = 20.0      # 创业板(300/301)/科创板(688/689) ±20%（科创板 ST 仍 ±20%）
BSE_LIMIT = 30.0               # 北交所 ±30%（预留：当前数据集无 bj 代码）

VALID_CODE_PREFIXES = ("sh.", "sz.")


def is_tradable_code(code: str) -> bool:
    """MVP 仅支持沪深 A 股普通股票。"""
    return code.startswith(VALID_CODE_PREFIXES)


def board_limit_pct(code: str, code_name: str = "") -> float:
    """按板块/ST 状态返回涨跌停幅度（百分点）。"""
    if code.startswith(("sh.688", "sh.689")):
        return CHINEXT_STAR_LIMIT
    if code.startswith(("sz.300", "sz.301")):
        return CHINEXT_STAR_LIMIT
    if code.startswith(("bj.4", "bj.8")):
        return BSE_LIMIT
    if code_name and "ST" in code_name.upper():
        return ST_LIMIT
    return MAIN_BOARD_LIMIT


def is_limit_up(pct_chg: float | None, limit_pct: float) -> bool:
    """pctChg（百分数）是否触及涨停。"""
    return pct_chg is not None and pct_chg >= limit_pct - LIMIT_EPS


def is_limit_down(pct_chg: float | None, limit_pct: float) -> bool:
    return pct_chg is not None and pct_chg <= -(limit_pct - LIMIT_EPS)


def compute_fees(side: str, amount: float) -> tuple[float, float, float]:
    """返回 (佣金, 印花税, 合计)。amount 为成交金额（price*qty）。"""
    commission = max(COMMISSION_MIN, round(amount * COMMISSION_RATE, 2))
    transfer = round(amount * TRANSFER_FEE_RATE, 2)
    commission = round(commission + transfer, 2)
    stamp = round(amount * STAMP_TAX_RATE, 2) if side == "sell" else 0.0
    return commission, stamp, round(commission + stamp, 2)


def price_limit_range(ref_close: float, limit_pct: float) -> tuple[float, float]:
    """按参考收盘价推算当日涨跌停价格区间（用于限价单合法性校验）。"""
    return round(ref_close * (1 - limit_pct / 100), 2), round(ref_close * (1 + limit_pct / 100), 2)
