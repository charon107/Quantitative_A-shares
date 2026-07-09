"""全局配置常量（可用环境变量覆盖）。

  - ``KLINE_START_DATE``  日线入库/统计起始日（默认 2013-01-01）。
    修改后需运行 scripts/reingest_all.py 回填历史数据到 DuckDB。
"""
import os

START_DATE = os.environ.get("KLINE_START_DATE", "2013-01-01")
