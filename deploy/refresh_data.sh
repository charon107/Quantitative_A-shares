#!/bin/bash
# 服务器定时任务：从 tushare 增量入库到 DuckDB，并清空 Redis 缓存。
#
# 需要环境变量 TUSHARE_TOKEN（及可选 TUSHARE_API_URL 代理网关）。

set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.."  # 进入项目根目录

# 加载 token 等凭证（供 SSH 非登录 shell / CI 触发时使用）
if [ -f "$HERE/tushare.env" ]; then
    set -a
    . "$HERE/tushare.env"
    set +a
fi
# 确保 uv 在 PATH（CI/cron 的精简 PATH 里可能没有）
export PATH="/root/.local/bin:$PATH"

if [ -z "${TUSHARE_TOKEN:-}" ]; then
    echo "ERROR: TUSHARE_TOKEN 未设置（检查 deploy/tushare.env）"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始入库（tushare -> DuckDB）..."
uv run python -m src.data_collection.stock_price

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 入库完成，清空 Redis 缓存..."
uv run python -m src.cache clear || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 预热 Redis 缓存..."
uv run python deploy/warmup_redis.py || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 完成。"
