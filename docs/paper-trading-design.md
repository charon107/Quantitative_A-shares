# 模拟盘详设与接口契约（开发依据）

> 依据《模拟盘功能开发计划书.md》§11 评审结论制定。后端、前端、测试均以此契约为准。

## 1. 存储：paper.duckdb

- 路径：环境变量 `PAPER_DUCKDB_PATH`，默认项目根 `paper.duckdb`（与 `market.duckdb` 同级，已确认 .gitignore 需加入该文件）。
- 连接：短连接（每次操作开→写→关），写操作遇到 DuckDB 文件锁冲突时有限重试（如 5 次 × 0.5s）。行情库 `market.duckdb` 一律只读（复用 `src/db.py` 的 `query_df` / `connect(read_only=True)`）。
- schema 由 `src/paper_trading/store.py` 的 `init_schema(conn)` 幂等创建（CREATE TABLE IF NOT EXISTS）。

### 表结构

- `accounts(account_id VARCHAR PK, name VARCHAR, init_cash DOUBLE, cash DOUBLE, frozen DOUBLE, created_at TIMESTAMP, status VARCHAR)` — cash=可用资金，frozen=冻结资金；总资产=cash+frozen+持仓市值。
- `orders(order_id VARCHAR PK, account_id VARCHAR, request_id VARCHAR, code VARCHAR, side VARCHAR('buy'/'sell'), price_type VARCHAR('market'/'limit'), limit_price DOUBLE, qty INTEGER, status VARCHAR('pending'/'filled'/'cancelled'/'expired'/'rejected'), reject_reason VARCHAR, ref_price DOUBLE, frozen_amount DOUBLE, created_at TIMESTAMP, updated_at TIMESTAMP)` — 唯一约束 `(account_id, request_id)` 用于幂等。frozen_amount：买单提交时冻结的资金额。
- `fills(fill_id VARCHAR PK, order_id VARCHAR, account_id VARCHAR, code VARCHAR, side VARCHAR, price DOUBLE, qty INTEGER, amount DOUBLE, commission DOUBLE, stamp_tax DOUBLE, fee DOUBLE, trade_date DATE, created_at TIMESTAMP)` — amount=price*qty；fee=commission+stamp_tax。
- `positions(account_id VARCHAR, code VARCHAR, qty INTEGER, cost_price DOUBLE, updated_at TIMESTAMP, PK(account_id, code))` — 买入摊薄成本（移动加权平均）。
- `cash_flows(flow_id VARCHAR PK, account_id VARCHAR, type VARCHAR('freeze'/'unfreeze'/'buy'/'sell'/'reset'), amount DOUBLE, balance_after DOUBLE, ref_id VARCHAR, created_at TIMESTAMP)` — balance_after 为变动后可用资金。
- `equity_snapshots(account_id VARCHAR, trade_date DATE, cash DOUBLE, frozen DOUBLE, market_value DOUBLE, total_asset DOUBLE, PK(account_id, trade_date))` — 每次撮合运行后按当日收盘价生成；账户创建日生成首条（cash=init_cash）。
- `account_resets(reset_id VARCHAR PK, account_id VARCHAR, snapshot_json VARCHAR, created_at TIMESTAMP)` — 重置前总览快照（JSON 字符串）。

## 2. 交易规则（`src/paper_trading/config.py`，全部参数化常量）

- 整手：买入 100 股整数倍；卖出也须 100 整数倍（MVP 不产生零股）；单笔 ≤ 1,000,000 股。
- 涨跌停幅度（按 code 前缀 + 名称推断）：
  - `sh.688`/`sh.689` 科创板：±20%
  - `sz.300`/`sz.301` 创业板：±20%
  - `sh.6*`/`sz.0*`/`sz.001/002/003` 主板：±10%；名称含 "ST"（`stock_meta.code_name`）的主板股：±5%（科创板 ST 仍 ±20%）
  - 北交所：当前数据集无 bj 代码，规则预留 ±30% 但默认无对象。
- 涨跌停判定：用 `kline.pctChg`（百分数，如 10.5）。`pctChg >= limit_pct` 视为涨停（买单不成交）；`pctChg <= -limit_pct` 视为跌停（卖单不成交）。留 0.3 个百分点的 epsilon 防浮点误差（如 9.7% 主板不算涨停）。
- 停牌：该 code 在撮合日 `kline` 无行 → 当日不撮合，委托保留 pending（市价单）或当日作废（限价单，MVP 当日有效）。
- 费用：佣金 `max(5.0, amount * 0.00025)` 双向；印花税卖出 `amount * 0.0005`；过户费 MVP 并入佣金（配置项预留，默认 0）。
- 委托有效期：限价单当日有效，撮合日未成交 → `expired` 并解冻。市价单当日停牌未成交可顺延（pending 保留）。
- 资金不足：买单在撮合时实际成交额+费用 > 可用+该单冻结 → `rejected`（原因 insufficient_funds）并解冻。

## 3. 撮合（`src/paper_trading/matcher.py`）

纯函数风格，不依赖 FastAPI：

```
match_day(market_con_ro, paper_con, trade_date, config) -> MatchResult
```

流程：
1. 取全部 `status='pending'` 委托（只处理 created_at <= trade_date 的）。
2. 对每个有委托的 code，查 `kline` 当日行（close, pctChg）。
3. 无行情（停牌）→ 市价单跳过保留；限价单 expire。
4. 市价单：涨停不买/跌停不卖（跳过保留至下一交易日），否则按收盘价成交。
5. 限价买单：close ≤ limit_price 且非涨停 → 按 close 成交；限价卖单：close ≥ limit_price 且非跌停 → 按 close 成交；否则 expire。
6. 成交：写 fills、更新 positions（买入摊薄成本；卖出按摊薄成本结转）、解冻并扣款/到账、写 cash_flows、更新 orders。
7. 全部账户生成 equity_snapshots（按 trade_date 收盘价估值）。
8. 支持「回放」：传入任意历史 trade_date 重算（调用方保证传入干净的测试库）。

