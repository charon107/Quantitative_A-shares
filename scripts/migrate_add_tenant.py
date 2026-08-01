"""多租户迁移脚本（规范书 §9.1）：为既有 paper.duckdb 引入租户模型。

用法：
    uv run python scripts/migrate_add_tenant.py

环境变量：
    MIGRATE_ADMIN_PASSWORD  默认管理员 admin 的初始密码（必填，禁止硬编码；
                            admin 已存在时可省略）
    DEFAULT_TENANT_ID       回填用默认租户（默认 t_default）
    PAPER_DUCKDB_PATH       paper.duckdb 路径（默认项目根 paper.duckdb）

步骤（全部幂等，可重复执行，已存在的对象跳过）：
    1. 执行新 schema（tenants / users / user_account_grants 建表，accounts 加 tenant_id 列）；
    2. 创建默认租户 t_default（若不存在）；
    3. UPDATE accounts SET tenant_id = 默认租户 WHERE tenant_id IS NULL；
    4. 创建默认管理员 admin（admin 角色，密码取 MIGRATE_ADMIN_PASSWORD）；
    5. 打印迁移摘要。

注意：执行前请备份——cp paper.duckdb paper.duckdb.bak.YYYYMMDD（§9.3）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.auth import config, password, store as auth_store  # noqa: E402
from src.paper_trading import store  # noqa: E402


def main() -> None:
    db_path = store.default_path()
    print(f"[migrate] paper 库: {db_path}")

    # 1. 新表 DDL + accounts.tenant_id（init_schema 幂等，connect 内自动执行）
    with store.connect(path=db_path) as conn:
        store.init_schema(conn)

    # 2. 默认租户
    tenant_id = config.DEFAULT_TENANT_ID
    existed = auth_store.get_tenant(tenant_id) is not None
    auth_store.create_tenant(tenant_id, "默认租户")
    print(f"[migrate] 默认租户 {tenant_id}: {'已存在（跳过）' if existed else '已创建'}")

    # 3. 回填 accounts.tenant_id
    with store.connect(path=db_path) as conn:
        backfilled = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE tenant_id IS NULL").fetchone()[0]
        conn.execute("UPDATE accounts SET tenant_id = ? WHERE tenant_id IS NULL", [tenant_id])
        total = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    print(f"[migrate] accounts 回填 tenant_id: {backfilled} 行（共 {total} 个账户）")

    # 4. 默认管理员（幂等：已存在则跳过）
    if auth_store.get_user_by_username("admin", tenant_id):
        print("[migrate] 默认管理员 admin: 已存在（跳过）")
    else:
        admin_password = os.environ.get("MIGRATE_ADMIN_PASSWORD", "")
        if len(admin_password) < config.PASSWORD_MIN_LEN:
            print(f"[migrate] 错误：MIGRATE_ADMIN_PASSWORD 未设置或长度 <{config.PASSWORD_MIN_LEN}，"
                  "无法创建 admin（禁止硬编码密码）", file=sys.stderr)
            sys.exit(1)
        user = auth_store.create_user(tenant_id, "admin",
                                      password.hash_password(admin_password), ["admin", "trader"])
        print(f"[migrate] 默认管理员 admin: 已创建（user_id={user['user_id']}，角色 admin,trader）")

    # 5. 摘要
    print("[migrate] 完成。建议确认后删除迁移前备份 paper.duckdb.bak.*")


if __name__ == "__main__":
    main()
