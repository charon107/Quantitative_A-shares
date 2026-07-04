"""DuckDB 本地滚动备份：swap 前把旧库 rename 进 backups/，按「每日 N 份 + 每周 M 份」轮转。

设计（零拷贝优先，适配小服务器磁盘）：
  - ``snapshot_before_swap``：入库替换前把旧库 ``os.replace``（rename，零 IO）到
    ``backups/market-YYYYMMDD-HHMMSS.duckdb``。
  - ``rotate_backups``：每日备份保留最近 ``BACKUP_KEEP_DAILY`` 份；若最近 7 天内没有
    周备份，把最新一份每日备份硬链接（同样零空间）为 ``weekly-*.duckdb``，周备份保留
    最近 ``BACKUP_KEEP_WEEKLY`` 份。
  - 备份文件写入后只读不改，硬链接安全（删除每日份不影响周份）。

环境变量：
  - ``BACKUP_KEEP_DAILY``   每日备份保留份数（默认 3）
  - ``BACKUP_KEEP_WEEKLY``  周备份保留份数（默认 4）
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

BACKUP_KEEP_DAILY = int(os.environ.get("BACKUP_KEEP_DAILY", "3"))
BACKUP_KEEP_WEEKLY = int(os.environ.get("BACKUP_KEEP_WEEKLY", "4"))

DAILY_PREFIX = "market-"
WEEKLY_PREFIX = "weekly-"
WEEKLY_INTERVAL_SECONDS = 7 * 86400


def backup_dir(dest_path: str) -> Path:
    """备份目录：与库文件同目录下的 backups/（同盘保证 rename/硬链接可用）。"""
    return Path(dest_path).resolve().parent / "backups"


def snapshot_before_swap(dest_path: str) -> Path | None:
    """把现有库 rename 到 backups/ 作为恢复点。库不存在时返回 None。"""
    dest = Path(dest_path)
    if not dest.exists():
        return None
    bdir = backup_dir(dest_path)
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = bdir / f"{DAILY_PREFIX}{stamp}.duckdb"
    os.replace(dest, target)
    return target


def _sorted_backups(bdir: Path, prefix: str) -> list[Path]:
    """按文件名（内嵌时间戳）升序列出某前缀的备份文件。"""
    return sorted(p for p in bdir.glob(f"{prefix}*.duckdb") if p.is_file())


def _promote_weekly(bdir: Path, dailies: list[Path]) -> None:
    """最近 7 天没有周备份时，把最新每日备份晋升为周备份（硬链接，失败退化为拷贝）。"""
    if not dailies:
        return
    weeklies = _sorted_backups(bdir, WEEKLY_PREFIX)
    if weeklies and time.time() - weeklies[-1].stat().st_mtime < WEEKLY_INTERVAL_SECONDS:
        return
    newest = dailies[-1]
    target = bdir / f"{WEEKLY_PREFIX}{newest.name.removeprefix(DAILY_PREFIX)}"
    if target.exists():
        return
    try:
        os.link(newest, target)
    except OSError:
        shutil.copy2(newest, target)


def rotate_backups(dest_path: str) -> None:
    """执行周备份晋升 + 每日/每周份数轮转（删除超额的最旧文件）。"""
    bdir = backup_dir(dest_path)
    if not bdir.is_dir():
        return
    dailies = _sorted_backups(bdir, DAILY_PREFIX)
    _promote_weekly(bdir, dailies)
    for old in dailies[:-BACKUP_KEEP_DAILY] if BACKUP_KEEP_DAILY > 0 else dailies:
        old.unlink(missing_ok=True)
    weeklies = _sorted_backups(bdir, WEEKLY_PREFIX)
    for old in weeklies[:-BACKUP_KEEP_WEEKLY] if BACKUP_KEEP_WEEKLY > 0 else weeklies:
        old.unlink(missing_ok=True)
