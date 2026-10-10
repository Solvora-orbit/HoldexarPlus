"""Fanatical 捆绑包域测试：合成 Algolia 命中/详情 JSON + 网络层打桩 + 真实落库。

模式同 test_humble_bundles.py（slug 用 fantest- 前缀隔离真实库；网络层
_get_text/_get_json/_algolia_query 全打桩，收录链 run_sequential 打桩）。
"""
from __future__ import annotations

import asyncio as _a
import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import get_session_factory, init_db  # noqa: E402
from app.domains.fanatical import service as fnt  # noqa: E402
from app.domains.fanatical.models import FanaticalBundle, FanaticalBundleGame  # noqa: E402

SLUG_T = "fantest-tiered"        # 档位包（独立集合两档）
SLUG_P = "fantest-pick"          # 选择包（数量档 + 全池）
APP_T1 = 1796470
APP_P1 = 1151340

_END = 1791982440  # 未来时刻（在售）


def _algolia_hits() -> list[dict]:
    """两包命中：tiered（两档独立集合）+ pick（池挂首档）；附一条 mystery 与
    一条 book-bundle 验证过滤。价格 map 全 7 币种形态（主单位，JPY ×100）。

    0.4.1 判据字段：steamId 缺失但 drm 含 steam = 检索兜底；无 drm = 直标。"""

    def tier_product(name: str, sid: int | None, drm: list | None = None) -> dict:
        p = {"name": name, "steamId": sid, "steamIdType": "app"}
        if drm is not None:
            p["drm"] = drm
        return p

    return [
        {
            "slug": SLUG_T, "name": "Test Tiered Bundle", "type": "bundle",
            "display_type": "bundle", "mystery": False,
            "cover": "t-cover.jpeg",
            "price": {"USD": 1.0, "EUR": 1.05, "JPY": 173.0},
            "available_valid_from": 1788354000, "available_valid_until": _END,
            "game_total": 3,
            "bundle_tiers": [
                {"displayTotal": 1, "products": [tier_product("Alpha Game", APP_T1, ["steam"])]},
                {"displayTotal": 2, "products": [
                    # steamId 缺失但 drm=steam：检索兜底应恢复（0.4.1）
                    tier_product("Beta Game", None, ["steam"]),
                    # 无 drm（站点未声明 Steam）：确知非 Steam 直标
                    tier_product("Gamma PDF", None),
                ]},
            ],
        },
        {
            "slug": SLUG_P, "name": "Test Pick Bundle", "type": "pick-and-mix",
            "display_type": "bundle", "mystery": False,
            "cover": "p-cover.jpeg",
            "price": {"USD": 5.99, "EUR": 5.99},
            "available_valid_until": _END, "game_total": 2,
            "bundle_tiers": [
                {"displayTotal": 5, "products": [
                    tier_product("Pool Game One", APP_P1, ["steam"]),
                    # 合辑 SKU（即使 drm=steam）：直标不烧检索
                    tier_product("Great Double-Pack", None, ["steam"]),
                ]},
                {"displayTotal": 7, "products": []},
            ],
        },
        {"slug": "fantest-mystery", "name": "Mystery Crate", "type": "bundle",
         "display_type": "bundle", "mystery": True, "bundle_tiers": []},
        {"slug": "fantest-book", "name": "Book Bundle", "type": "bundle",
         "display_type": "book-bundle", "mystery": False, "bundle_tiers": []},
    ]


def _all_en() -> dict:
    """pickandmix 数量档价格（游戏 pick 与其他类混在同一数组，按 slug 取）。
    价格量纲：详情/ALL API 为**分**（与 Algolia 主单位不同）。"""
    return {"pickandmix": [
        {"slug": SLUG_P, "tiers": [
            {"quantity": 5, "price": {"USD": 599, "JPY": 87000}},
            {"quantity": 7, "price": {"USD": 805}},
        ]},
        {"slug": "fantest-other-book", "tiers": [
            {"quantity": 2, "price": {"USD": 199}},
        ]},
    ]}


