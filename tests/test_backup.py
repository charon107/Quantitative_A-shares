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

