"""全局配置常量（可用环境变量覆盖）。

  - ``KLINE_START_DATE``  日线入库/统计起始日（默认 2025-01-01）。
    当初取 2025 年起是为控制库体积与查询内存，适配 1.6GB 小服务器；
    如需扩大历史窗口，改此环境变量后用 scripts/reingest_all.py 回填。
"""
import os

START_DATE = os.environ.get("KLINE_START_DATE", "2025-01-01")
