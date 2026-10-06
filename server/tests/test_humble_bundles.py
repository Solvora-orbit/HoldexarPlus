"""HB 商店捆绑包链验收（humble 域，0.2.0）。

纯逻辑：列表 products 解析（游戏包过滤/去重）、详情页解析（价格最低档
CNY、steam 平台条目提取与慈善/自名剔除）、站内路径安全校验。
链路：合成列表页 + 合成详情页 + 合成 storesearch 解析结果，走真实落库
（slug 用 hbtest 前缀常量 + 精确清理，不污染真实库），验证 upsert/价格/
关联。
"""
import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_session_factory, init_db  # noqa: E402
from app.domains.games.models import Game  # noqa: E402
from app.domains.humble import service as humble  # noqa: E402
from app.domains.humble.models import HumbleBundle, HumbleBundleGame  # noqa: E402

SLUG_A = "hbtest-alpha-bundle"
SLUG_B = "hbtest-beta-bundle"
APP_A1 = 990501
APP_A2 = 990502

_NAME_RE_T = "Test Game Alpha"
_NAME_CHARITY = "Child's Play Charity"


def _display_item(name: str, steam: bool = True) -> str:
    obj = {
        "item": {
            "front_page_art": {"image_path": "x.png", "title": None, "image_text": name},
            "preview_image": "p.jpg",
            "developers": [],
            "is_clickable": True,
            "platforms_and_oses": ({"game": {"steam": ["windows"]}} if steam else {"game": {}}),
        }
    }
    return json.dumps(obj, ensure_ascii=False)


def _listing_html() -> str:
    products = [
        {
            "type": "bundle", "machine_name": "alpha_bundle", "tile_name": "Alpha Pack",
            "tile_image": "https://hb.imgix.net/a.jpg",
            "product_url": f"/games/{SLUG_A}",
            "start_date|datetime": "2026-10-01T17:00:00",
            "end_date|datetime": "2026-10-20T17:00:00",
        },
        {
            "type": "bundle", "machine_name": "beta_bundle", "tile_name": "Beta Pack",
            "tile_image": "https://hb.imgix.net/b.jpg",
            "product_url": f"/games/{SLUG_B}",
            "start_date|datetime": "2026-10-02T17:00:00",
            "end_date|datetime": "2026-10-21T17:00:00",
        },
        # software 包与外链混入：都不该收进游戏捆绑包列表
        {"type": "bundle", "machine_name": "sw", "tile_name": "Sw", "product_url": "/software/x"},
        {"type": "bundle", "machine_name": "evil", "tile_name": "Evil",
         "product_url": "https://evil.example.com/games/pwn"},
        {"type": "game", "machine_name": "single", "tile_name": "Single",
         "product_url": "/games/single-one"},
    ]
    blob = json.dumps({"sections": [{"products": products}]}, ensure_ascii=False)
    return f'<html><script>window.D = {blob};</script></html>'


def _detail_html(bundle_name: str) -> str:
    items = " ".join([
        _display_item(_NAME_RE_T),
        _display_item(_NAME_CHARITY),      # 慈善条目：剔除
        _display_item(bundle_name),        # 包名自身：剔除
        _display_item("No Steam Item", steam=False),  # 无 steam：剔除
    ])
    return (
        '<html><script>window.P = {'
        '"human_name": "Plan", "current_price|money": {"currency": "CNY", "amount": 1.0}, '
        '"preset_prices": ['
        '{"price|money": {"currency": "CNY", "amount": 33.53}, "qualifying_tier_id": "initial"}, '
        '{"price|money": {"currency": "CNY", "amount": 67.06}, "qualifying_tier_id": "t2"}], '
        '"sections": [' + items + "]};</script></html>"
    )


# ─── 纯逻辑 ────────────────────────────────────────────────────────


def test_safe_game_path_accepts_and_rejects():
    assert humble._safe_game_path("/games/x-bundle") == "/games/x-bundle"
    assert humble._safe_game_path("https://www.humblebundle.com/games/y") == "/games/y"
    assert humble._safe_game_path("https://zh.humblebundle.com/games/z") == "/games/z"
    # 非游戏类目 / 外域 / 协议 / 注入形态一律拒绝
    assert humble._safe_game_path("/software/x") is None
    assert humble._safe_game_path("https://evil.example.com/games/pwn") is None
    assert humble._safe_game_path("http://127.0.0.1:8/games/x") is None
    assert humble._safe_game_path("https://humblebundle.com.evil/x") is None
    assert humble._safe_game_path("") is None


