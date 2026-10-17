"""assert_gateway_ok 单测：网络类瞬时错误退避重试，鉴权/业务错误立即致命。

回归背景：2026-10-16 的定时任务因网关一次 30s 读超时直接 TushareFatalError，
当天增量财报整批未入库；同时"token 过期必须第一时间非零退出"的防线不能松。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.data_collection import tushare_client as tsc  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {"code": 0, "data": {}}
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture()
def probe(monkeypatch):
    """搭好探活环境：假客户端 + 假 sleep + 记录请求次数，返回投喂脚本的入口。"""
    class FakeClient:
        _DataApi__http_url = "https://gateway.example.com"

    monkeypatch.setattr(tsc, "_pro", lambda: FakeClient())
    monkeypatch.setattr(tsc, "PROBE_ATTEMPTS", 3)
    monkeypatch.setattr(tsc.time, "sleep", lambda _s: None)

    state = {"calls": 0}

    def install(responses):
        """responses 依次消费；耗尽后重复最后一个。"""
        seq = list(responses)

        def fake_post(url, json=None, timeout=None):
            state["calls"] += 1
            item = seq[min(state["calls"], len(seq)) - 1]
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr("requests.post", fake_post)
        return state

    return install


def test_retries_on_read_timeout_then_succeeds(probe):
    """一次读超时不再致命：重试后成功即通过（本次故障场景）。"""
    state = probe([TimeoutError("Read timed out. (read timeout=30.0)"), FakeResponse()])

    tsc.assert_gateway_ok()

    assert state["calls"] == 2


def test_retries_on_transient_5xx_then_succeeds(probe):
    state = probe([FakeResponse(502, text="bad gateway"), FakeResponse()])

    tsc.assert_gateway_ok()

    assert state["calls"] == 2


def test_retries_on_200_non_json(probe):
    state = probe([FakeResponse(200, body=ValueError("no json"), text="<html>oops</html>"),
                   FakeResponse()])

    tsc.assert_gateway_ok()

    assert state["calls"] == 2


def test_all_attempts_timeout_raises_fatal(probe):
    """重试耗尽仍然抛 TushareFatalError（"静默空跑"防线不变）。"""
    state = probe([TimeoutError("Read timed out.")])

    with pytest.raises(tsc.TushareFatalError) as ei:
        tsc.assert_gateway_ok()

    assert "3 次全部失败" in str(ei.value)
    assert state["calls"] == 3


def test_401_is_fatal_without_retry(probe):
    """token 过期（401）必须立即致命，不能靠重试掩盖。"""
    state = probe([FakeResponse(401, text='{"msg":"Token 已过期"}')])

    with pytest.raises(tsc.TushareFatalError) as ei:
        tsc.assert_gateway_ok()

    assert "401" in str(ei.value)
    assert state["calls"] == 1


def test_body_error_is_fatal_without_retry(probe):
    state = probe([FakeResponse(200, body={"error": "token invalid"})])

    with pytest.raises(tsc.TushareFatalError) as ei:
        tsc.assert_gateway_ok()

    assert "token invalid" in str(ei.value)
    assert state["calls"] == 1


def test_nonzero_code_is_fatal_without_retry(probe):
    state = probe([FakeResponse(200, body={"code": -2001, "msg": "权限不足"})])

    with pytest.raises(tsc.TushareFatalError) as ei:
        tsc.assert_gateway_ok()

    assert "权限不足" in str(ei.value)
    assert state["calls"] == 1
