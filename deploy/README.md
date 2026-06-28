# 部署指南 — A股股价看板（DuckDB + FastAPI + React）

目标服务器：`47.109.138.67`，项目路径 `/root/Quantitative_A-shares/WechatNum`。
架构：FastAPI（uvicorn）同进程托管 `/api` 与前端静态 `frontend/dist`，端口 8501；DuckDB 为数据源；Redis 做 L2 缓存。

## 前置

- Linux + Python 3.11+ + `uv`（已在 `/root/.local/bin/uv`）
- Redis（已安装并运行）
- **前端构建在本地/CI 完成**（服务器内存小，不跑 Node）：本地 `cd frontend && npm ci && npm run build` 产出 `frontend/dist/`，再传到服务器同路径。

## 首次部署

```bash
cd /root/Quantitative_A-shares/WechatNum
git pull origin main
uv sync                       # 安装 duckdb/fastapi/uvicorn 等

# 1) 从历史 parquet 迁移到 DuckDB（一次性；之后由 refresh_data 增量入库）
uv run python scripts/migrate_parquet_to_duckdb.py \
    --base-dir 股价数据_parquet_fq --dest market.duckdb

# 2) 传入前端构建产物（本地构建后）
#    本地：scp -r frontend/dist root@47.109.138.67:/root/Quantitative_A-shares/WechatNum/frontend/

# 3) 安装 systemd 单元
cp deploy/api.service /etc/systemd/system/
# refresh_data：把 deploy/refresh_data.sh 作为 oneshot service，由 timer 触发
systemctl daemon-reload
systemctl enable --now api
systemctl enable --now refresh_data.timer   # 每日入库

# 4) 预热缓存
uv run python deploy/warmup_redis.py
```

## refresh_data oneshot service（供 timer 调用）

```ini
# /etc/systemd/system/refresh_data.service
[Unit]
Description=Refresh A股数据（tushare -> DuckDB）+ 清/预热 Redis
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/root/Quantitative_A-shares/WechatNum
Environment="TUSHARE_TOKEN=YOUR_TOKEN"
Environment="DUCKDB_PATH=/root/Quantitative_A-shares/WechatNum/market.duckdb"
ExecStart=/root/Quantitative_A-shares/WechatNum/deploy/refresh_data.sh
```

> 需在该 service 配置 `TUSHARE_TOKEN`（及可选 `TUSHARE_API_URL` 代理网关）。

## 日常更新

```bash
bash /root/Quantitative_A-shares/WechatNum/deploy/update.sh   # git pull + 按需 uv sync + 重启 api
# 前端有改动时，本地重新 npm run build 并 scp dist/ 覆盖
```

## 验证

```bash
systemctl status api
journalctl -u api -f
curl -s http://127.0.0.1:8501/api/status
# 浏览器：http://47.109.138.67:8501
free -h        # 确认内存正常（DuckDB 查询期 < memory_limit）
```

## 防火墙

```bash
ufw allow 8501       # 或 iptables 放行 8501
```

## 备注

- DuckDB 并发：API 用只读短连接；入库 `refresh_data` 写临时库后原子替换，避免争锁。
- 内存：`api.service` 设 `DUCKDB_MEMORY_LIMIT=400MB`、uvicorn 单 worker，适配 1.6GB 机器。
- 旧的 `dashboard.service`（Streamlit）已废弃；如仍在系统里：`systemctl disable --now dashboard` 后删除单元文件。
