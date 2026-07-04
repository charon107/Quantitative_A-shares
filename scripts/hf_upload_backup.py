"""runner 侧：把 DB parquet 快照上传到 Hugging Face 私有 dataset，并清理过期快照。

上传到 repo 内 snapshots/YYYY-MM-DD/ 路径；保留最近 HF_BACKUP_KEEP 份（默认 8），
更旧的整目录删除。需环境变量 HF_TOKEN、HF_BACKUP_REPO（如 user/wechatnum-db-backup）。

用法（runner）：
  uv run --no-project --with huggingface_hub \
    python scripts/hf_upload_backup.py <snapshot_dir>
"""
from __future__ import annotations

import os
import sys
import time

from huggingface_hub import HfApi

KEEP = int(os.environ.get("HF_BACKUP_KEEP", "8"))
SNAP_PREFIX = "snapshots/"


def main() -> None:
    snapshot_dir = sys.argv[1] if len(sys.argv) > 1 else "db_export"
    repo = os.environ.get("HF_BACKUP_REPO", "")
    token = os.environ.get("HF_TOKEN", "")
    if not repo or not token:
        raise SystemExit("需设置 HF_BACKUP_REPO / HF_TOKEN 环境变量")

    api = HfApi(token=token)
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)

    day = time.strftime("%Y-%m-%d")
    dest = f"{SNAP_PREFIX}{day}"
    api.upload_folder(
        folder_path=snapshot_dir,
        path_in_repo=dest,
        repo_id=repo,
        repo_type="dataset",
        commit_message=f"db snapshot {day}",
    )
    print(f"[hf] 已上传 -> {repo}/{dest}")

    # 轮转：按快照日期目录排序，删掉超出保留份数的最旧目录
    files = api.list_repo_files(repo, repo_type="dataset")
    days = sorted({p.split("/")[1] for p in files if p.startswith(SNAP_PREFIX) and p.count("/") >= 2})
    for old in days[:-KEEP] if KEEP > 0 else []:
        api.delete_folder(f"{SNAP_PREFIX}{old}", repo_id=repo, repo_type="dataset",
                          commit_message=f"prune snapshot {old}")
        print(f"[hf] 已清理过期快照 {old}")
    print(f"[hf] 当前保留 {min(len(days), KEEP)} 份快照")


if __name__ == "__main__":
    main()
