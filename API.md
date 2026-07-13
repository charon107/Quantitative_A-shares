# 数据查询接口文档

本地 Python 直接查询服务器 DuckDB 的只读 SQL 网关，以及全库 15 张表的数据字典。

- **服务地址**：`http://47.109.138.67:8501`
- **鉴权**：请求头 `Authorization: Bearer <SQL_API_TOKEN>`（token 配置在仓库根 `.env`，与服务器一致；GitHub secrets 里有同一份）
- **权限**：只读。写库、改配置、读写服务器文件的 SQL 一律被拒绝

---

## 一、快速开始

### 1. 命令行（仓库根目录）

```bash
uv run python query.py "SELECT COUNT(*) AS n FROM kline"

# 结果落盘
uv run python query.py "SELECT * FROM stock_fundamental_quarterly WHERE code='sh.600660'" --csv fuyao.csv
uv run python query.py "SELECT * FROM stock_valuation_daily WHERE code='sh.600519'" --parquet maotai.parquet
```

### 2. Python / Jupyter

```python
from query import query_df   # 或 from scripts.remote_query import query_df

# 福耀玻璃全部季度基本面
df = query_df("SELECT * FROM stock_fundamental_quarterly WHERE code='sh.600660' ORDER BY end_date")

# 2025 年 ROE 最高的 20 只（年报口径）
top = query_df("""
    SELECT f.code, m.code_name, f.roe, f.net_profit/1e8 AS 净利润_亿
    FROM stock_fundamental f JOIN stock_meta m USING(code)
    WHERE f.year = 2025 ORDER BY f.roe DESC LIMIT 20
""")

# 结果可继续喂给本地 duckdb 加工
import duckdb
con = duckdb.connect()
con.register("t", df)
con.sql("SELECT year, AVG(roe) FROM t GROUP BY year")
```

### 3. 裸 HTTP（任意语言）

```bash
curl -X POST "http://47.109.138.67:8501/api/sql" \
  -H "Authorization: Bearer $SQL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT COUNT(*) AS n FROM kline"}' \
  -o result.arrow
# result.arrow 为 Arrow IPC stream，pyarrow/polars/duckdb 均可直接读
```

---

## 二、接口规格：`POST /api/sql`

### 请求

| 项 | 说明 |
|---|---|
| 方法 / 路径 | `POST /api/sql` |
| 请求头 | `Authorization: Bearer <token>`、`Content-Type: application/json` |
| Body | `{"sql": "<单条只读 SQL>"}` |

SQL 方言为 **DuckDB**（与 PostgreSQL 高度相似；支持 `SHOW TABLES`、`DESCRIBE 表名` 探索结构）。

### 响应

| 项 | 说明 |
|---|---|
| 成功 | `200`，Body 为 **Arrow IPC stream**（`application/vnd.apache.arrow.stream`） |
| 响应头 `X-Rows` | 返回行数 |
| 响应头 `X-Truncated` | `true` 表示结果超行数上限被截断（默认 200 万行），请在 SQL 里加过滤/LIMIT |

Python 读回：`pyarrow.ipc.open_stream(resp.content).read_all().to_pandas()`（`query_df` 已封装）。

### 错误码

| 状态码 | 含义 |
|---|---|
| `400` | SQL 错误（语法错/表不存在/被只读或外部访问限制拒绝/超内存上限），detail 带 DuckDB 原始报错 |
| `403` | token 缺失或不正确 |
| `404` | 服务器未配置 token（端点关闭） |

### 限制与注意

- **只读**：`INSERT/UPDATE/DELETE/CREATE` 等被连接层拒绝；`COPY TO 文件`、`ATTACH`、`read_csv/read_parquet` 等触碰服务器文件系统的语句被 `enable_external_access=false` 拒绝
- **单查询内存上限 400MB**（服务器只有 1.6GB 内存）：全表扫 `kline`（约 760 万行）这类查询请先聚合或过滤，直接 `SELECT *` 可能报内存错误
- **行数上限 200 万**：超出截断（看 `X-Truncated` 头）
- 8501 为明文 HTTP，token 在公网明文传输；库内只有公开市场数据，介意可另加 TLS 反代
- 本机走 Clash 类代理会 502（代理出口在海外），`query_df` 已默认直连不走系统代理

---

## 三、数据字典（15 张表）

