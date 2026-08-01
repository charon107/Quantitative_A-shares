"""缓存租户化单元测试（规范书 §7.9）：key 含 tenant_id、按租户失效 pattern。

Redis 在测试环境整体禁用（根 conftest REDIS_ENABLED=False），故直接断言 key
结构与 SCAN pattern 匹配逻辑，不依赖真实 Redis。
"""
import fnmatch

import pytest

from src import cache
from src.cache import _make_key, _safe_tenant


def test_make_key_public_default():
    """默认租户 __public__：公共市场数据 key 结构确定（§7.9）。"""
    k1 = _make_key("load_latest_day")
    k2 = _make_key("load_latest_day")
    assert k1 == k2
    assert k1.startswith(f"{cache.CACHE_VERSION}:load_latest_day:{cache.PUBLIC_TENANT}:")


def test_make_key_tenant_isolated():
    """不同租户同 func → 不同 key（§7.9 不串数据）。"""
    k_a = _make_key("overview", tenant_id="t_a")
    k_b = _make_key("overview", tenant_id="t_b")
    assert k_a != k_b
    assert f":t_a:" in k_a and f":t_b:" in k_b
    assert cache.PUBLIC_TENANT not in k_a


def test_make_key_params_affect_digest():
    """relevant_params 参与 digest 哈希（既有语义保持）。"""
    k1 = _make_key("overview", {"account_id": "a"})
    k2 = _make_key("overview", {"account_id": "b"})
    assert k1 != k2


def test_safe_tenant_escapes_glob():
    """glob 元字符转义，防租户 ID 干扰 SCAN pattern（§7.9）。"""
    assert _safe_tenant("t_a*b[c]{d}") == "t_a_b_c__d_"
    assert _safe_tenant("t_default") == "t_default"


def test_invalidate_tenant_pattern_isolated():
    """invalidate_tenant 的 pattern 只命中目标租户，公共缓存与其他租户不受影响。"""
    pub = _make_key("market")                                  # __public__
    a = _make_key("overview", tenant_id="t_a")
    a_meta = a + ":meta"
    b = _make_key("overview", tenant_id="t_b")

    pattern = f"{cache.CACHE_VERSION}:*:{_safe_tenant('t_a')}:*"
    matched = {k for k in (pub, a, a_meta, b) if fnmatch.fnmatch(k, pattern)}
    assert matched == {a, a_meta}


def test_invalidate_tenant_deletes_only_target(monkeypatch):
    """FakeRedis 验证：invalidate_tenant 只删目标租户 key + 其 meta。"""
    pub = _make_key("market")
    a = _make_key("overview", tenant_id="t_a")
    a_meta = a + ":meta"
    b = _make_key("overview", tenant_id="t_b")

    deleted: list[str] = []

    class FakeRedis:
        def __init__(self):
            self.all_keys = {pub, a, a_meta, b}
            self.done = False

        def scan(self, cursor, match=None, count=100):
            if self.done:
                return (0, [])
            self.done = True
            return (0, [k for k in self.all_keys if fnmatch.fnmatch(k, match)])

        def delete(self, *keys):
            deleted.extend(keys)
            return len(keys)

    monkeypatch.setattr(cache, "_get_redis", lambda: FakeRedis())
    n = cache.invalidate_tenant("t_a")
    assert n == 2
    assert set(deleted) == {a, a_meta}


def test_version_bumped_to_v2():
    """附录 C：key 结构调整后 CACHE_VERSION 递增，强制旧缓存全量失效。"""
    assert cache.CACHE_VERSION == "v2"