def test_parse_games_list_filters():
    rows = humble.parse_games_list(_listing_html())
    slugs = [r["slug"] for r in rows]
    assert slugs == [SLUG_A, SLUG_B]
    assert rows[0]["name"] == "Alpha Pack"
    assert rows[0]["image"] == "https://hb.imgix.net/a.jpg"
    assert rows[0]["end_at"].year == 2026 and rows[0]["end_at"].month == 10


def test_parse_detail_price_and_titles():
    d = humble.parse_bundle_detail(_detail_html("Alpha Pack"), "Alpha Pack")
    # 最低档价（元→分）
    assert d["price_cny_fen"] == 3353
    # 只留 steam 游戏条目：慈善与自名剔除
    assert d["titles"] == [_NAME_RE_T]


def test_cny_to_fen_rejects_foreign_currency():
    assert humble._cny_to_fen({"currency": "USD", "amount": 9.99}) is None
    assert humble._cny_to_fen({"currency": "CNY", "amount": 9.99}) == 999
    assert humble._cny_to_fen(None) is None


# ─── 链路（真实落库 + 合成数据）──────────────────────────────────


async def _purge(session, slugs: list[str]) -> None:
    """ORM 对象级清理（不走裸 SQL delete 语句，测试库与真实库同文件）。"""
    for slug in slugs:
        bundle = await session.get(HumbleBundle, slug)
        if bundle is not None:
            for g in (
                await session.execute(
                    select(HumbleBundleGame).where(HumbleBundleGame.slug == slug)
                )
            ).scalars():
                await session.delete(g)
            await session.delete(bundle)


@pytest_asyncio.fixture(autouse=True)
async def _seed_clean():
    await init_db()
    yield
    async with get_session_factory()() as session:
        await _purge(session, [SLUG_A, SLUG_B])
        await session.commit()


@pytest.mark.asyncio
async def test_refresh_upsert_price_and_assoc(monkeypatch):
    """列表 upsert → 详情解析价格 → 包内游戏 appid 关联入库。"""
    import asyncio as _a

    monkeypatch.setattr(humble, "_REFRESH_LOCK", _a.Lock())

    async def fake_page(session, proxy, url):
        if url.endswith("/games"):
            return _listing_html()
        return _detail_html("Alpha Pack" if SLUG_A in url else "Beta Pack")

    async def fake_resolve(session, title, proxy):
        # B 包解析失败 → 欠账不入库，下轮 known 无自动重试
        return APP_A1 if title == _NAME_RE_T else None

    async def fake_proxy():
        return None

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(humble, "_fetch_page", fake_page)
    monkeypatch.setattr("app.domains.metadata.service._resolve_appid", fake_resolve)
    monkeypatch.setattr(humble, "_strategy_proxy", fake_proxy)
    monkeypatch.setattr(humble.asyncio, "sleep", no_sleep)

    res = await humble.refresh_humble_bundles()
    assert res["ok"] is True
    assert res["listed"] == 2
    async with get_session_factory()() as session:
        a = await session.get(HumbleBundle, SLUG_A)
        assert a is not None and a.on_sale is True and a.price_cny_fen == 3353
        games_a = (
            await session.execute(
                select(HumbleBundleGame).where(HumbleBundleGame.slug == SLUG_A)
            )
        ).scalars().all()
        assert len(games_a) == 1
        assert games_a[0].appid == APP_A1