def _product_detail() -> dict:
    """tiered 详情：bundles[] 每元素一档价（与 Algolia 档序一致；分为量纲）。"""
    return {"bundles": [
        {"price": {"USD": 100, "JPY": 17300}},
        {"price": {"USD": 1299}},
    ]}


@pytest_asyncio.fixture(autouse=True)
async def _seed_clean():
    await init_db()
    yield
    async with get_session_factory()() as session:
        for slug in (SLUG_T, SLUG_P, "fantest-mystery", "fantest-book"):
            bundle = await session.get(FanaticalBundle, slug)
            if bundle is not None:
                for g in (
                    await session.execute(
                        select(FanaticalBundleGame).where(FanaticalBundleGame.slug == slug)
                    )
                ).scalars():
                    await session.delete(g)
                await session.delete(bundle)
        await session.commit()


@pytest.fixture(autouse=True)
def _patch_network(monkeypatch):
    async def fake_proxy():
        return None

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(fnt, "_strategy_proxy", fake_proxy)
    monkeypatch.setattr(fnt.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(fnt, "_REFRESH_LOCK", _a.Lock())


def _stub_full_chain(monkeypatch) -> None:
    """打桩整条刷新链（Algolia 两包 + pick 价格 + tiered 详情价）。
    resolver 默认桩返回 None（ drm=steam 缺 id 的条目走 attempts 记账），
    需要特定结果的测试在调用后再覆盖。"""

    async def fake_resolve(session, title, proxy):
        return None

    async def fake_get_text(session, proxy, url, headers):
        return "window.searchKey = 'abc';"

    async def fake_get_json(session, proxy, url, headers):
        if url.endswith("/api/all/en"):
            return _all_en()
        if url.endswith(f"/api/products/{SLUG_T}/en"):
            return _product_detail()
        return None

    async def fake_algolia(session, proxy, key):
        return _algolia_hits()

    monkeypatch.setattr("app.domains.metadata.service._resolve_appid", fake_resolve)
    monkeypatch.setattr(fnt, "_get_text", fake_get_text)
    monkeypatch.setattr(fnt, "_get_json", fake_get_json)
    monkeypatch.setattr(fnt, "_algolia_query", fake_algolia)


# ─── 解析层 ───────────────────────────────────────────────────────


def test_parse_hits_filters_and_kinds():
    rows = fnt.parse_hits(_algolia_hits())
    slugs = [r["slug"] for r in rows]
    assert slugs == [SLUG_T, SLUG_P]  # mystery 与 book-bundle 被剔
    t = rows[0]
    assert t["kind"] == "tiered"
    assert len(t["tiers"]) == 2
    assert t["tiers"][0]["titles"] == ["Alpha Game"]
    # 档间独立集合：tier1 不含 tier0 的游戏
    assert "Alpha Game" not in t["tiers"][1]["titles"]
    # cover 文件名拼 cdn-ext 直链
    assert t["image"] == "https://cdn-ext.fanatical.com/production/product/1280x720/t-cover.jpeg"
    assert t["end_at"] is not None and t["end_at"].year == 2026
    p = rows[1]
    assert p["kind"] == "pick"
    assert [x for tier in p["tiers"] for x in tier["titles"]] == ["Pool Game One", "Great Double-Pack"]
    assert {"name": "Pool Game One", "steamId": APP_P1, "steamIdType": "app", "drm": ["steam"]} in p["steam_products"]


def test_price_map_minor_and_min():
    # Algolia 价为主单位浮点 → ×100 存分；详情/ALL API 已是分 → _minor_map 原样取整
    assert fnt._price_map({"USD": 1.0, "JPY": 173.0}) == {"USD": 100, "JPY": 17300}
    assert fnt._minor_map({"USD": 5999, "JPY": 629000}) == {"USD": 5999, "JPY": 629000}
    assert fnt._minor_map({"USD": 59.99}) == {"USD": 60}  # 畸形小数按分取整（容错不炸）
    assert fnt._min_price_map([{"USD": 1299}, {"USD": 100}]) == {"USD": 100}
    # 无 USD 档可跑比价 → 退首个非空（不做跨币种折算）
    assert fnt._min_price_map([{"JPY": 10000}, {"EUR": 10500}]) == {"JPY": 10000}
    assert fnt._min_price_map([None, {"EUR": 10500}]) == {"EUR": 10500}
    assert fnt._price_map(None) is None and fnt._price_map({"bad": "x"}) is None


def test_parse_pick_prices_and_product_tiers():
    pp = fnt.parse_pick_prices(_all_en())
    assert pp[SLUG_P] == [
        {"quantity": 5, "price": {"USD": 599, "JPY": 87000}},
        {"quantity": 7, "price": {"USD": 805}},
    ]
    tiers = fnt.parse_product_tiers(_product_detail())
    assert [t["price"]["USD"] for t in tiers] == [100, 1299]


def test_merge_tiers_and_mismatch_guard():
    rows = fnt.parse_hits(_algolia_hits())
    merged = fnt.merge_tiers(rows[0], [{"price": {"USD": 100}}, {"price": {"USD": 1299}}])
    assert merged is not None and merged[0]["price"] == {"USD": 100}
    assert merged[1]["titles"] == ["Beta Game", "Gamma PDF"]
    # 档数不一致（页面切换期）→ 价格留空宁缺勿错
    merged2 = fnt.merge_tiers(rows[0], [{"price": {"USD": 100}}])
    assert merged2 is not None and all(t["price"] is None for t in merged2)
    pick_tiers = fnt.merge_tiers(rows[1], fnt.parse_pick_prices(_all_en())[SLUG_P])
    assert pick_tiers is not None and pick_tiers[0]["id"] == "q5"
    assert all(t["titles"] == [] for t in pick_tiers)  # pick 池在 games 表


# ─── 链路（真实落库）──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_upsert_games_and_tiers(monkeypatch):
    """全链：Algolia 列表 → upsert → pick 价格 → tiered 详情价 → 条目直取
    appid / drm 权威判据（steamId 缺走检索兜底、无 drm 直标、合辑直标）→
    档位与条目计数落库 → 收录扫描登记。"""
    ingest_calls: list[list[int]] = []
    resolve_calls: list[str] = []

    async def fake_resolve(session, title, proxy):
        resolve_calls.append(title)
        # 检索兜底：Beta Game 恢复，Others 查不到（attempts 记账）
        return 2221390 if title == "Beta Game" else None

    async def fake_run_sequential(specs, **_):
        ingest_calls.append(list(specs[0]["appids"]))
        return [{"id": 9}]

    _stub_full_chain(monkeypatch)
    monkeypatch.setattr("app.domains.metadata.service._resolve_appid", fake_resolve)
    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run_sequential)

    res = await fnt.refresh_fanatical_bundles()
    assert res["ok"] is True and res["listed"] == 2
    # 检索兜底只打 drm=steam 且缺 id 的条目（Beta Game；合辑被拦不烧检索）
    assert resolve_calls == ["Beta Game"]
    async with get_session_factory()() as session:
        t = await session.get(FanaticalBundle, SLUG_T)
        assert t is not None and t.kind == "tiered" and t.on_sale is True
        assert json.loads(t.price_map_json)["USD"] == 100  # 最低档
        assert t.url.endswith(f"/en/bundle/{SLUG_T}")
        tiers_t = json.loads(t.tiers_json)
        assert tiers_t[0]["price"] == {"USD": 100, "JPY": 17300}
        assert tiers_t[1]["price"] == {"USD": 1299}
        games_t = (
            await session.execute(
                select(FanaticalBundleGame).where(FanaticalBundleGame.slug == SLUG_T)
            )
        ).scalars().all()
        by_title = {g.title: g for g in games_t}
        assert by_title["Alpha Game"].appid == APP_T1
        assert by_title["Alpha Game"].resolve_attempts == 0
        # steamId 缺 + drm=steam：检索兜底恢复关联（0.4.1 根修）
        assert by_title["Beta Game"].appid == 2221390
        # 无 drm：确知非 Steam 直置满 → not_found
        assert by_title["Gamma PDF"].appid is None
        assert by_title["Gamma PDF"].resolve_attempts == fnt.RESOLVE_MAX_ATTEMPTS
        assert t.game_count == 3

        p = await session.get(FanaticalBundle, SLUG_P)
        assert p is not None and p.kind == "pick"
        assert json.loads(p.price_map_json)["USD"] == 599
        assert p.url.endswith(f"/en/pick-and-mix/{SLUG_P}")
        games_p = (
            await session.execute(
                select(FanaticalBundleGame).where(FanaticalBundleGame.slug == SLUG_P)
            )
        ).scalars().all()
        assert len(games_p) == 2
        by_title_p = {g.title: g for g in games_p}
        assert by_title_p["Pool Game One"].appid == APP_P1
        # 合辑 SKU：即使 drm=steam 也直标（无单 app 对应，不烧检索）
        assert by_title_p["Great Double-Pack"].appid is None
        assert by_title_p["Great Double-Pack"].resolve_attempts == fnt.RESOLVE_MAX_ATTEMPTS
        assert "Great Double-Pack" not in resolve_calls
    # 收录扫描：三个已关联 appid（Alpha/Beta/Pool One）均无 games 行 → 一次批量登记
    assert res["items"] == 5
    assert ingest_calls and sorted(ingest_calls[0]) == sorted([APP_T1, APP_P1, 2221390])


