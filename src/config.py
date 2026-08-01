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

JWT / 多租户鉴权配置（见 docs/JWT租户功能开发规范书.md §八）：
  - ``AUTH_MODE``  鉴权模式：legacy / hybrid（默认） / jwt。
  - ``JWT_SECRET``  HS256 密钥（≥32 字节；只从环境变量注入，禁止落仓库）。
  - ``JWT_SECRET_PREVIOUS``  轮换期旧密钥（仅用于校验，签发生效用当前密钥）。
  - ``JWT_ISSUER`` / ``JWT_AUDIENCE`` / ``JWT_ALGORITHM``  token 标准 claims 与算法。
  - ``JWT_ACCESS_TTL`` / ``JWT_REFRESH_TTL`` / ``JWT_LEEWAY``  有效期与时钟容差（秒）。
  - ``JWT_COOKIE_NAME`` / ``JWT_COOKIE_SECURE``  Refresh Cookie 名与 Secure 属性。
  - ``REFRESH_ROTATE``  Refresh Token 轮转（一次性使用）开关。
  - ``PASSWORD_MIN_LEN`` / ``BCRYPT_ROUNDS``  密码策略。
  - ``DEFAULT_TENANT_ID``  迁移脚本回填用的默认租户。
  - ``LEGACY_ACCOUNT_ID_AUTH``  过渡期是否允许 account_id 凭证。
取值合法性（TTL 上限、密钥长度等）的启动期校验在 src/auth/config.py。
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

# ===== Auth / JWT（规范书 §8.1；合法性校验见 src/auth/config.py）=====
AUTH_MODE = os.environ.get("AUTH_MODE", "hybrid")
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_SECRET_PREVIOUS = os.environ.get("JWT_SECRET_PREVIOUS", "")
JWT_ISSUER = os.environ.get("JWT_ISSUER", "wechatnum-api")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "wechatnum-clients")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TTL = int(os.environ.get("JWT_ACCESS_TTL", "900"))        # ≤ 3600
JWT_REFRESH_TTL = int(os.environ.get("JWT_REFRESH_TTL", "604800"))   # ≤ 30 天
JWT_LEEWAY = int(os.environ.get("JWT_LEEWAY", "30"))                 # 0-300
JWT_COOKIE_NAME = os.environ.get("JWT_COOKIE_NAME", "wn_refresh")
# Refresh Cookie 的 Secure 属性：本地 http 开发默认 false，生产 HTTPS 必须置 true（§10.2）
JWT_COOKIE_SECURE = os.environ.get("JWT_COOKIE_SECURE", "false").lower() not in ("false", "0", "no")
REFRESH_ROTATE = os.environ.get("REFRESH_ROTATE", "true").lower() not in ("false", "0", "no")
PASSWORD_MIN_LEN = int(os.environ.get("PASSWORD_MIN_LEN", "8"))
BCRYPT_ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "12"))
DEFAULT_TENANT_ID = os.environ.get("DEFAULT_TENANT_ID", "t_default")
LEGACY_ACCOUNT_ID_AUTH = os.environ.get("LEGACY_ACCOUNT_ID_AUTH", "true").lower() not in ("false", "0", "no")
