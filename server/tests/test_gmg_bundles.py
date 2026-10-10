"""GMG 捆绑包域测试：合成主站列表/子站详情/switch_tier HTML + 网络打桩 + 真实落库。

模式同 test_humble_bundles.py（slug 用 gmgtest- 前缀隔离真实库；resolver 走
metadata.service._resolve_appid 打桩；收录链 run_sequential 打桩）。
"""
from __future__ import annotations

import asyncio as _a

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import get_session_factory, init_db  # noqa: E402
from app.domains.gmg import service as gmg  # noqa: E402
from app.domains.gmg.models import GmgBundle, GmgBundleGame  # noqa: E402

SLUG_A = "gmgtest-alpha"    # 多档累进包（2/4/6 款）
SLUG_B = "gmgtest-beta"     # 单档包
APP_A1 = 553310
APP_A2 = 244730


def _as_target(fake):
    """旧 appid 打桩 → resolve_target 适配（0.5.0 接缝；bundle 侧恒 None）。"""

    async def wrapper(session, title, proxy):
        r = await fake(session, title, proxy)
        return (r, None) if isinstance(r, int) else (None, None)

    return wrapper


_LIVE_END = "2026-10-27T18:00"


def _list_html() -> str:
    return f"""
<div class="product-card" data-category="video-games" data-bundle-state="live" data-end-date="{_LIVE_END}">
<a href="https://www.greenmangamingbundles.com/bundles/{SLUG_A}?utm_term=flatpage" class="packshot-link">
<img src="https://media.greenmangaming.com/bundles/alpha/Card.png" alt="Alpha Bundle" /></a>
<h3 class="product-title">Alpha Bundle</h3>
<span class="pill pill-item-count">6 Items</span>
<div class="price-info"><span class="worth-price">VALUE <span>&yen;565</span></span>
<span class="from-price">FROM <span>&yen;45</span></span></div></div>
<div class="product-card" data-category="video-games" data-bundle-state="live" data-end-date="{_LIVE_END}">
<a href="https://www.greenmangamingbundles.com/bundles/{SLUG_B}/" class="packshot-link">
<img src="https://media.greenmangaming.com/bundles/beta/Card.png" alt="Beta Bundle" /></a>
<h3 class="product-title">Beta Bundle</h3>
<div class="price-info"><span class="from-price">FROM <span>CN&yen;61.20 CNY</span></span></div></div>
<div class="product-card" data-category="books" data-bundle-state="live" data-end-date="{_LIVE_END}">
<a href="https://www.greenmangamingbundles.com/bundles/gmgtest-book/" class="packshot-link"></a>
<h3 class="product-title">Book Bundle</h3></div>
<div class="product-card" data-category="video-games" data-bundle-state="expired" data-end-date="{_LIVE_END}">
<a href="https://www.greenmangamingbundles.com/bundles/gmgtest-expired/" class="packshot-link"></a>
<h3 class="product-title">Expired</h3></div>
"""


def _tier_radio(tier_id: str, price: str, cny_text: str, name: str, count: int) -> str:
    return f"""
<label data-bundle-radio-card class="custom-radio-card">
<input type="radio" name="amount" data-upgrade-tier-id="{tier_id}" value="{tier_id}:{price}" required />
<div data-wrapper="true" class="radio-wrapper tier-upgrade-btn">
<div class="d-flex flex-column tier-label">
<span class="bundle-type d-none d-xl-flex">{name}</span>
<small>{count} 款游戏</small></div>
<div class="d-flex tier-pricing"><div class="radio-content">
<div class="min-w-100 d-flex flex-column"><span class="price">{cny_text}</span></div>
</div></div></div></label>"""


def _game_card(title: str) -> str:
    return f"""
<figcaption class="d-flex flex-column align-items-center">
<strong class="game-title">{title}</strong>
<div class="product-badges"><ul><li><span class="badge gray badge-sm"><span>Steam</span></span></li></ul></div>
</figcaption>"""


