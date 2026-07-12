#!/bin/bash
# 一键更新部署：更新代码 → 按需同步依赖 → 重启看板服务
#
# 用法（在服务器上，项目任意位置均可）：
#   bash deploy/update.sh /tmp/ingest_fund/repo.bundle   # bundle 模式（推荐，零网络）：
#                                                        # 从 runner scp 来的 git bundle
#                                                        # 单文件本地 fetch+merge
#   bash deploy/update.sh                                # 兜底：git pull origin（服务器连
#                                                        # GitHub 时断时续，带 5 次重试）

set -euo pipefail

cd "$(dirname "$0")/.."  # 进入项目根目录（WechatNum）
PROJECT_DIR="$(pwd)"
BUNDLE="${1:-}"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# root 直接调用 systemctl，否则用 sudo
if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL="systemctl"
else
    SYSTEMCTL="sudo systemctl"
fi

echo "[$(ts)] 项目目录：$PROJECT_DIR"

# 1) 更新代码（仅快进，避免脏合并）
BEFORE="$(git rev-parse HEAD)"
if [ -n "$BUNDLE" ]; then
    # bundle 模式：从本地单文件取代码，服务器无需连 GitHub
    [ -f "$BUNDLE" ] || { echo "[$(ts)] bundle 不存在：$BUNDLE"; exit 1; }
    echo "[$(ts)] 从 bundle 更新：$BUNDLE ..."
    git bundle verify "$BUNDLE"
    git fetch "$BUNDLE" main
    git merge --ff-only FETCH_HEAD
else
    echo "[$(ts)] 拉取 origin/main（连 GitHub 偶发超时，重试 5 次）..."
    ok=""
    for i in 1 2 3 4 5; do
        git pull --ff-only origin main && ok=1 && break
        echo "[$(ts)] pull 失败，重试 $i/5 ..."
        sleep 8
    done
    [ -n "$ok" ]
fi
AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
    echo "[$(ts)] 已是最新（$AFTER），无新提交。"
else
    echo "[$(ts)] 更新：$BEFORE → $AFTER"
fi

# 2) 依赖变更（pyproject.toml 或 uv.lock）才同步，否则跳过
if [ "$BEFORE" != "$AFTER" ] \
    && git diff --name-only "$BEFORE" "$AFTER" | grep -qE '(^|/)(pyproject\.toml|uv\.lock)$'; then
    echo "[$(ts)] 检测到依赖变更，运行 uv sync ..."
    uv sync
else
    echo "[$(ts)] 依赖无变更，跳过 uv sync。"
fi

# 3) 重启 API 服务
echo "[$(ts)] 重启 api 服务 ..."
$SYSTEMCTL restart api

# 4) 显示服务状态
sleep 2
$SYSTEMCTL --no-pager --full status api | head -n 12 || true
echo "[$(ts)] 完成。访问 http://47.109.138.67:8501 （强制刷新 Ctrl+F5 避开缓存）"
