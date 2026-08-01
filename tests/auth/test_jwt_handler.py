"""JWT 签发/校验单元测试（规范书 §11.1）：claims 齐全、过期、错密钥、
alg=none、aud/iss 不符、refresh 轮转、密钥轮换回退。"""
from datetime import timedelta

import jwt as pyjwt
import pytest
from freezegun import freeze_time

from src.auth import jwt_handler
from tests.auth.conftest import TEST_SECRET, TEST_SECRET_2

USER = {"user_id": "u_001", "tenant_id": "t_a", "roles": ["trader"],
        "account_ids": ["acc_1"]}


def _issue():
    return jwt_handler.issue_token_pair(**USER)


def test_jwt_issue_decode(jwt_env):
    """签发后能正确解码，标准 claims + 业务 claims 齐全（§4.4）。"""
    pair = _issue()
    assert pair["token_type"] == "Bearer" and pair["expires_in"] == 900
    claims = jwt_handler.decode_access(pair["access_token"])
    for key in ("iss", "sub", "aud", "iat", "nbf", "exp", "jti",
                    "tenant_id", "roles", "account_ids", "token_type"):
        assert key in claims, f"缺 claim: {key}"
    assert claims["sub"] == "u_001" and claims["tenant_id"] == "t_a"
    assert claims["roles"] == ["trader"] and claims["account_ids"] == ["acc_1"]
    assert claims["token_type"] == "access"
    assert claims["exp"] - claims["iat"] == 900

    rclaims = jwt_handler.decode_refresh(pair["refresh_token"])
    assert rclaims["token_type"] == "refresh"
    assert rclaims["exp"] - rclaims["iat"] == 604800


def test_jwt_expired(jwt_env):
    """exp 过期（超出 leeway）→ ExpiredSignatureError（§4.7）。"""
    with freeze_time("2026-08-01 00:00:00") as frozen:
        pair = _issue()
        frozen.tick(timedelta(seconds=900 + 31))  # TTL + leeway 之外
        with pytest.raises(pyjwt.ExpiredSignatureError):
            jwt_handler.decode_access(pair["access_token"])


def test_jwt_leeway_tolerates_small_skew(jwt_env):
    """leeway 30s 内的过期仍可解码（时钟漂移容忍，§4.7）。"""
    with freeze_time("2026-08-01 00:00:00") as frozen:
        pair = _issue()
        frozen.tick(timedelta(seconds=900 + 29))
        assert jwt_handler.decode_access(pair["access_token"])["sub"] == "u_001"


def test_jwt_wrong_secret(jwt_env):
    """错误密钥验签失败。"""
    pair = _issue()
    jwt_env.JWT_SECRET = TEST_SECRET_2  # 换密钥后旧 token 必须验签失败
    with pytest.raises(pyjwt.InvalidSignatureError):
        jwt_handler.decode_access(pair["access_token"])


def test_jwt_alg_none_rejected(jwt_env):
    """alg=none 的 token 一律拒绝（§10.3.1）。"""
    payload = {"iss": "wechatnum-api", "aud": "wechatnum-clients", "sub": "u_001",
               "tenant_id": "t_a", "roles": ["admin"], "iat": 1, "nbf": 1,
               "exp": 9999999999, "jti": "x", "token_type": "access"}
    none_token = pyjwt.encode(payload, key=None, algorithm="none")
    with pytest.raises(pyjwt.PyJWTError):
        jwt_handler.decode_access(none_token)


def test_jwt_wrong_aud_iss(jwt_env):
    """aud / iss 不匹配被拒绝（§10.3.3）。"""
    base = {"sub": "u_001", "tenant_id": "t_a", "roles": [], "iat": 1, "nbf": 1,
            "exp": 9999999999, "jti": "x", "token_type": "access"}
    bad_aud = pyjwt.encode({**base, "iss": "wechatnum-api", "aud": "evil"}, TEST_SECRET, algorithm="HS256")
    with pytest.raises(pyjwt.InvalidAudienceError):
        jwt_handler.decode_access(bad_aud)
    bad_iss = pyjwt.encode({**base, "iss": "evil", "aud": "wechatnum-clients"}, TEST_SECRET, algorithm="HS256")
    with pytest.raises(pyjwt.InvalidIssuerError):
        jwt_handler.decode_access(bad_iss)


def test_refresh_cannot_access_business(jwt_env):
    """refresh token 不能当 access 用（§4.5 token_type 校验）。"""
    pair = _issue()
    with pytest.raises(pyjwt.InvalidTokenError):
        jwt_handler.decode_access(pair["refresh_token"])
    with pytest.raises(pyjwt.InvalidTokenError):
        jwt_handler.decode_refresh(pair["access_token"])


def test_refresh_rotation(jwt_env, mem_blacklist):
    """轮转：旧 refresh 的 jti 撤销后视为已用（§4.5 一次性）。"""
    pair = _issue()
    claims = jwt_handler.decode_refresh(pair["refresh_token"])
    assert not jwt_handler.is_revoked(claims["jti"])
    jwt_handler.revoke_jti(claims["jti"], jwt_handler.remaining_ttl(claims))
    assert jwt_handler.is_revoked(claims["jti"])


def test_previous_secret_fallback(jwt_env):
    """密钥轮换：旧密钥签发的 token 在 PREVIOUS 窗口内仍可校验（§4.2）。"""
    pair = _issue()  # 用 TEST_SECRET 签发
    jwt_env.JWT_SECRET = TEST_SECRET_2          # 轮换到新密钥
    jwt_env.JWT_SECRET_PREVIOUS = TEST_SECRET   # 旧密钥进入 PREVIOUS
    assert jwt_handler.decode_access(pair["access_token"])["sub"] == "u_001"
    # 签发一律用当前密钥
    new_pair = _issue()
    with pytest.raises(pyjwt.InvalidSignatureError):
        pyjwt.decode(new_pair["access_token"], TEST_SECRET, algorithms=["HS256"],
                     audience="wechatnum-clients")
