"""proxies 域策略解析测试：策略引擎分支（proxy_first / direct_first / direct_only）
+ resolve 端点语义 + 0.3.0 默认翻转 / 自动让位 / 系统代理识别。

不触真实网络/内核/注册表——monkeypatch ClashRuntime 状态与 _system_proxy_url；
settings 键值层隔离到临时库。
"""
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.domains.proxies import clash_manager, service as proxies_service
from app.domains.settings import service as settings_service


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.core.database as database_module

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}", echo=False
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(settings_service, "get_session_factory", lambda: factory)
    monkeypatch.setattr(proxies_service, "get_session_factory", lambda: factory)
    return factory


@pytest_asyncio.fixture(autouse=True)
async def _schema(db):
    import app.domains.alerts.models  # noqa: F401
    import app.domains.crawl.models  # noqa: F401
    import app.domains.games.models  # noqa: F401
    import app.domains.proxies.models  # noqa: F401
    import app.domains.rates.models  # noqa: F401
    import app.domains.regions.models  # noqa: F401
    import app.domains.settings.models  # noqa: F401
    import app.domains.wishlist.models  # noqa: F401

    async with db.kw["bind"].begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
def _no_system_proxy(monkeypatch):
    """系统代理探测默认打桩为「没开」——真实机器注册表状态不进测试。"""
    monkeypatch.setattr(proxies_service, "_sys_proxy_cache", None)

    async def _none() -> str | None:
        return None

    monkeypatch.setattr(proxies_service, "_system_proxy_url", _none)


@pytest.mark.asyncio
async def test_resolve_proxy_first_clash_running(db, monkeypatch):
    """proxy_first + 内核在跑 → 返回内核混合端口（登录窗代理优先的取数源）。"""
    await settings_service.set_value("proxy.strategy", "proxy_first")
    monkeypatch.setattr(
        clash_manager.runtime,
        "status",
        lambda: {"running": True, "port": 7890, "configPath": "x", "controllerUrl": "y"},
    )
    url = await proxies_service.resolve_proxy_url()
    assert url == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_resolve_proxy_first_nothing_falls_back_direct(db, monkeypatch):
    """proxy_first + 无内核无池 + 本地混合端口死 → None（直连兜底，不报错）。

    端口探活 mock 为死：真实机器上用户自启的 Clash 可能活着（会让
    本用例假失败），测试语义是「真的一无所有时直连」。
    """
    await settings_service.set_value("proxy.strategy", "proxy_first")
    monkeypatch.setattr(
        clash_manager.runtime,
        "status",
        lambda: {"running": False, "port": None, "configPath": None, "controllerUrl": None},
    )
    async def _dead() -> bool:
        return False

    monkeypatch.setattr(proxies_service, "_local_clash_alive", _dead)
    assert await proxies_service.resolve_proxy_url() is None


@pytest.mark.asyncio
async def test_resolve_proxy_first_local_clash_port_alive(db, monkeypatch):
    """proxy_first + 无自管内核无池 + 本地混合端口活着（用户自启 Verge）→ 走该端口。

    steam 域直连基本被墙，活着的本地代理永远优于直连兜底。
    """
    await settings_service.set_value("proxy.strategy", "proxy_first")
    await settings_service.set_value("proxy.clash_port", 7890)
    monkeypatch.setattr(
        clash_manager.runtime,
        "status",
        lambda: {"running": False, "port": None, "configPath": None, "controllerUrl": None},
    )
    async def _alive() -> bool:
        return True

    monkeypatch.setattr(proxies_service, "_local_clash_alive", _alive)
    assert await proxies_service.resolve_proxy_url() == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_resolve_direct_only(db):
    """direct_only 策略 → 恒直连。"""
    await settings_service.set_value("proxy.strategy", "direct_only")
    assert await proxies_service.resolve_proxy_url() is None


@pytest.mark.asyncio
async def test_removed_strategies_migrate_to_proxy_first(db):
    """下架策略迁移：pinned/clash 存量库值被 get_strategy 一次性迁移到
    proxy_first（含返回体不再带 pinnedId），pinned_id 设置键清账。"""
    for removed in ("pinned", "clash"):
        await settings_service.set_value("proxy.strategy", removed)
        await settings_service.set_value("proxy.pinned_id", 42)
        info = await proxies_service.get_strategy()
        assert info["strategy"] == "proxy_first"
        assert "pinnedId" not in info  # 字段随策略下架移除
        assert await settings_service.get_value("proxy.pinned_id") is None