@pytest.mark.asyncio
async def test_refresh_idempotent_and_offsale(monkeypatch):
    """重复刷新幂等（不重复落行）；列表消失的包置下架保留历史。"""
    _stub_full_chain(monkeypatch)
    await fnt.refresh_fanatical_bundles()
    await fnt.refresh_fanatical_bundles()
    async with get_session_factory()() as session:
        games_t = (
            await session.execute(
                select(FanaticalBundleGame).where(FanaticalBundleGame.slug == SLUG_T)
            )
        ).scalars().all()
        assert len(games_t) == 3  # 幂等：不重复落行

    # 第二轮只给 T 包（P 消失）→ P 下架保留
    async def only_t(session, proxy, key):
        return [h for h in _algolia_hits() if h["slug"] != SLUG_P]

    monkeypatch.setattr(fnt, "_algolia_query", only_t)
    await fnt.refresh_fanatical_bundles()
    async with get_session_factory()() as session:
        p = await session.get(FanaticalBundle, SLUG_P)
        assert p is not None and p.on_sale is False


@pytest.mark.asyncio
async def test_detail_payload_tiered_vs_pick(monkeypatch):
    """详情载荷：tiered 档内条目 unlockPriceMap=该档价；pick 每档=全池、
    unlockPriceMap=最低档价；pending 三态（非 Steam→not_found）。"""
    _stub_full_chain(monkeypatch)
    await fnt.refresh_fanatical_bundles()

    d = await fnt.bundle_detail(SLUG_T)
    assert d is not None and d["kind"] == "tiered"
    assert d["tiers"][0]["count"] == 1
    assert d["tiers"][0]["games"][0]["unlockPriceMap"] == {"USD": 100, "JPY": 17300}
    assert d["tiers"][1]["games"][1]["appid"] is None
    pending_t = {p["title"]: p["status"] for p in d["pending"]}
    assert pending_t["Gamma PDF"] == "not_found"  # 非 Steam 直标

    dp = await fnt.bundle_detail(SLUG_P)
    assert dp is not None and dp["kind"] == "pick"
    # 数量档：count=选 N；games=全池；解锁价 = 最低档
    assert [t["count"] for t in dp["tiers"]] == [5, 7]
    assert len(dp["tiers"][0]["games"]) == 2 and len(dp["tiers"][1]["games"]) == 2
    assert dp["tiers"][1]["games"][0]["unlockPriceMap"] == {"USD": 599, "JPY": 87000}


