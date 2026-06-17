# WechatNum — A股量化交易研究

基于微信指数 + 涨停策略的 A 股量化研究框架。核心思路：用微信指数衡量市场对某股票的关注热度，结合涨停信号做量化选股与回测。

## 目录结构

```
WechatNum/
├── src/
│   ├── data_collection/     # 数据采集
│   │   ├── wechat_index.py      # 微信指数批量爬取（自动增量更新）
│   │   ├── wechat_index_gui.py  # 微信指数爬取（原版/GUI）
│   │   ├── stock_price.py       # A股日线前复权 K线更新（baostock）
│   │   ├── limit_up_updater.py  # 涨停池更新（akshare，追加到 data/limit_up/）
│   │   └── limit_up_history.py  # 历史涨停生成（从 parquet 扫描）
│   ├── strategies/          # 交易策略回测
│   │   ├── limit_up_ma.py       # 涨停+MA多头策略（主策略）
│   │   ├── breakout_60d.py      # 60日高点突破策略
│   │   ├── next_day_open.py     # 昨日触及涨停次日开盘买入
│   │   └── next_day_failed_zt.py # 昨日触及涨停但未封板次日策略
│   ├── optimization/        # 参数优化
│   │   └── grid_search.py       # 网格搜索（持有期/止盈止损）
│   ├── analysis/            # 数据分析
│   │   ├── wechat_backtest.py   # 微信指数大回测
│   │   ├── surf_return.py       # SURF 选股收益计算
│   │   └── parquet_columns.py   # parquet 字段查看工具
│   └── visualization/       # 可视化
│       ├── wechat_plot.py       # 单股微信指数曲线
│       ├── kline_wechat_plot.py # K线+成交量+微信指数三联图
│       ├── surf_kline_plot.py   # SURF 选股股价图
│       └── batch_plot.py        # 批量绘图
├── data/
│   ├── limit_up/
│   │   ├── 2023.csv             # 2023年涨停记录
│   │   ├── 2024.csv             # 2024年涨停记录
│   │   └── 2025_2026.csv        # 2025-2026年涨停记录（持续更新）
│   └── concepts/
│       └── ths_concept_board.csv # 同花顺概念板块
├── results/                 # 回测结果（按年分类）
│   ├── 2022/
│   ├── 2023/
│   ├── 2024/
│   └── 2025/
├── config/
│   └── wechat_search_config.ini # 微信指数 API 凭证（本地保留，不入库）
├── pyproject.toml
└── uv.lock
```

> **大型数据目录**（`沪深主板微信指数/`、`股价数据_parquet_fq/`、`parquet_A股_2007-2024_Q4/`）不进 git，在 `.gitignore` 中排除。

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置微信指数凭证

编辑 `config/wechat_search_config.ini`：

```ini
[DEFAULT]
openid = <你的openid>
search_key = <你的search_key>
```

凭证获取方式：用 **Fiddler** 对微信小程序抓包，找 `search.weixin.qq.com/cgi-bin/wxaweb/wxindex` 的请求 Body。凭证约每周失效，需重新抓取。

### 3. 采集数据

```bash
# 更新微信指数（增量）
.venv\Scripts\python.exe src/data_collection/wechat_index.py

# 更新 A 股日线 K 线
.venv\Scripts\python.exe src/data_collection/stock_price.py

# 更新涨停记录（当日到今天）
.venv\Scripts\python.exe src/data_collection/limit_up_updater.py
```

### 4. 运行策略回测

```bash
# 涨停 + MA 多头策略（主策略）
.venv\Scripts\python.exe src/strategies/limit_up_ma.py

# 60日高点突破策略
.venv\Scripts\python.exe src/strategies/breakout_60d.py
```

### 5. 参数优化

```bash
# 先确保 results/2025/full_year_backtest_enhanced.xlsx 存在
.venv\Scripts\python.exe src/optimization/grid_search.py
```

## 注意事项

- 所有脚本需从**项目根目录**运行（保证相对路径正确）
- baostock 已针对 pandas 2.x 打 patch（`df.append` → `pd.concat`）
- 微信指数凭证不入 git，仅存 `config/wechat_search_config.ini`（已加入 .gitignore）
