"""全局配置常量（可用环境变量覆盖）。

  - ``KLINE_START_DATE``  日线入库起始日（默认 2013-01-01）。
    修改后需运行 scripts/reingest_all.py 回填历史数据到 DuckDB。
  - ``DASHBOARD_START_DATE``  看板全市场统计窗口起始日（默认 2025-01-01）。
    等权指数/涨跌停/MA 时长等全市场聚合按此窗口计算——13 年全历史的
    全市场聚合会耗尽 1.6GB 小服务器内存（尤其 pandas 侧不受 DuckDB
    memory_limit 约束）。全历史仅用于按单只股票查询的选股历史图。
"""
import os

START_DATE = os.environ.get("KLINE_START_DATE", "2013-01-01")
DASHBOARD_START_DATE = os.environ.get("DASHBOARD_START_DATE", "2025-01-01")
