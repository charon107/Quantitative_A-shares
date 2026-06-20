#!/bin/bash
# 一键更新部署：拉取最新代码 → 按需同步依赖 → 重启看板服务
#
# 用法（在服务器上，项目任意位置均可）：
#   bash /home/wechatnum/Project/wechatnum/WechatNum/deploy/update.sh
# 或赋予可执行权限后：
#   ./deploy/update.sh

set -euo pipefail

cd "$(dirname "$0")/.."  # 进入项目根目录（WechatNum）
PROJECT_DIR="$(pwd)"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# root 直接调用 systemctl，否则用 sudo
if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL="systemctl"
else
    SYSTEMCTL="sudo systemctl"
fi

echo "[$(ts)] 项目目录：$PROJECT_DIR"

# 1) 拉取最新代码（仅快进，避免脏合并）
BEFORE="$(git rev-parse HEAD)"
echo "[$(ts)] 拉取 origin/main ..."
git pull --ff-only origin main
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

# 3) 重启看板服务
echo "[$(ts)] 重启 dashboard 服务 ..."
$SYSTEMCTL restart dashboard

# 4) 显示服务状态
sleep 2
$SYSTEMCTL --no-pager --full status dashboard | head -n 12 || true
echo "[$(ts)] 完成。访问 http://47.109.138.67:8501 （强制刷新 Ctrl+F5 避开缓存）"
