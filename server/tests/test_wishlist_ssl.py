"""wishlist 同步链 SSL 回归（mock，不触网）。

背景：打包版定时任务 `[定时] 账户同步失败` 根因——经 Clash 内核/本机加速器
出网时严格证书校验必失败（CERTIFICATE_VERIFY_FAILED / unable to get local
issuer certificate），而 wishlist 曾是全项目唯一没有 SSL 降级重试的 Steam
出网链路。覆盖三层修复：
- family._steam_get 的「先严格校验，SSL 错则跳过校验重试一次」行为
- fetch_wishlist 确实接线到 _steam_get（不再裸 httpx）
- sync_account 愿望单通道失败 = 软失败（不崩整次同步、不做反向核对）
"""
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.domains.family import service as family_service
from app.domains.wishlist import service as wishlist_service
from app.domains.wishlist.models import TrackedAccount, WishlistItem

PRIMARY = "76561198000000001"

_SSL_MSG = (
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "unable to get local issuer certificate (_ssl.c:1032)"
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


# ── _steam_get 降级重试行为 ──────────────────────────────


@pytest.mark.asyncio
async def test_steam_get_degrades_on_ssl_error(monkeypatch):
    """严格校验抛 SSL ConnectError → 同参数宽松重试一次并成功。"""

    async def no_proxy():
        return None

    monkeypatch.setattr(family_service, "_strategy_proxy", no_proxy)

    calls: list[dict] = []

    class _Client:
        def __init__(self, **kwargs):
            self.strict = kwargs.get("verify") is not False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, params=None):
            calls.append({"url": url, "params": params, "strict": self.strict})
            if self.strict:
                raise httpx.ConnectError(_SSL_MSG)
            return _Resp({"response": {"items": [{"appid": 620}]}})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    resp = await family_service._steam_get(
        wishlist_service.WISHLIST_URL, params={"steamid": PRIMARY}
    )
    assert resp.json() == {"response": {"items": [{"appid": 620}]}}
    assert [c["strict"] for c in calls] == [True, False], "应恰好一次严格 + 一次宽松重试"


@pytest.mark.asyncio
async def test_steam_get_reraises_non_ssl(monkeypatch):
    """非 SSL 网络错误不盲目降级（校验失败 ≠ 网络不通）。"""

    async def no_proxy():
        return None

    monkeypatch.setattr(family_service, "_strategy_proxy", no_proxy)

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, params=None):
            raise httpx.ConnectTimeout("connect timed out")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    with pytest.raises(httpx.ConnectTimeout):
        await family_service._steam_get(
            wishlist_service.WISHLIST_URL, params={"steamid": PRIMARY}
        )


# ── fetch_wishlist 接线 ─────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_wishlist_routes_through_steam_get(monkeypatch):
    """fetch_wishlist 必须走 _steam_get（带 SSL 降级），禁止回退裸 httpx。"""
    seen = {}

    async def fake_steam_get(url, params, *, method="GET"):
        seen["url"] = url
        seen["params"] = params
        return _Resp({"response": {"items": [{"appid": 620, "added_at": 111}, {"bad": 1}]}})

    monkeypatch.setattr(family_service, "_steam_get", fake_steam_get)

    items = await wishlist_service.fetch_wishlist(PRIMARY)
    assert seen["url"] == wishlist_service.WISHLIST_URL
    assert seen["params"] == {"steamid": PRIMARY}
    assert items == [{"appid": 620, "added_at": 111}]


# ── sync_account 愿望单通道软失败 ────────────────────────


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.core.database as database_module
    import app.domains.monitoring.service as monitoring_service

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}", echo=False
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(wishlist_service, "get_session_factory", lambda: factory)
    monkeypatch.setattr(monitoring_service, "get_session_factory", lambda: factory)

    async def fake_persona(steamid):
        return {"persona_name": "", "avatar_url": "", "online": False, "in_game_name": ""}

    monkeypatch.setattr(wishlist_service, "_fetch_persona", fake_persona)

    async def no_family():
        return {}

    monkeypatch.setattr(wishlist_service, "_family_personas", no_family)
    return factory


@pytest_asyncio.fixture(autouse=True)
async def _schema(db):
    import app.domains.games.models  # noqa: F401
    import app.domains.monitoring.models  # noqa: F401
    import app.domains.wishlist.models  # noqa: F401

    async with db.kw["bind"].begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_sync_account_wishlist_channel_fail_soft(db, monkeypatch):
    """愿望单通道 SSL 崩 → 本次同步不整体失败：已购/persona 照常同步，
    既有愿望单/已购标记保留，反向核对跳过（通道失败 ≠ 愿望单为空）。"""
    from sqlalchemy import select

    async with db() as session:
        session.add(TrackedAccount(
            steamid=PRIMARY, kinds_json={"wishlist": True, "owned": True}, item_count=0
        ))
        session.add(WishlistItem(steamid=PRIMARY, appid=570, active=True, wishlisted=True))
        await session.commit()

    async def fail_wishlist(steamid):
        raise httpx.ConnectError(_SSL_MSG)

    async def ok_owned(steamid):
        return [{"appid": 620, "name": "Portal 2"}], "jwt"

    monkeypatch.setattr(wishlist_service, "fetch_wishlist", fail_wishlist)
    monkeypatch.setattr(wishlist_service, "fetch_owned_games", ok_owned)

    result = await wishlist_service.sync_account(PRIMARY, auto_crawl=False)

    assert result["wishlistError"] is not None and "CERTIFICATE_VERIFY_FAILED" in result["wishlistError"]
    # 已购同步没有被愿望单失败连带崩掉
    assert result["ownedError"] is None
    assert result["ownedSource"] == "jwt"
    assert 620 in result["newOwnedAppids"]

    async with db() as session:
        rows = (
            await session.execute(
                select(WishlistItem).where(WishlistItem.steamid == PRIMARY)
            )
        ).scalars().all()
    by_appid = {int(r.appid): r for r in rows}
    assert by_appid[570].active is True
    assert by_appid[570].wishlisted is True, "通道失败不得洗掉愿望单标记"