**全库统一口径**：
- `code`：股票代码，`sh.600000` / `sz.000001` 风格；覆盖沪深主板（sh.60 / sz.00）
- **比率一律为小数**（0.15 = 15%），例外仅 kline 的 `pctChg`/`turn` 与估值表的 `dv_ratio`/`dv_ttm`（百分数）
- **金额一律为元**，例外：kline `amount`（千元）、估值表 `total_mv`/`circ_mv`（万元）、公司表 `reg_capital`（万元）
- 日期：`DATE` 类型列可直接比较；报告期 `end_date`/公告日 `ann_date` 为 `'YYYY-MM-DD'` 字符串

### 1. `kline` — 前复权日线（主表，约 760 万行，2013 起）

| 列 | 说明 |
|---|---|
| code, date | 主键 |
| open / high / low / close | 前复权价（元） |
| volume | 成交量（**手**） |
| amount | 成交额（**千元**） |
| pctChg | 涨跌幅（**百分数**，3.5 = +3.5%） |
| turn | 换手率（**百分数**） |

### 2. `stock_fundamental` — 年报基本面（选股用）

主键 `(code, year)`。

| 列 | 说明 |
|---|---|
| ann_date | 年报公告日 |
| roe | 净资产收益率（小数；tushare 普通口径，缺失年份用报表推算） |
| netprofit_yoy | 归母净利润同比（小数） |
| debt_ratio | **有息总债务/总资产**（总债务=短借+长借+应付债券），选股条件 3 |
| net_profit | 归母净利润（元） |
| cfo | 经营活动现金流净额（元） |

### 3. `stock_fundamental_quarterly` — 季度基本面（展示用，约 23 万行，1990s 起）

主键 `(code, end_date)`，`end_date` 为季末 `'YYYY-MM-DD'`，另有冗余 `year`/`quarter`(1-4) 列。
**累计列** = 年初至报告期末（财报原口径）；**`q_` 前缀 = 单季**（Q1=累计，Qn=同年相邻累计差分，上一季缺失则 NULL）。

| 列 | 说明 |
|---|---|
| ann_date | 财报公告日 |
| roe / roe_dt / roa | 累计 ROE / 扣非 ROE / 总资产收益率 |
| netprofit_margin / grossprofit_margin | 累计净利率 / 毛利率 |
| net_profit / profit_dedt / revenue | 累计归母净利润 / 扣非净利润 / 营收（元） |
| eps / bps | 每股收益 / 每股净资产（元） |
| debt_to_assets | 资产负债率（负债合计口径） |
| or_yoy / netprofit_yoy / dt_netprofit_yoy | 累计营收 / 归母净利润 / 扣非净利润同比 |
| cfo | 累计经营现金流净额（元） |
| total_assets / total_liab | 期末总资产 / 总负债·负债合计（元，时点值） |
| **total_debt** | 期末**有息总债务**=短借+长借+应付债券（元，与选股 debt_ratio 分子同口径） |
| q_roe / q_dt_roe | 单季 ROE / 扣非单季 ROE |
| q_netprofit_margin / q_gsprofit_margin | 单季净利率 / 毛利率 |
| q_net_profit / q_revenue / q_cfo | 单季净利润 / 营收 / 经营现金流（元） |
| q_sales_yoy / q_sales_qoq | 单季营收同比 / 环比 |
| q_netprofit_yoy / q_netprofit_qoq | 单季归母净利润同比 / 环比 |

注：fina_indicator 官方值优先；代理缺失段（2006–2011 等）用三大报表推算兜底，每股类/扣非类无兜底来源为 NULL。

### 4. `stock_valuation_daily` — 估值日频（约 930 万行，2013 起）

主键 `(code, date)`。

| 列 | 说明 |
|---|---|
| pe / pe_ttm | 市盈率（静态 / TTM） |
| pb | 市净率 |
| ps / ps_ttm | 市销率（静态 / TTM） |
| dv_ratio / dv_ttm | 股息率（**百分数**） |
| total_mv / circ_mv | 总市值 / 流通市值（**万元**） |

### 5. `stock_dividend` — 分红送股（仅已实施）

主键 `(code, end_date)`，`end_date` 为分红年度报告期（中期分红为 `xxxx-06-30`）。

| 列 | 说明 |
|---|---|
| ann_date | 预案公告日 |
| stk_div | 每股送转合计（股） |
| cash_div / cash_div_tax | 每股分红 税后 / **税前**（元） |
| record_date / ex_date / pay_date | 股权登记日 / 除权除息日 / 派息日 |