@pytest.mark.asyncio
async def test_mark_seen_and_list(monkeypatch):
    _stub_full_chain(monkeypatch)
    await fnt.refresh_fanatical_bundles()

    lst = await fnt.list_bundles()
    assert {b["slug"] for b in lst["bundles"]} == {SLUG_T, SLUG_P}
    assert lst["bundles"][0]["priceMap"]["USD"] in (100, 599)
    assert all(b["isNew"] for b in lst["bundles"])
    assert await fnt.mark_seen(SLUG_T) is True
    lst2 = await fnt.list_bundles()
    assert next(b for b in lst2["bundles"] if b["slug"] == SLUG_T)["isNew"] is False
    assert await fnt.mark_seen("fantest-unknown") is False


def test_combo_sku_detection():
    """0.4.1 合辑 SKU 拦截：Double/Triple Pack、A + B、Pack/Bundle/Collection
    收尾——站点自造 SKU 无单 app 对应，直标不烧检索；真实游戏名不误伤。"""
    assert fnt.is_combo_sku("Action-Roguelike Fantasy Double-Pack")
    assert fnt.is_combo_sku("AMID EVIL & DUSK Double Pack")
    assert fnt.is_combo_sku("Rogue Trooper, AvP Classic 2000 & Dredd Triple Pack")
    assert fnt.is_combo_sku("Balrum + Deepest Chamber: Resurrection Pack")
    assert fnt.is_combo_sku("Point and Click Premium Bundle")
    assert fnt.is_combo_sku("Nobodies: Complete Bundle")
    assert fnt.is_combo_sku("Alt F-4 Pack")
    assert not fnt.is_combo_sku("Dreamscaper")
    assert not fnt.is_combo_sku("Teslagrad 2")
    assert not fnt.is_combo_sku("Police Stories - Complete Edition")
    assert not fnt.is_combo_sku("Coromon Deluxe Edition")


