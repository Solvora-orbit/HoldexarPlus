"""Steampy 行情域测试（0.5.0 实验区）：响应形状/SPA 壳防御/实验开关守卫。

fetch_hot_page 的网络层用假会话打桩（校验 Content-Type 防SPA fallback 的
分支必须真实走到）；实验开关经 settings KV 落库后由 router 判定。
"""
from __future__ import annotations

import asyncio as _a
from typing import Any

import pytest

from app.core.database import init_db  # noqa: E402
from app.domains.settings import service as settings_service  # noqa: E402
from app.domains.steampy import service as spy  # noqa: E402

_HOT_PAGE = {
    "success": True,
    "result": {
        "totalElements": 2,
        "content": [
            {
                "gameName": "双人成行", "appId": "1426210", "oriPrice": 198.0,
                "gamePrice": 198.0, "hisPrice": 39.6, "discount": 0.83,
                "keyPrice": 39.6, "keySales": 3, "keyAveAmt": 45.1,
                "gameAva": "https://media.st.dl.eccdnx.com/steam/apps/1426210/header.jpg",
            },
            {"gameName": "", "appId": "", "oriPrice": 1},  # 空条目应被丢弃
            {"gameName": "Bad Row", "appId": "not-a-number", "oriPrice": 1},
        ],
    },
}


class _FakeResp:
    def __init__(self, status: int, content_type: str, payload: Any):
        self.status = status
        self._ct = content_type
        self._payload = payload

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Type": self._ct}

    async def json(self, content_type: Any = None) -> Any:
        return self._payload

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeSession:
    """记录调用次数的假会话（校验 60s 缓存命中不再发请求）。"""

    def __init__(self, resp: _FakeResp):
        self.resp = resp
        self.calls = 0

    def get(self, *args: Any, **kwargs: Any) -> _FakeResp:
        self.calls += 1
        return self.resp


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    spy._CACHE.clear()
    spy._session = None

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(spy.asyncio, "sleep", no_sleep)
    yield
    spy._CACHE.clear()


def test_normalize_item_shapes():
    ok = spy._normalize_item(_HOT_PAGE["result"]["content"][0])
    assert ok is not None
    assert ok["appId"] == 1426210 and ok["keyPrice"] == 39.6 and ok["keySales"] == 3
    assert spy._normalize_item({"gameName": "", "appId": "1"}) is None
    assert spy._normalize_item({"gameName": "x", "appId": "abc"}) is None


@pytest.mark.asyncio
async def test_fetch_hot_page_success_and_cache(monkeypatch):
    fake = _FakeSession(_FakeResp(200, "application/json", _HOT_PAGE))
    monkeypatch.setattr(spy, "_get_session", lambda: _async_return(fake))

    first = await spy.fetch_hot_page(page=1, page_size=20)
    assert first is not None and len(first["items"]) == 1  # 空条目/坏行被剔
    assert first["items"][0]["gameName"] == "双人成行"
    assert first["total"] == 2
    calls_after_first = fake.calls
    second = await spy.fetch_hot_page(page=1, page_size=20)
    assert second is not None and second["items"] == first["items"]
    assert fake.calls == calls_after_first  # 60s 缓存命中：不再发请求


async def _async_return(v: Any) -> Any:
    return v


@pytest.mark.asyncio
async def test_fetch_hot_page_spa_shell_rejected(monkeypatch):
    """SPA fallback 防：200 + text/html 壳必须按失败计（None）。"""
    fake = _FakeSession(_FakeResp(200, "text/html; charset=utf-8", None))
    monkeypatch.setattr(spy, "_get_session", lambda: _async_return(fake))
    assert await spy.fetch_hot_page(page=1) is None


@pytest.mark.asyncio
async def test_fetch_hot_page_http_error(monkeypatch):
    fake = _FakeSession(_FakeResp(503, "application/json", None))
    monkeypatch.setattr(spy, "_get_session", lambda: _async_return(fake))
    assert await spy.fetch_hot_page(page=1) is None


@pytest.mark.asyncio
async def test_router_gated_by_experimental(monkeypatch):
    """实验开关关闭 → 语义载荷 enabled=false 且不发起抓取；开启 → 透传。"""
    from app.domains.steampy import router as steampy_router

    await init_db()

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(spy.asyncio, "sleep", no_sleep)
    await settings_service.set_value("experimental.enabled", False)
    off = await steampy_router.hot(page=1, pageSize=20)
    assert off["enabled"] is False and off["items"] == []

    fake = _FakeSession(_FakeResp(200, "application/json", _HOT_PAGE))
    monkeypatch.setattr(spy, "_get_session", lambda: _async_return(fake))
    await settings_service.set_value("experimental.enabled", True)
    on = await steampy_router.hot(page=1, pageSize=20)
    assert on["enabled"] is True and on["ok"] is True and len(on["items"]) == 1

    # 还原开关（测试隔离：settings KV 落在测试库，离开前恢复默认关）
    await settings_service.set_value("experimental.enabled", False)