@pytest.mark.asyncio
async def test_bundle_detail_payload():
    """列表端点过滤在售包；详情端点返回包内游戏条目；未知 slug 返回 None。"""
    from datetime import datetime

    now_dt = datetime(2026, 10, 5)
    async with get_session_factory()() as session:
        # 包内游戏要能在 games 表出现（比价载荷 join 的是目录行）
        if await session.get(Game, APP_A1) is None:
            session.add(Game(appid=APP_A1, name=_NAME_RE_T, created_at=now_dt))
        session.add(HumbleBundle(
            slug=SLUG_A, name="Alpha Pack", on_sale=True,
            price_cny_fen=3353, game_count=1,
            first_seen_at=now_dt, last_seen_at=now_dt,
        ))
        session.add(HumbleBundleGame(slug=SLUG_A, appid=APP_A1, title=_NAME_RE_T))
        await session.commit()

    listing = await humble.list_bundles()
    assert any(b["slug"] == SLUG_A for b in listing["bundles"])

    detail = await humble.bundle_detail(SLUG_A)
    assert detail is not None and detail["name"] == "Alpha Pack"
    assert detail["total"] >= 1 and any(
        g["appid"] == APP_A1 for g in detail["items"]
    )

    assert await humble.bundle_detail("hbtest-no-such") is None

    # 清掉临时 Game 行（只删测试自己插的）
    async with get_session_factory()() as session:
        game = await session.get(Game, APP_A1)
        if game is not None and game.name == _NAME_RE_T:
            await session.delete(game)
        await session.commit()


@pytest.mark.asyncio
async def test_unresolved_entries_persist_with_pending(monkeypatch):
    """0.2.1 全量条目：未解析标题也落行（appid NULL）——外层计数 = 抽屉可见
    行数；详情载荷 pending 标 resolving，与 items 相加对得上 gameCount。"""
    import asyncio as _a

    monkeypatch.setattr(humble, "_REFRESH_LOCK", _a.Lock())

    async def fake_page(session, proxy, url):
        if url.endswith("/games"):
            return _listing_html()
        # 两页各含解析成功/失败两个标题
        return (
            '<html><script>window.P = {"preset_prices": ['
            '{"price|money": {"currency": "CNY", "amount": 10.0}, "qualifying_tier_id": "initial"}], '
            '"sections": [' + _display_item("Matched Game") + " " + _display_item("Missing Game") +
            "]};</script></html>"
        )

    async def fake_resolve(session, title, proxy):
        return APP_A1 if title == "Matched Game" else None

    async def fake_proxy():
        return None

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(humble, "_fetch_page", fake_page)
    monkeypatch.setattr("app.domains.metadata.service._resolve_appid", fake_resolve)
    monkeypatch.setattr(humble, "_strategy_proxy", fake_proxy)
    monkeypatch.setattr(humble.asyncio, "sleep", no_sleep)

    res = await humble.refresh_humble_bundles()
    assert res["ok"] is True
    async with get_session_factory()() as session:
        a = await session.get(HumbleBundle, SLUG_A)
        assert a is not None
        assert a.game_count == 2  # 匹配 1 + 未解析 1
        rows = (
            await session.execute(
                select(HumbleBundleGame.title, HumbleBundleGame.appid).where(
                    HumbleBundleGame.slug == SLUG_A
                )
            )
        ).all()
        assert {t: ap for t, ap in rows} == {"Matched Game": APP_A1, "Missing Game": None}

    detail = await humble.bundle_detail(SLUG_A)
    assert detail is not None
    assert detail["gameCount"] == 2
    # 未入目录的 appid（APP_A1 此刻 games 无行）= ingesting；未解析 = resolving
    by_title = {p["title"]: p["status"] for p in detail["pending"]}
    assert by_title.get("Missing Game") == "resolving"
    assert by_title.get("Matched Game") == "ingesting"


@pytest.mark.asyncio
async def test_new_badge_and_mark_seen():
    """acked_at NULL → 列表 isNew=True；mark_seen 后已读（幂等）。"""
    from datetime import datetime

    now_dt = datetime(2026, 10, 6)
    async with get_session_factory()() as session:
        session.add(HumbleBundle(
            slug=SLUG_B, name="Beta Pack", on_sale=True,
            price_cny_fen=3360, game_count=0, acked_at=None,
            first_seen_at=now_dt, last_seen_at=now_dt,
        ))
        await session.commit()

    listing = await humble.list_bundles()
    row_b = next(b for b in listing["bundles"] if b["slug"] == SLUG_B)
    assert row_b["isNew"] is True

    assert await humble.mark_seen(SLUG_B) is True
    listing2 = await humble.list_bundles()
    row_b2 = next(b for b in listing2["bundles"] if b["slug"] == SLUG_B)
    assert row_b2["isNew"] is False

    # 幂等：二次标记不报错；未知 slug 返回 False
    assert await humble.mark_seen(SLUG_B) is True
    assert await humble.mark_seen("hbtest-no-such") is False