def test_extract_search_key():
    assert fnt.extract_search_key("window.searchKey = 'abc123';") == "abc123"
    assert fnt.extract_search_key('window.searchKey="double"') is None  # 只认单引号形态
    assert fnt.extract_search_key("") is None


@pytest.mark.asyncio
async def test_ingest_scan_skips_existing(monkeypatch):
    """收录链：games 已有行的 appid 退出候选；爬虫占用返回 0 停轮。"""
    from app.domains.games.models import Game

    calls: list[list[int]] = []

    async def fake_run_sequential(specs, **_):
        calls.append(list(specs[0]["appids"]))
        return [{"id": 1}]

    # run_sequential 先于 refresh 打桩：刷新尾自带一轮收录扫描
    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run_sequential)
    _stub_full_chain(monkeypatch)
    await fnt.refresh_fanatical_bundles()
    async with get_session_factory()() as s:
        if await s.get(Game, APP_T1) is None:
            s.add(Game(appid=APP_T1, name="Alpha Game"))
        await s.commit()
    calls.clear()
    res = await fnt.scan_missing_ingest()
    # Alpha/Pool One 直取关联 → 候选；Beta Game 缺 steamId 检索未中（attempts=1）不入候选
    assert res["candidates"] == 1
    assert calls == [[APP_P1]]
    # 爬虫占用（返回空）→ queued 0
    async def busy(specs, **_):
        return []

    monkeypatch.setattr("app.domains.crawl.service.run_sequential", busy)
    async with get_session_factory()() as s:
        row = await s.get(Game, APP_T1)
        if row is not None:
            await s.delete(row)
        await s.commit()
    res2 = await fnt.scan_missing_ingest()
    assert res2["queued"] == 0
