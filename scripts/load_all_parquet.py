"""服务器侧：把 runner 抓好的 parquet 加载进 DuckDB（不触网关）。

读取 raw_recent / adj_recent / valuation_recent / meta / company / ths_hot，
upsert raw_kline/adj_factor/stock_valuation_daily → 对「有新交易日」的 code
重算前复权写 kline → upsert stock_meta / stock_info，全量替换 ths_hot；
最后原子替换 + 清 Redis 缓存。

qfq 重算分两档（qfq = raw * factor / 最新factor）：
- 复权基准（最新复权因子）不变 → 历史 qfq 不变，只为新交易日追加（秒级）；
- 基准变化（除权除息）或新上市 → 该 code 全量重算。

用法：uv run python -u scripts/load_all_parquet.py /tmp/ingest
随后由 workflow 调 deploy/warmup_redis.py 预热。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import cache, db  # noqa: E402
from src.data_collection import tushare_client as tsc  # noqa: E402
from src.data_collection.stock_price import STATE_PATH  # noqa: E402

# 每 code 比较「最新因子」与「kline 现有末日(kmax)时点的因子」：
# 不等（或当时无因子 = 新上市/停牌后复牌）才需全量重算 qfq。
_REBASE_SQL = """
WITH f_new AS (
    SELECT code, arg_max(adj_factor, trade_date) AS f FROM adj_factor GROUP BY code
), f_old AS (
    SELECT code, arg_max(adj_factor, trade_date) AS f FROM adj_factor
    WHERE trade_date <= ? GROUP BY code
)
SELECT n.code, (o.f IS NULL OR n.f <> o.f) AS rebased
FROM f_new n LEFT JOIN f_old o ON n.code = o.code
"""

_PROGRESS_EVERY = 500


def _read(d: str, name: str) -> pd.DataFrame:
    p = os.path.join(d, name)
    return pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()


def _recompute_qfq(conn, kmax) -> tuple[int, int]:
    """对「出现新交易日」的 code 重算/追加 qfq。返回 (全量重算数, 增量追加数)。"""
    if kmax is not None:
        touched = [r[0] for r in conn.execute(
            "SELECT DISTINCT code FROM raw_kline WHERE date > ?", [kmax]
        ).fetchall()]
        rebased = {r[0]: bool(r[1]) for r in conn.execute(_REBASE_SQL, [kmax]).fetchall()}
    else:
        touched = [r[0] for r in conn.execute("SELECT DISTINCT code FROM raw_kline").fetchall()]
        rebased = {}

    n_full = 0
    n_incr = 0
    for i, code in enumerate(touched, 1):
        try:
            af = db.read_adj(code, conn)
            if af.empty:
                continue
            full = kmax is None or rebased.get(code, True)
            # 增量从 kmax 当天（含）读起：该行重写幂等，且为 compute_qfq
            # 内部的因子 ffill 提供锚点
            rf = db.read_raw(code, conn) if full else db.read_raw_since(code, kmax, conn)
            if rf.empty:
                continue
            q = tsc.compute_qfq(rf, af, code)
            if not q.empty:
                db.upsert_kline(q, conn)
                if full:
                    n_full += 1
                else:
                    n_incr += 1
        except Exception as e:
            print(f"[load_all] {code} qfq 失败：{e}")
        if i % _PROGRESS_EVERY == 0:
            # 定期把脏页/WAL 落盘：758 万行大表上连续 upsert 会把 buffer pool
            # 占满不可逐出的脏页，400MB memory_limit 下曾 OOM abort（2026-07-10）
            conn.execute("CHECKPOINT")
            print(f"[load_all] qfq 进度 {i}/{len(touched)}", flush=True)
    conn.execute("CHECKPOINT")
    return n_full, n_incr


def main(d: str | None = None) -> None:
    if d is None:
        d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ingest"
    raw = _read(d, "raw_recent.parquet")
    adj = _read(d, "adj_recent.parquet")
    val = _read(d, "valuation_recent.parquet")
    meta = _read(d, "meta.parquet")
    comp = _read(d, "company.parquet")
    hot = _read(d, "ths_hot.parquet")
    delisted = _read(d, "delisted.parquet")
    delisted_codes = delisted["code"].tolist() if not delisted.empty and "code" in delisted.columns else []
    print(f"[load_all] 输入：raw={len(raw)} adj={len(adj)} valuation={len(val)} meta={len(meta)} company={len(comp)} hot={len(hot)} delisted={len(delisted_codes)}")

    dest = db.DUCKDB_PATH
    _lock = db.acquire_ingest_lock()  # noqa: F841 持有到进程退出
    tmp = dest + ".new"
    # 连 .wal/.tmp 一起清：上次 abort 残留的 WAL 若不删，会被重放到本次新副本上
    for leftover in (tmp, tmp + ".wal", tmp + ".tmp"):
        if os.path.isdir(leftover):
            shutil.rmtree(leftover)
        elif os.path.exists(leftover):
            os.remove(leftover)
    if os.path.exists(dest):
        shutil.copy2(dest, tmp)

    last_date = None
    purged = 0
    with db.connect(read_only=False, path=tmp) as conn:
        db.init_schema(conn)
        kmax_row = conn.execute("SELECT MAX(date) FROM kline").fetchone()
        kmax = kmax_row[0] if kmax_row else None

        if not raw.empty:
            db.upsert_raw(raw, conn)
        if not adj.empty:
            db.upsert_adj(adj, conn)
        if not val.empty:
            db.upsert_valuation_daily(val, conn)

        n_full, n_incr = _recompute_qfq(conn, kmax)

        if not meta.empty:
            db.upsert_meta(meta, conn)
        if not comp.empty:
            db.upsert_company(comp, conn)
        if not hot.empty:
            conn.execute("DELETE FROM ths_hot")
            db.upsert_ths_hot(hot, conn)

        # 清理退市股：runner 给的 list_status='D' + stock_meta 中名字带「退」（退市整理期）
        purged = db.purge_delisted(conn, delisted_codes)

        nm = conn.execute("SELECT MAX(date) FROM kline").fetchone()[0]
        last_date = nm.strftime("%Y-%m-%d") if nm else None

    db.atomic_swap(tmp, dest)

    # 推进入库进度
    try:
        parent = os.path.dirname(os.path.abspath(STATE_PATH))
        os.makedirs(parent, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "last_complete_date": last_date},
                f, ensure_ascii=False, indent=2,
            )
    except Exception as e:
        print(f"[load_all] 写 state 失败（忽略）：{e}")

    # 清 Redis（预热交给 warmup_redis.py）
    cache.invalidate_all()
    print(f"[load_all] 完成：qfq 全量重算 {n_full} 只 + 增量追加 {n_incr} 只，清理退市股 {purged} 只，最新交易日 {last_date} -> {os.path.abspath(dest)}")


if __name__ == "__main__":
    main()
