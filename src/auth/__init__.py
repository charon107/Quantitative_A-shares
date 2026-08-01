"""JWT 鉴权 + 多租户模块（规范书：docs/JWT租户功能开发规范书.md）。

- ``config``        JWT 配置加载与启动期校验（§四/§八）
- ``jwt_handler``   token 签发 / 校验 / 撤销黑名单（§4.5/§4.6/§4.8）
- ``models``        Principal / TokenPair / LoginIn 等 Pydantic 模型（§6.2）
- ``password``      bcrypt 密码哈希（§10.6）
- ``store``         tenants / users / user_account_grants 持久化（§5.2，落 paper.duckdb）
- ``dependencies``  FastAPI Depends：get_current_principal / require_roles 等（§六）
"""
