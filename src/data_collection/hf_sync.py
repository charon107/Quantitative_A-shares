"""
同步股价数据到/从 Hugging Face dataset 仓库。

用法：
  python -m src.data_collection.hf_sync download  # 从 HF 拉取现有数据
  python -m src.data_collection.hf_sync upload    # 把本地更新推回 HF

环境变量：
  HF_TOKEN  - Hugging Face API token（对 dataset repo 拥有 Write 权限）
"""
import os
import sys
import time
import random
from datetime import datetime
from huggingface_hub import snapshot_download, HfApi

# 配置：修改为你的 Hugging Face dataset 仓库地址
REPO_ID = "Charon107/stock-price"
LOCAL_DIR = "股价数据_parquet_fq"
# 下载并发数（官方 HF 用 4 OK；hf-mirror.com 镜像限流严需 1）
DOWNLOAD_WORKERS = int(os.environ.get("HF_DOWNLOAD_WORKERS", "4"))

# 一次性大批量变更（比如新加了 raw_kline/ 整个目录）容易让 upload_folder 一次性
# 触发几千个文件的 LFS 校验请求，把 HF 限流打到 429，重试期间预签名 S3 URL
# （15 分钟有效期）又会过期，导致整次上传失败。改成分批 commit，每批之间
# 留点间隔，单批文件数可控。
UPLOAD_BATCH_SIZE = int(os.environ.get("HF_UPLOAD_BATCH_SIZE", "300"))
UPLOAD_BATCH_PAUSE = float(os.environ.get("HF_UPLOAD_BATCH_PAUSE", "5"))
UPLOAD_MAX_RETRIES = int(os.environ.get("HF_UPLOAD_MAX_RETRIES", "4"))


def download_from_hf():
    """
    从 HF dataset 下载最新数据到本地。

    容错逻辑：若 repo 为空/不存在，捕获异常并继续。
    这样 stock_price.py 会自然进入 INIT 全量初始化流程（冷启动）。
    """
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise RuntimeError("HF_TOKEN environment variable not set")

    try:
        print(f"[hf_sync] 下载数据从 {REPO_ID} ...")
        snapshot_download(
            repo_id=REPO_ID,
            repo_type="dataset",
            local_dir=LOCAL_DIR,
            token=hf_token,
            max_workers=DOWNLOAD_WORKERS,
        )
        print(f"[hf_sync] 下载完成，本地目录: {LOCAL_DIR}")
    except Exception as e:
        print(f"[hf_sync] 下载跳过（可能是首次运行）: {e}")


def _iter_relative_files(local_dir: str):
    for root, _, files in os.walk(local_dir):
        for fn in files:
            full = os.path.join(root, fn)
            yield os.path.relpath(full, local_dir).replace(os.sep, "/")


def _chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def upload_to_hf():
    """
    把本地数据推送到 HF dataset，覆盖式更新。

    注意：upload_folder 只覆盖，不删除远端多出的文件。
    对这个场景（只增不删）足够用。

    分批上传：一次性几千个文件的大改动（比如新增一整个目录）会让单次
    upload_folder 触发大量并发 LFS 校验请求，把 HF 限流打到 429；重试期间
    预签名 S3 URL（15 分钟有效期）又会过期，导致整次上传失败、什么都没
    传上去。分批 commit 之后，前面已成功的批次不会因为后面某一批失败而
    丢失。
    """
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise RuntimeError("HF_TOKEN environment variable not set")

    if not os.path.exists(LOCAL_DIR):
        raise RuntimeError(f"本地目录不存在: {LOCAL_DIR}")

    api = HfApi(token=hf_token)
    all_files = sorted(_iter_relative_files(LOCAL_DIR))
    if not all_files:
        print("[hf_sync] 本地目录为空，跳过上传")
        return

    batches = list(_chunked(all_files, UPLOAD_BATCH_SIZE))
    print(f"[hf_sync] 上传数据到 {REPO_ID} ...（{len(all_files)} 个文件，分 {len(batches)} 批，每批 <= {UPLOAD_BATCH_SIZE}）")

    for i, batch in enumerate(batches, 1):
        last_err = None
        for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
            try:
                api.upload_folder(
                    repo_id=REPO_ID,
                    repo_type="dataset",
                    folder_path=LOCAL_DIR,
                    path_in_repo=".",
                    allow_patterns=batch,
                    commit_message=f"auto update {datetime.today():%Y-%m-%d %H:%M:%S} ({i}/{len(batches)})",
                )
                print(f"[hf_sync] 第 {i}/{len(batches)} 批上传完成（{len(batch)} 个文件）")
                break
            except Exception as e:
                last_err = e
                if attempt < UPLOAD_MAX_RETRIES:
                    wait = UPLOAD_BATCH_PAUSE * attempt + random.uniform(0, 2)
                    print(f"[hf_sync] 第 {i}/{len(batches)} 批上传失败（第 {attempt} 次尝试），{wait:.1f}s 后重试：{e}")
                    time.sleep(wait)
        else:
            print(f"[hf_sync] 第 {i}/{len(batches)} 批上传最终失败：{last_err}")
            raise RuntimeError(f"上传第 {i}/{len(batches)} 批失败：{last_err}") from last_err

        time.sleep(UPLOAD_BATCH_PAUSE)

    print("[hf_sync] 上传完成")


def main():
    if len(sys.argv) < 2:
        print("用法: python -m src.data_collection.hf_sync <download|upload|clear-redis-cache>")
        sys.exit(1)

    action = sys.argv[1]
    if action == "download":
        download_from_hf()
    elif action == "upload":
        upload_to_hf()
    elif action == "clear-redis-cache":
        from src.visualization.redis_cache import invalidate_all
        deleted = invalidate_all()
        print(f"[hf_sync] Redis 缓存已清空 ({deleted} keys)")
    else:
        print(f"未知操作: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