def _detail_html(order: list[str]) -> str:
    """详情页：三档 radio（Kingdoms 2 / Dynasties 4 / Empires 6，CN¥ 价）+
    游戏网格（order 即网格顺序，模拟最高档视图的任意顺序）。"""
    radios = (
        _tier_radio("kingdoms", "45.00", "CN¥45.00", "Kingdoms", 2)
        + _tier_radio("dynasties", "65.00", "CN¥65.00", "Dynasties", 4)
        + _tier_radio("empires", "90.00", "CN¥90.00", "Empires", 6)
    )
    grid = "".join(_game_card(t) for t in order)
    return f"""<html><body>
<div class="bundles-wrap" hx-get="/zh-hans/bundles/{SLUG_A}/switch_tier/">{radios}</div>
<div class="games-grid">{grid}</div>
</body></html>"""


def _switch_html(order: list[str]) -> str:
    return "<html><body>" + "".join(_game_card(t) for t in order) + "</body></html>"


# 档位升序（switch_tier 最低档响应的顺序）：Kingdoms 2 款 → Dynasties 新增 2 → Empires 新增 2
_TIER_ORDER = [
    "Quarry Game", "Roller Game",                 # kingdoms
    "Tina Game", "XCOM Game",                     # dynasties 新增
    "Risk Game", "BioShock Game",                 # empires 新增
]


@pytest_asyncio.fixture(autouse=True)
async def _seed_clean():
    await init_db()
    yield
    async with get_session_factory()() as session:
        for slug in (SLUG_A, SLUG_B):
            bundle = await session.get(GmgBundle, slug)
            if bundle is not None:
                for g in (
                    await session.execute(
                        select(GmgBundleGame).where(GmgBundleGame.slug == slug)
                    )
                ).scalars():
                    await session.delete(g)
                await session.delete(bundle)
        await session.commit()


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch):
    async def fake_proxy():
        return None

    monkeypatch.setattr(gmg, "_strategy_proxy", fake_proxy)
    monkeypatch.setattr(gmg, "_REFRESH_LOCK", _a.Lock())


# ─── 解析层 ───────────────────────────────────────────────────────


def test_parse_money_symbols():
    assert gmg._parse_money("CN¥45.00") == ("CNY", 4500)
    assert gmg._parse_money("&yen;591") == ("CNY", 59100)
    assert gmg._parse_money("$12.99") == ("USD", 1299)
    assert gmg._parse_money("€9,99") == ("EUR", 999)  # 欧式小数逗号
    assert gmg._parse_money("1,234.50") is None  # 无符号不猜币种，但数字形态合法
    assert gmg._parse_money("45.00") is None  # 无币种符号不硬猜


def test_parse_list_filters_and_fields():
    rows = gmg.parse_list(_list_html())
    assert [r["slug"] for r in rows] == [SLUG_A, SLUG_B]  # 书类/过期被剔
    a = rows[0]
    assert a["name"] == "Alpha Bundle"
    assert a["end_at"] is not None and a["end_at"].hour == 18
    assert a["price_map"] == {"CNY": 4500}  # FROM ¥45
    assert rows[1]["price_map"] == {"CNY": 6120}


def test_parse_detail_and_assign_tier_titles():
    detail = gmg.parse_detail(_detail_html(list(reversed(_TIER_ORDER))))
    assert detail is not None
    assert [t["id"] for t in detail["tiers_raw"]] == ["kingdoms", "dynasties", "empires"]
    assert [t["count"] for t in detail["tiers_raw"]] == [2, 4, 6]
    assert detail["tiers_raw"][0]["price_map"] == {"CNY": 4500}
    # 网格乱序全量 → switch_tier 升序响应切片分档
    tiers = gmg.merge_tiers(detail, _TIER_ORDER)
    assert [t["id"] for t in tiers] == ["kingdoms", "dynasties", "empires"]
    assert tiers[0]["titles"] == ["Quarry Game", "Roller Game"]
    assert tiers[1]["titles"] == ["Tina Game", "XCOM Game"]
    assert tiers[2]["titles"] == ["Risk Game", "BioShock Game"]
    assert tiers[0]["is_initial"] is True


def test_assign_tier_titles_mismatch_falls_back():
    detail = gmg.parse_detail(_detail_html(list(reversed(_TIER_ORDER))))
    tiers = gmg.merge_tiers(detail, ["Wrong", "Set"])  # 集合不符 → 单伪档退化
    assert len(tiers) == 3
    assert tiers[0]["titles"] == []
    assert tiers[2]["titles"] == list(reversed(_TIER_ORDER))  # 全量归最高档
    # 无 switch_tier 数据（None）同退化
    tiers2 = gmg.merge_tiers(detail, None)
    assert tiers2[2]["titles"] == list(reversed(_TIER_ORDER))


