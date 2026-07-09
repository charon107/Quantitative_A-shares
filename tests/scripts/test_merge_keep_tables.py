"""merge_keep_tables：新库空表从生产库补齐，非空表保持不动。"""
from __future__ import annotations

import importlib
import os

import duckdb
import pytest

from src import db


@pytest.fixture()
def dbs(tmp_path, monkeypatch):
    """构造：生产库（旧日线 + 基本面）与新库（新日线、基本面为空）。"""
    prod = str(tmp_path / "prod.duckdb")
    new = str(tmp_path / "new.duckdb")

    with db.connect(read_only=False, path=prod) as conn:
        db.init_schema(conn)
        conn.execute("INSERT INTO kline (code, date) VALUES ('sh.600000', DATE '2025-01-02')")
        conn.execute(
            "INSERT INTO stock_fundamental (code, year) VALUES ('sh.600000', 2023), ('sz.000001', 2023)"
        )
        conn.execute("INSERT INTO selected_stocks (year, code) VALUES (2023, 'sh.600000')")

    with db.connect(read_only=False, path=new) as conn:
        db.init_schema(conn)
        conn.execute(
            "INSERT INTO kline (code, date) VALUES ('sh.600000', DATE '2013-01-04'), ('sh.600000', DATE '2025-01-02')"
        )

    monkeypatch.setattr(db, "DUCKDB_PATH", prod)
    return prod, new


def test_merge_fills_empty_tables_and_keeps_nonempty(dbs):
    prod, new = dbs

    mod = importlib.import_module("scripts.merge_keep_tables")
    mod.merge(new)

    with duckdb.connect(new, read_only=True) as conn:
        # 非空表（新日线）不被生产库旧数据覆盖
        assert conn.execute("SELECT COUNT(*) FROM kline").fetchone()[0] == 2
        # 空表从生产库补齐
        assert conn.execute("SELECT COUNT(*) FROM stock_fundamental").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM selected_stocks").fetchone()[0] == 1
        # schema_version 已写入
        v = conn.execute("SELECT value FROM meta_kv WHERE key='schema_version'").fetchone()
        assert v is not None and v[0] == str(db.SCHEMA_VERSION)


def test_merge_refuses_same_path(dbs):
    prod, _ = dbs
    mod = importlib.import_module("scripts.merge_keep_tables")
    with pytest.raises(SystemExit):
        mod.merge(prod)
