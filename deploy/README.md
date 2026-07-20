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

# 1) 从历史 parquet 迁移到 DuckDB（一次性；之后由 GitHub Actions 增量入库）
uv run python scripts/migrate_parquet_to_duckdb.py \
    --base-dir 股价数据_parquet_fq --dest market.duckdb

# 2) 传入前端构建产物（本地构建后）
#    本地：scp -r frontend/dist root@47.109.138.67:/root/Quantitative_A-shares/WechatNum/frontend/

# 3) 安装 systemd 单元
cp deploy/api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now api

# 4) 预热缓存
uv run python deploy/warmup_redis.py
```

## 数据入库（由 GitHub Actions 驱动，服务器无 timer）

服务器**不**主动抓数据——「服务器 → tushare 网关」网络不通。全部入库由 runner 发起：

| workflow | 频率 | 服务器侧动作 |
|---|---|---|
| `daily_ingest.yml` | 工作日 17:17 / 19:17（北京） | `scripts/load_all_parquet.py` 重算前复权入库 + 清/预热 Redis |
| `fundamentals_refresh.yml` | 财报季次月上旬 | `scripts/load_fundamentals.py` + 跑逐年选股 |
| `valuation_backfill.yml` | 手动（首次/重建） | `scripts/load_valuation.py` |
| `db_backup.yml` | 每周 | `deploy/export_backup.py` 导出快照供 runner 拉回上传 HF |

> 早期的 `refresh_data.sh` + `refresh_data.timer`（服务器侧直连 tushare）已随该架构切换废弃并删除。
> 若服务器上仍有残留单元：`systemctl disable --now refresh_data.timer` 后删除
> `/etc/systemd/system/refresh_data.{service,timer}`。

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

## 备份与恢复

- **本地滚动备份（自动）**：每次入库 `atomic_swap` 前，旧库自动 rename 进库文件同目录的
  `backups/`（`market-*.duckdb` 每日保留 3 份 + `weekly-*.duckdb` 周备份保留 4 份，
  `BACKUP_KEEP_DAILY`/`BACKUP_KEEP_WEEKLY` 可调）。误操作后直接把对应备份文件
  拷回 `market.duckdb` 并重启 api 即可回滚。
- **异地备份（每周）**：`.github/workflows/db_backup.yml` 每周把全表 zstd parquet 快照
  上传到 Hugging Face 私有 dataset（需 secrets `HF_TOKEN` + `HF_BACKUP_REPO`），保留 8 份。
- **从快照恢复**：下载某个 `snapshots/YYYY-MM-DD/` 目录到服务器后：
  `DUCKDB_PATH=... uv run python scripts/restore_from_backup.py <目录>`（自动校验 manifest 行数）。
- **schema 迁移**：`scripts/migrate_schema_v2.py`（幂等，版本号存 `meta_kv`）；
  迁移前后用 `scripts/db_size_report.py` 对比体积。

## 备注

- DuckDB 并发：API 用只读短连接；入库脚本写临时库后原子替换，避免争锁。
- 内存：`api.service` 设 `DUCKDB_MEMORY_LIMIT=400MB`、uvicorn 单 worker，适配 1.6GB 机器。
- 旧的 `dashboard.service`（Streamlit）已废弃；如仍在系统里：`systemctl disable --now dashboard` 后删除单元文件。