# ─── 链路（真实落库 + resolver 打桩）─────────────────────────────


def _stub_network(monkeypatch, detail_html: str | None = None) -> None:
    async def fake_page(session, proxy, url, attempts=3):
        if url.endswith("/zh/bundles/"):
            return _list_html()
        if SLUG_A in url:
            return detail_html if detail_html is not None else _detail_html(list(reversed(_TIER_ORDER)))
        if SLUG_B in url:
            return _tier_radio("only", "10.00", "CN¥10.00", "Only", 1) + _game_card("Solo Game")
        return None

    async def fake_switch(session, proxy, slug, amount_value):
        if slug == SLUG_A:
            assert amount_value == "kingdoms:45.00"
            return _switch_html(_TIER_ORDER)
        if slug == SLUG_B:
            return _switch_html(["Solo Game"])
        return None

    monkeypatch.setattr(gmg, "_fetch_page", fake_page)
    monkeypatch.setattr(gmg, "_fetch_switch_tier", fake_switch)


@pytest.mark.asyncio
async def test_refresh_upsert_assoc_and_tiers(monkeypatch):
    """列表 upsert → 详情档位/条目 → 统一检索引擎关联 → 收录扫描登记。"""
    ingest_calls: list[list[int]] = []

    async def fake_resolve(session, title, proxy):
        return APP_A1 if title == "Quarry Game" else (APP_A2 if title == "Roller Game" else None)

    async def fake_run_sequential(specs, **_):
        ingest_calls.append(list(specs[0]["appids"]))
        return [{"id": 9}]

    async def no_sleep(_: float) -> None:
        return None

    _stub_network(monkeypatch)
    monkeypatch.setattr("app.domains.metadata.service._resolve_target", _as_target(fake_resolve))
    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run_sequential)
    monkeypatch.setattr(gmg.asyncio, "sleep", no_sleep)

    res = await gmg.refresh_gmg_bundles()
    assert res["ok"] is True and res["listed"] == 2
    async with get_session_factory()() as session:
        a = await session.get(GmgBundle, SLUG_A)
        assert a is not None and a.on_sale is True and a.price_map_json
        assert '"kingdoms"' in a.tiers_json and '"empires"' in a.tiers_json
        games_a = (
            await session.execute(
                select(GmgBundleGame).where(GmgBundleGame.slug == SLUG_A)
            )
        ).scalars().all()
        by_title = {g.title: g for g in games_a}
        assert by_title["Quarry Game"].appid == APP_A1
        assert by_title["Roller Game"].appid == APP_A2
        assert by_title["Tina Game"].appid is None
        assert by_title["Tina Game"].resolve_attempts == 1  # 首轮失败记账
        assert a.game_count == 6
        b = await session.get(GmgBundle, SLUG_B)
        assert b is not None
        assert '"only"' in b.tiers_json  # 单档包档位落库
    assert res["unresolvedTotal"] == 5  # Tina/XCOM/Risk/BioShock + Solo 首轮未解
    assert ingest_calls and sorted(ingest_calls[0]) == sorted([APP_A1, APP_A2])


@pytest.mark.asyncio
async def test_refresh_retry_exhausted_not_found(monkeypatch):
    """三轮未解 → not_found 不再拾取；下一轮重抓因 has_pending 不再触发。"""
    async def never_resolves(session, title, proxy):
        return None

    async def fake_run_sequential(specs, **_):
        return [{"id": 9}]

    async def no_sleep(_: float) -> None:
        return None

    _stub_network(monkeypatch)
    monkeypatch.setattr("app.domains.metadata.service._resolve_target", _as_target(never_resolves))
    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run_sequential)
    monkeypatch.setattr(gmg.asyncio, "sleep", no_sleep)
    await gmg.refresh_gmg_bundles()
    await gmg.refresh_gmg_bundles()
    await gmg.refresh_gmg_bundles()
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                select(GmgBundleGame).where(GmgBundleGame.slug == SLUG_A)
            )
        ).scalars().all()
        unresolved = [g for g in rows if g.appid is None]
        assert unresolved and all(
            g.resolve_attempts >= gmg._RESOLVE_MAX_ATTEMPTS for g in unresolved
        )
    # 三轮后 has_pending 不再触发重抓：第四轮 stale 里没有 SLUG_A 详情抓取
    calls: list[str] = []

    async def counting_page(session, proxy, url, attempts=3):
        calls.append(url)
        if url.endswith("/zh/bundles/"):
            return _list_html()
        if SLUG_A in url:
            return _detail_html(list(reversed(_TIER_ORDER)))
        return None

    monkeypatch.setattr(gmg, "_fetch_page", counting_page)
    await gmg.refresh_gmg_bundles()
    assert not any(SLUG_A in c for c in calls)  # 欠账耗尽 → 不再重抓