@pytest.mark.asyncio
async def test_set_strategy_rejects_removed_strategy(db):
    """下架后 set_strategy 对 pinned/clash 报未知策略（前端已无入口，API 层兜底）。"""
    for removed in ("pinned", "clash"):
        with pytest.raises(ValueError, match="未知策略"):
            await proxies_service.set_strategy(removed)


# ── 0.3.0：默认翻转 direct_first / 自动让位 / 系统代理 ──────────


@pytest.mark.asyncio
async def test_get_strategy_default_is_direct_first(db):
    """全新库（未设策略）：get_strategy 兜底写 direct_first（装好就能用）。"""
    info = await proxies_service.get_strategy()
    assert info["strategy"] == "direct_first"
    assert await settings_service.get_value("proxy.strategy") == "direct_first"


@pytest.mark.asyncio
async def test_resolve_direct_first_plain_direct(db):
    """direct_first + 无系统代理（autouse 桩）→ None 真直连。"""
    await settings_service.set_value("proxy.strategy", "direct_first")
    assert await proxies_service.resolve_proxy_url() is None


@pytest.mark.asyncio
async def test_resolve_direct_first_uses_system_proxy(db, monkeypatch):
    """direct_first + 注册表开了系统代理 → 出口 = 系统代理（「自动走系统代理」）。"""
    await settings_service.set_value("proxy.strategy", "direct_first")

    async def _sys() -> str | None:
        return "http://127.0.0.1:7897"

    monkeypatch.setattr(proxies_service, "_system_proxy_url", _sys)
    assert await proxies_service.resolve_proxy_url() == "http://127.0.0.1:7897"


@pytest.mark.asyncio
async def test_resolve_direct_only_ignores_system_proxy(db, monkeypatch):
    """direct_only 强制直连：即便系统代理在跑也不绕过（调试/白名单场景语义）。"""
    await settings_service.set_value("proxy.strategy", "direct_only")

    async def _sys() -> str | None:
        return "http://127.0.0.1:7897"

    monkeypatch.setattr(proxies_service, "_system_proxy_url", _sys)
    assert await proxies_service.resolve_proxy_url() is None


@pytest.mark.asyncio
async def test_notify_proxy_source_ready_switches_only_when_unmanually(db):
    """自动让位链：未手动选过策略且当前直连类 → 切 proxy_first；
    手动选过（set_strategy 落 manual 标记）或已是代理类 → 不动。"""
    # 未手动 + direct_first → 切
    await settings_service.set_value("proxy.strategy", "direct_first")
    assert await proxies_service.notify_proxy_source_ready() is True
    assert await settings_service.get_value("proxy.strategy") == "proxy_first"
    # 已是代理类 → 不重复动
    assert await proxies_service.notify_proxy_source_ready() is False
    # 用户手动选过 direct_only → 永不覆盖
    await proxies_service.set_strategy("direct_only")
    assert await settings_service.get_value("proxy.strategy_manual") is True
    assert await proxies_service.notify_proxy_source_ready() is False
    assert await settings_service.get_value("proxy.strategy") == "direct_only"


def test_parse_system_proxy_forms():
    """注册表 ProxyServer 两形态解析 + 脏值容错。"""
    p = proxies_service.parse_system_proxy
    assert p("127.0.0.1:7897") == "http://127.0.0.1:7897"
    assert p("http=127.0.0.1:7890;https=127.0.0.1:7890;socks=127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert p("socks=1.2.3.4:1080") is None  # 只有 socks 形态：不采用
    assert p("https=10.0.0.1:8443;http=10.0.0.1:8080") == "http://10.0.0.1:8443"
    assert p("") is None
    assert p("host无端口") is None
    assert p("host:notaport") is None
    assert p(" 127.0.0.1:7897 ") == "http://127.0.0.1:7897"


def test_read_registry_proxy_never_raises(monkeypatch):
    """winreg 缺失/键异常一律 (0, "")——增强路径静默降级，绝不炸调用方。"""
    import builtins

    real_import = builtins.__import__

    def _no_winreg(name, *a, **kw):
        if name == "winreg":
            raise ImportError("no winreg")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_winreg)
    assert proxies_service._read_registry_proxy() == (0, "")
