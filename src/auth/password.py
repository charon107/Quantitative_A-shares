"""密码哈希（规范书 §10.6）：passlib bcrypt，禁止明文 / MD5 / SHA1。

bcrypt 单条密码上限 72 字节；超长输入先 SHA-256 预哈希是常见做法，但会引入
「密码 shucking」隐患——本项目直接拒绝超长密码（创建用户时按 PASSWORD_MIN_LEN
校验下限、此处按 72 字节校验上限），简单明确。

CryptContext 按调用时 BCRYPT_ROUNDS 惰性构建，便于测试 monkeypatch 降轮数提速。
"""
from __future__ import annotations

from passlib.context import CryptContext

from src.auth import config

_BCRYPT_MAX_BYTES = 72


def _context() -> CryptContext:
    return CryptContext(schemes=["bcrypt"], deprecated="auto",
                        bcrypt__rounds=config.BCRYPT_ROUNDS)


def hash_password(password: str) -> str:
    """生成 bcrypt 哈希。空密码或超 72 字节的输入直接拒绝。"""
    if not password:
        raise ValueError("密码不能为空")
    if len(password.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        raise ValueError("密码超长（bcrypt 上限 72 字节）")
    return _context().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """常数时间校验密码（passlib 内部按哈希格式校验，时序不泄露明文长度）。"""
    if not password or not password_hash:
        return False
    try:
        return bool(_context().verify(password, password_hash))
    except ValueError:
        # 哈希串损坏/格式未知等按校验失败处理，不向上抛细节
        return False
