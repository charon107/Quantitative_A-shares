"""本地滚动备份与 schema v2 迁移的行为测试。"""
import os
import sys
import time

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src import backup, db  # noqa: E402


def _write_db(path: str, marker: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(marker)


class TestSnapshotAndRotate:
    def test_snapshot_moves_old_db_into_backups(self, tmp_path):
        # Arrange
        dest = str(tmp_path / "market.duckdb")
        _write_db(dest, "v1")

        # Act
        target = backup.snapshot_before_swap(dest)

        # Assert：旧库被 rename 进 backups/，原路径不存在
        assert target is not None and target.exists()
        assert not os.path.exists(dest)
        assert target.parent == tmp_path / "backups"

    def test_snapshot_returns_none_when_db_missing(self, tmp_path):
        assert backup.snapshot_before_swap(str(tmp_path / "nope.duckdb")) is None

    def test_rotate_keeps_recent_dailies_and_promotes_weekly(self, tmp_path, monkeypatch):
        # Arrange：5 份每日备份（时间戳递增），无周备份
        monkeypatch.setattr(backup, "BACKUP_KEEP_DAILY", 3)
        monkeypatch.setattr(backup, "BACKUP_KEEP_WEEKLY", 4)
        dest = str(tmp_path / "market.duckdb")
        bdir = tmp_path / "backups"
        bdir.mkdir()
        for i in range(5):
            _write_db(str(bdir / f"market-2026070{i + 1}-000000.duckdb"), f"d{i}")

        # Act
        backup.rotate_backups(dest)

        # Assert：每日只剩最近 3 份；最新一份被晋升为周备份
        dailies = sorted(p.name for p in bdir.glob("market-*.duckdb"))
        weeklies = sorted(p.name for p in bdir.glob("weekly-*.duckdb"))
        assert dailies == [
            "market-20260703-000000.duckdb",
            "market-20260704-000000.duckdb",
            "market-20260705-000000.duckdb",
        ]
        assert weeklies == ["weekly-20260705-000000.duckdb"]

    def test_rotate_skips_weekly_promotion_when_recent_weekly_exists(self, tmp_path):
        # Arrange：已有一份新鲜（mtime=now）的周备份
        dest = str(tmp_path / "market.duckdb")
        bdir = tmp_path / "backups"
        bdir.mkdir()
        _write_db(str(bdir / "market-20260704-000000.duckdb"), "d")
        weekly = bdir / "weekly-20260701-000000.duckdb"
        _write_db(str(weekly), "w")
        os.utime(weekly, (time.time(), time.time()))

        # Act
        backup.rotate_backups(dest)

        # Assert：不再晋升新的周备份
        assert len(list(bdir.glob("weekly-*.duckdb"))) == 1

    def test_atomic_swap_creates_backup_of_old_db(self, tmp_path):
        # Arrange
        dest = str(tmp_path / "market.duckdb")
        tmp = dest + ".new"
        _write_db(dest, "old")
        _write_db(tmp, "new")

        # Act
        db.atomic_swap(tmp, dest)

        # Assert：新库就位，旧库进 backups/
        with open(dest, encoding="utf-8") as f:
            assert f.read() == "new"
        backups = list((tmp_path / "backups").glob("market-*.duckdb"))
        assert len(backups) == 1
        with open(backups[0], encoding="utf-8") as f:
            assert f.read() == "old"


class TestMigrateSchemaV2:
    @pytest.fixture
    def legacy_db(self, tmp_path):
        """构造带 adjustflag 列的 v1 旧库（含 1 行 kline + meta）。"""
        path = str(tmp_path / "legacy.duckdb")
        with db.connect(read_only=False, path=path) as conn:
            conn.execute(
                """
                CREATE TABLE kline (
                    code VARCHAR NOT NULL, date DATE NOT NULL,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                    volume DOUBLE, amount DOUBLE, pctChg DOUBLE, turn DOUBLE,
                    adjustflag VARCHAR, PRIMARY KEY (code, date)
                );
                CREATE TABLE stock_meta (code VARCHAR PRIMARY KEY, code_name VARCHAR);
                """
            )
            conn.execute(
                "INSERT INTO kline VALUES ('sh.600000', DATE '2025-01-02', "
                "10.0, 10.1, 9.9, 10.05, 12345.0, 6789.0, 1.23, 0.88, '2')"
            )
            conn.execute("INSERT INTO stock_meta VALUES ('sh.600000', '浦发银行')")
        return path

    def test_migrate_drops_adjustflag_and_sets_version(self, legacy_db):
        # Act
        from scripts.archive.migrate_schema_v2 import migrate

        migrate(legacy_db)

        # Assert
        with db.connect(read_only=True, path=legacy_db) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info('kline')").fetchall()]
            assert "adjustflag" not in cols
            row = conn.execute("SELECT code, close, pctChg FROM kline").fetchone()
            assert row[0] == "sh.600000"
            assert row[1] == pytest.approx(10.05)
            assert row[2] == pytest.approx(1.23, abs=1e-4)
            assert db.get_meta("schema_version", conn) == str(db.SCHEMA_VERSION)

    def test_migrate_is_idempotent(self, legacy_db, capsys):
        from scripts.archive.migrate_schema_v2 import migrate

        migrate(legacy_db)
        migrate(legacy_db)  # 第二次应跳过

        assert "无需迁移" in capsys.readouterr().out