下单时校验（service 层，先于落库）：
- 仅 `sh.`/`sz.` 前缀、存在于 `stock_meta`。
- 买入预估金额（qty × ref_price + 预估费用）≤ 可用资金；ref_price=限价或最新收盘价。
- 卖出 qty ≤ 可卖 = 持仓 qty − 该 code 其他 pending 卖单合计（T+1 由撮合时机天然保证，见计划书 §11.3）。
- 限价须在按最近收盘价推算的涨跌停范围内。
- 买入冻结 frozen_amount = qty × ref_price + 预估费用；成交时多退少补（先解冻再按实际扣款）。

## 4. API（`src/api/routes/paper.py`，`APIRouter(prefix="/paper")`，注册于 main.py）

统一约定：account_id 为 path 参数即能力凭证；写接口 body 含 `request_id` 幂等；错误用 HTTPException（400 参数/校验错、404 账户不存在、409 冲突）。分页参数 `limit`(默认50,≤200) `offset`，列表响应 `{items: [...], total: n}`。

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/accounts` | `{name, init_cash}` | `{account_id, name, init_cash, cash, frozen, created_at}` |
| GET | `/accounts/{aid}/overview` | — | `{account_id, name, init_cash, cash, frozen, market_value, total_asset, total_pnl, total_return_pct, position_count, asof_date}` |
| POST | `/accounts/{aid}/reset` | `{confirm: true}` | `{ok: true, reset_id}` |
| GET | `/accounts/{aid}/positions` | — | `{items: [{code, name, qty, sellable_qty, cost_price, last_close, market_value, pnl, pnl_pct}]}` |
| POST | `/accounts/{aid}/orders` | `{request_id, code, side, price_type, limit_price?, qty}` | order 对象（含 order_id/status/frozen_amount） |
| DELETE | `/accounts/{aid}/orders/{order_id}` | — | `{ok: true}`（仅 pending 可撤，解冻） |
| GET | `/accounts/{aid}/orders?status=&limit=&offset=` | — | `{items: [order+code_name], total}` |
| GET | `/accounts/{aid}/fills?limit=&offset=` | — | `{items: [fill+code_name], total}` |
| GET | `/accounts/{aid}/cash-flows?limit=&offset=` | — | `{items: [flow], total}` |
| GET | `/accounts/{aid}/equity-curve?start=` | — | `{curve: [{date, total_asset, return_pct}], benchmark: [{date, value}]}`，benchmark 复用 `metrics.equal_weighted_index`，同区间归一化起点=0% |
| GET | `/accounts/{aid}/metrics` | — | `{total_return_pct, annualized_return_pct, max_drawdown_pct, win_rate}`（由净值曲线+fills 计算） |

CORS：`main.py` 的 `allow_methods` 放宽为 `["GET","POST","DELETE"]`。

## 5. 每日撮合接入

- `scripts/paper_match_daily.py [YYYY-MM-DD]`：默认取 `kline` 的 `MAX(date)` 作为撮合日；调用 matcher，输出成交笔数；结束后调用 `cache.invalidate_all()`。paper.duckdb 不存在时自动建库；无 pending 委托时仍刷新净值快照。
- `.github/workflows/daily_ingest.yml`：在服务器 load 步骤之后加一步执行该脚本（先判断 `paper.duckdb` 存在或无条件执行均可，脚本本身幂等：同一 trade_date 重跑不产生重复成交——撮合前将当日已撮合标记/或由“只处理 pending”天然幂等）。

## 6. 前端（`frontend/src/pages/PaperTrade.tsx` 单文件 + `App.tsx` PAGES 注册「模拟盘」）

- `api/client.ts` 增加 `post/del` 封装（复用现有错误抛出风格）与 paper 相关 hooks（`@tanstack/react-query` mutation + 写后 invalidate）。
- localStorage key `paper_account_id` 存账户 UUID；无则显示创建表单（名称、初始资金 10万/50万/100万/500万/自定义，默认100万）；页头显示账户 ID 可复制（备份提示）。
- 区块：KpiCard×4（总资产/可用资金/持仓市值/累计盈亏）→ 净值曲线 vs 等权指数（RangeTabs 1M/3M/6M/全部，ECharts 复用 `theme/echarts.ts`）→ 持仓表格（行内「卖出」预填下单面板）→ 下单面板（SearchBox 复用、买卖切换、市价/限价、数量、预估金额与费用、可用提示）→ Tab 记录区（委托/成交/资金流水，含撤单按钮）→ 底部固定风险披露文案（计划书 §9 原文）。
- 风格遵循 DESIGN.md：浅色暖调、陶土强调、红涨绿跌、等宽数字、无 emoji。
- 重置按钮：二次确认（window.confirm 即可）。

## 7. 测试

- `tests/paper_trading/`：matcher 单测（市价/限价/涨跌停/停牌/T+1/整手/最低佣金/印花税/过期/资金不足）、资金对账（可用+冻结+Σ流水一致性）、回放用例；fixture 仿 `tests/conftest.py` 用 tmp_path 双库（market + paper）。
- `tests/test_paper_api.py`：TestClient 全路由 + request_id 幂等 + 撤单/重置。
- 不改动任何现有测试。