### 6. `stock_forecast` — 业绩预告

主键 `(code, end_date, ann_date)`（同一报告期多次修正均保留）。

| 列 | 说明 |
|---|---|
| type | 预增 / 预减 / 扭亏 / 首亏 / 续亏 / 续盈 / 略增 / 略减 |
| p_change_min / p_change_max | 预告净利润变动幅度下限 / 上限（小数） |
| net_profit_min / net_profit_max | 预告净利润下限 / 上限（元） |
| change_reason | 业绩变动原因 |

### 7. `stock_express` — 业绩快报（正式财报前的先行数据）

主键 `(code, end_date)`。列：`ann_date`、`revenue`/`operate_profit`/`total_profit`/`n_income`（元）、`diluted_eps`/`bps`（元）、`diluted_roe`、`yoy_sales`/`yoy_op`/`yoy_dedu_np`（小数）。

### 8. `selected_stocks` — 逐年选股池（策略输出）

主键 `(year, code)` + `code_name`。year 为选股年（用前一年财报窗口筛出）。

### 9. `index_daily` — 指数日线

主键 `(code, date)` + `close`。目前只有上证综指 `sh.000001`。

### 10. `stock_info` — 公司信息

主键 `code`。列：`code_name`/`fullname`/`area`/`industry`/`market`/`list_date`/`chairman`/`manager`/`secretary`/`reg_capital`（万元）/`setup_date`/`province`/`city`/`employees`/`website`/`email`/`office`/`main_business`/`introduction`/`business_scope`。

### 11. `stock_meta` — 代码 → 名称映射

主键 `code` + `code_name`（JOIN 取名称用）。

### 12. `ths_hot` — 同花顺人气榜（仅最新一日快照）

主键 `code`。列：`rank_no`/`current_price`/`pct_change`/`hot`/`concept`/`rank_reason`/`trade_date`。

### 13–15. 内部表（一般不用查）

`raw_kline`（未复权日线，重算前复权的底稿）、`adj_factor`（复权因子）、`meta_kv`（schema 版本等键值）。

---

## 四、常用查询示例

```sql
-- 探索：有哪些表 / 某表结构
SHOW TABLES;
DESCRIBE stock_fundamental_quarterly;

-- 某股最近 8 个季度的单季营收与净利润（亿元）
SELECT end_date, q_revenue/1e8 AS 营收, q_net_profit/1e8 AS 净利润, q_roe
FROM stock_fundamental_quarterly WHERE code='sh.600660'
ORDER BY end_date DESC LIMIT 8;

-- 2026 年选股池 + 各自最新估值
SELECT s.code, s.code_name, v.pe_ttm, v.pb, v.total_mv/1e4 AS 市值_亿
FROM selected_stocks s
JOIN stock_valuation_daily v ON s.code = v.code
WHERE s.year = 2026 AND v.date = (SELECT MAX(date) FROM stock_valuation_daily)
ORDER BY v.pe_ttm;

-- 连续 5 年股息率>3% 的公司（按分红年度聚合）
SELECT d.code, m.code_name, COUNT(*) AS 年数
FROM stock_dividend d JOIN stock_meta m USING(code)
WHERE d.end_date >= '2021-01-01' AND d.cash_div_tax > 0
GROUP BY d.code, m.code_name HAVING COUNT(*) >= 5;

-- 大表请先聚合再取回（内存上限 400MB）
SELECT code, MAX(date) AS 最新, COUNT(*) AS 天数 FROM kline GROUP BY code LIMIT 20;
```

---

## 五、其它 REST 端点（JSON，看板同款，无需 token）

| 端点 | 说明 |
|---|---|
| `GET /api/stocks/{code}/kline` | 单股前复权 K线 + MA |
| `GET /api/stocks/{code}/fundamental/quarterly` | 季度基本面（同表 3） |
| `GET /api/stocks/{code}/valuation` | 估值日频 |
| `GET /api/stocks/{code}/dividend` | 分红历史 |
| `GET /api/stocks/{code}/earnings` | 业绩预告 + 快报 |
| `GET /api/stocks/{code}/info` | 公司信息 |
| `GET /api/screening/years`、`/api/screening/{year}` | 选股年份 / 某年选股池 |
| `GET /api/export/kline.parquet` | 全量 K线 Parquet 下载 |

数据更新节奏：日线/估值/人气榜每交易日晚更新；财务五表（年报/季度/分红/预告/快报）随财报季（5/9/11 月）刷新。
