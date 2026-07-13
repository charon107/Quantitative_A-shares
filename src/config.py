"""全局配置常量（可用环境变量覆盖；仓库根 .env 会在模块加载时补进环境变量）。

  - ``KLINE_START_DATE``  日线入库起始日（默认 2013-01-01）。
    修改后需运行 scripts/reingest_all.py 回填历史数据到 DuckDB。
  - ``DASHBOARD_START_DATE``  看板全市场统计窗口起始日（默认 2025-01-01）。
    等权指数/涨跌停/MA 时长等全市场聚合按此窗口计算——13 年全历史的
    全市场聚合会耗尽 1.6GB 小服务器内存（尤其 pandas 侧不受 DuckDB
    memory_limit 约束）。全历史仅用于按单只股票查询的选股历史图。
  - ``SQL_API_TOKEN``  只读 SQL 网关（POST /api/sql）的 Bearer token；
    未配置时该端点关闭（404），见 src/api/routes/sql.py。
  - ``SQL_MAX_ROWS``  SQL 网关单次查询返回的最大行数（默认 200 万，超出截断）。
"""
import os


def _load_dotenv() -> None:
    """把仓库根目录 .env 里的键值补进环境变量（已设置的环境变量优先，不覆盖）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

START_DATE = os.environ.get("KLINE_START_DATE", "2013-01-01")
DASHBOARD_START_DATE = os.environ.get("DASHBOARD_START_DATE", "2025-01-01")
SQL_API_TOKEN = os.environ.get("SQL_API_TOKEN", "")
SQL_MAX_ROWS = int(os.environ.get("SQL_MAX_ROWS", "2000000"))
