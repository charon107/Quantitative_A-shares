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
from datetime import datetime
from huggingface_hub import snapshot_download, HfApi

# 配置：修改为你的 Hugging Face dataset 仓库地址
REPO_ID = "Charon107/stock-price"
LOCAL_DIR = "股价数据_parquet_fq"


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
            max_workers=16,  # 数据集是数千个小 parquet，提高并发缩短拉取时间
        )
        print(f"[hf_sync] 下载完成，本地目录: {LOCAL_DIR}")
    except Exception as e:
        print(f"[hf_sync] 下载跳过（可能是首次运行）: {e}")


def upload_to_hf():
    """
    把本地数据推送到 HF dataset，覆盖式更新。

    注意：upload_folder 只覆盖，不删除远端多出的文件。
    对这个场景（只增不删）足够用。
    """
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise RuntimeError("HF_TOKEN environment variable not set")

    if not os.path.exists(LOCAL_DIR):
        raise RuntimeError(f"本地目录不存在: {LOCAL_DIR}")

    try:
        print(f"[hf_sync] 上传数据到 {REPO_ID} ...")
        api = HfApi(token=hf_token)
        api.upload_folder(
            repo_id=REPO_ID,
            repo_type="dataset",
            folder_path=LOCAL_DIR,
            path_in_repo=".",
            commit_message=f"auto update {datetime.today():%Y-%m-%d %H:%M:%S}",
        )
        print(f"[hf_sync] 上传完成")
    except Exception as e:
        print(f"[hf_sync] 上传失败: {e}")
        raise


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
