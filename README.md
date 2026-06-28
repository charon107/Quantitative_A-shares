# WechatNum — A股股价看板

前复权日线行情看板。数据源 tushare，存储 **DuckDB**，后端 **FastAPI**，前端 **React (Vite + Tailwind + ECharts)**，Anthropic 浅色暖调设计。

## 架构

```
tushare ──每日入库──▶ DuckDB(market.duckdb, 服务器唯一真源)
                          │ 只读连接 + SQL 聚合
                          ▼
React SPA  ◀── JSON ── FastAPI(/api/*) ◀── Redis L2 缓存(重算结果)
   (静态 dist/ 由 FastAPI 同进程托管, 端口 8501)
```

- **数据库**：DuckDB 单文件，列式聚合，零常驻开销（适配小内存服务器）。
- **缓存**：Redis L2（重算结果序列化为 parquet bytes）；不可用时优雅降级为实时计算。
- **前端**：纯 SPA，构建成静态 `dist/` 由 FastAPI 同进程托管，运行时无 Node。

## 目录结构

```
src/
├── db.py                       # DuckDB 仓储层（只读短连接 / 入库 upsert / 原子替换）
├── metrics.py                  # 纯计算（SQL 聚合，被 API 复用）
├── cache.py                    # Redis L2 缓存
├── api/                        # FastAPI：main + schemas + routes(market/stocks/rankings/analytics)
├── analysis/ma5_above_ma10_duration.py   # MA5>MA20 多头时长统计（纯函数）
└── data_collection/
    ├── stock_price.py          # tushare → DuckDB 增量入库
    └── tushare_client.py       # tushare 封装（token/限流/熔断/前复权）
frontend/                       # Vite + React + TS + Tailwind + ECharts
scripts/migrate_parquet_to_duckdb.py   # 一次性：旧 parquet → DuckDB
deploy/                         # systemd 单元 + 数据刷新 + 缓存预热
tests/                          # pytest（metrics + API + 分析）
```

## 本地开发

### 后端

```bash
uv sync --all-extras

# 首次：从历史 parquet 建库（或在服务器已迁移好的库上跳过）
uv run python scripts/migrate_parquet_to_duckdb.py --base-dir 股价数据_parquet_fq --dest market.duckdb

# 启动 API（开发期 8000）
DUCKDB_PATH=market.duckdb uv run uvicorn src.api.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173 ，/api 代理到 127.0.0.1:8000
```

### 生产构建（前端静态产物，本地/CI 构建，不在服务器跑 Node）

```bash
cd frontend && npm run build      # 产出 frontend/dist/
# 之后 FastAPI 会自动托管 dist/（见 src/api/main.py）
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/market/breadth` | 市场宽度（涨跌家数 / 涨跌比） |
| GET | `/api/market/equal-weight-index?start=` | 等权指数累计收益 |
| GET | `/api/market/limit-up-down` | 每日涨停/跌停家数 |
| GET | `/api/stocks/search?q=` | 代码/名称模糊搜索 |
| GET | `/api/stocks/{code}/kline` | 个股 K线（含 MA5/10/20/60） |
| GET | `/api/stocks/{code}/volatility?window=` | 滚动年化波动率 |
| GET | `/api/rankings?metric=&n=&ascending=` | 排行榜（pctChg/amount/turn） |
| GET | `/api/ma-duration` | MA5>MA20 多头时长分布 |
| GET | `/api/status` | 数据新鲜度 / 覆盖 / Redis 状态 |

## 数据更新

每日由 systemd timer 触发 `deploy/refresh_data.sh`：跑 `stock_price.py`（tushare → DuckDB 增量入库）→ 清 Redis 缓存。需在服务器配置环境变量 `TUSHARE_TOKEN`（及可选 `TUSHARE_API_URL` 代理网关）。

## 部署

见 [deploy/README.md](deploy/README.md)。要点：服务器装 DuckDB（`uv sync`）、建库、`api.service` 跑 uvicorn 托管 `/api` + 前端 `dist/`，Redis 做缓存。

## 测试

```bash
uv run pytest          # metrics（DuckDB 夹具）+ API（TestClient）+ 分析纯函数
```

> 仅供量化研究，不构成投资建议。
