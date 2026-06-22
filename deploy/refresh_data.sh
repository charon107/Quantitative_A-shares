#!/bin/bash
# 服务器定时任务：从 Hugging Face 拉取最新数据

set -e  # 任何错误则退出

export HF_TOKEN="${HF_TOKEN:-}"  # 从环境变量读取 token

if [ -z "$HF_TOKEN" ]; then
    echo "ERROR: HF_TOKEN not set"
    exit 1
fi

cd "$(dirname "$0")/.."  # 进入项目根目录

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting data refresh..."

# 使用 uv run 执行 hf_sync download
uv run python -m src.data_collection.hf_sync download

if [ $? -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Data refresh completed successfully"
    # 数据更新后清空 Redis 缓存，下次页面加载从新数据重算
    uv run python -m src.data_collection.hf_sync clear-redis-cache || true
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Data refresh failed"
    exit 1
fi