@pytest.mark.asyncio
async def test_detail_payload_tiers_pending(monkeypatch):
    """详情载荷：累进档位展开（每档=累计、新增带角标）+ unlockPriceMap +
    pending 三态（未找到/收录中）。"""
    async def fake_resolve(session, title, proxy):
        return APP_A1 if title == "Quarry Game" else None

    async def fake_run_sequential(specs, **_):
        return [{"id": 9}]

    async def no_sleep(_: float) -> None:
        return None

    _stub_network(monkeypatch)
    monkeypatch.setattr("app.domains.metadata.service._resolve_target", _as_target(fake_resolve))
    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run_sequential)
    monkeypatch.setattr(gmg.asyncio, "sleep", no_sleep)
    await gmg.refresh_gmg_bundles()

    d = await gmg.bundle_detail(SLUG_A)
    assert d is not None
    assert [t["id"] for t in d["tiers"]] == ["kingdoms", "dynasties", "empires"]
    assert d["tiers"][0]["count"] == 2
    assert d["tiers"][2]["count"] == 6  # 累计
    assert d["tiers"][0]["games"][0]["unlockPriceMap"] == {"CNY": 4500}
    assert d["tiers"][1]["games"][2]["isNew"] is True  # dynasties 档新增
    pending = {p["title"]: p["status"] for p in d["pending"]}
    assert pending["Tina Game"] in ("resolving", "not_found")
    assert d["tiers"][0]["games"][0]["appid"] == APP_A1


@pytest.mark.asyncio
async def test_empty_body_detail_keeps_stale(monkeypatch):
    """子站空响应（软封锁）→ 详情失败安全跳过，档位保留旧值待自愈。"""
    async def fake_resolve(session, title, proxy):
        return APP_A1 if title == "Quarry Game" else None

    async def fake_run_sequential(specs, **_):
        return [{"id": 9}]

    async def no_sleep(_: float) -> None:
        return None

    _stub_network(monkeypatch, detail_html=None)
    # A 包详情返回 None（_fetch_page 打桩返回 None 路径）
    async def empty_detail(session, proxy, url, attempts=3):
        if url.endswith("/zh/bundles/"):
            return _list_html()
        return None

    monkeypatch.setattr(gmg, "_fetch_page", empty_detail)
    monkeypatch.setattr("app.domains.metadata.service._resolve_target", _as_target(fake_resolve))
    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run_sequential)
    monkeypatch.setattr(gmg.asyncio, "sleep", no_sleep)
    res = await gmg.refresh_gmg_bundles()
    assert res["ok"] is True
    assert all(not d["ok"] for d in res["details"])  # 详情全失败但不炸
    async with get_session_factory()() as session:
        a = await session.get(GmgBundle, SLUG_A)
        assert a is not None and a.tiers_json is None  # 档位缺失 → 下轮自愈


@pytest.mark.asyncio
async def test_mark_seen_and_list(monkeypatch):
    async def fake_run_sequential(specs, **_):
        return [{"id": 9}]

    async def no_sleep(_: float) -> None:
        return None

    async def fake_resolve(session, title, proxy):
        return None

    _stub_network(monkeypatch)
    monkeypatch.setattr("app.domains.metadata.service._resolve_target", _as_target(fake_resolve))
    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run_sequential)
    monkeypatch.setattr(gmg.asyncio, "sleep", no_sleep)
    await gmg.refresh_gmg_bundles()

    lst = await gmg.list_bundles()
    assert {b["slug"] for b in lst["bundles"]} == {SLUG_A, SLUG_B}
    assert lst["bundles"][0]["priceMap"]["CNY"] in (4500, 6120)
    assert await gmg.mark_seen(SLUG_A) is True
    lst2 = await gmg.list_bundles()
    assert next(b for b in lst2["bundles"] if b["slug"] == SLUG_A)["isNew"] is False
    assert await gmg.mark_seen("gmgtest-unknown") is False
