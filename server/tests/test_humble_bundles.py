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


def _display_item(name: str, steam: bool = True, machine: str | None = None) -> str:
    """display 条目对象：machine_name 锚在开头（0.2.2 解析按锚切段），
    front_page_art/platforms 在后——与真实页键序一致。"""
    m = machine or ("item_" + name.lower().replace(" ", "_")[:20])
    obj = {
        "machine_name": m,
        "item_content_type": "game",
        "front_page_art": {"image_path": "x.png", "title": None, "image_text": name},
        "preview_image": "p.jpg",
        "developers": [],
        "is_clickable": True,
        "platforms_and_oses": ({"game": {"steam": ["windows"]}} if steam else {"game": {}}),
    }
    return json.dumps({m: obj}, ensure_ascii=False)


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
        _display_item(_NAME_RE_T, machine="matched_game"),
        _display_item(_NAME_CHARITY),      # 慈善条目：剔除
        _display_item(bundle_name),        # 包名自身：剔除
        _display_item("No Steam Item", steam=False),  # 无 steam：剔除
    ])
    # 档位数据（0.2.2）：initial 档含 matched_game，bt20 档新增 second_game
    tiers = (
        '"tier_pricing_data": {'
        '"initial": {"price|money": {"currency": "CNY", "amount": 33.53}, "is_free": false, "is_initial_tier": true, "should_be_included_in_tier_list": true}, '
        '"bt20": {"price|money": {"currency": "CNY", "amount": 67.06}, "is_free": false, "is_initial_tier": false}, '
        '"less_than_initial": {"is_free": true}}, '
        '"tier_display_data": {'
        '"initial": {"header": "Pay 33.53 or more", "tier_item_machine_names": ["matched_game"]}, '
        '"bt20": {"header": "Pay 67.06 or more to also unlock!", "tier_item_machine_names": ["second_game", "ghost_machine"]}} '
    )
    return (
        '<html><script>window.P = {'
        '"human_name": "Plan", "current_price|money": {"currency": "CNY", "amount": 1.0}, '
        '"preset_prices": ['
        '{"price|money": {"currency": "CNY", "amount": 33.53}, "qualifying_tier_id": "initial"}, '
        '{"price|money": {"currency": "CNY", "amount": 67.06}, "qualifying_tier_id": "bt20"}], '
        + tiers + ", "
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


def test_parse_detail_tiers():
    """0.2.2 档位：按价格升序、每档本档新增机名映射成标题、免费伪档剔除；
    映射不到的机名以可读占位标题保留（前端按未解析展示）。"""
    d = humble.parse_bundle_detail(_detail_html("Alpha Pack"), "Alpha Pack")
    tiers = d["tiers"]
    assert [t["id"] for t in tiers] == ["initial", "bt20"]
    assert tiers[0]["price_cny_fen"] == 3353 and tiers[0]["is_initial"] is True
    assert tiers[0]["titles"] == [_NAME_RE_T]
    assert tiers[1]["price_cny_fen"] == 6706
    # second_game 无对应 display 条目 → 可读占位；ghost_machine 同理
    assert tiers[1]["titles"] == ["Second Game", "Ghost Machine"]


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
    """列表 upsert → 详情解析价格/档位 → 包内游戏 appid 关联入库 →
    刷新尾收录扫描批量登记未入目录的 appid（0.2.2）。"""
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

    ingest_calls: list[list[int]] = []

    async def fake_run_sequential(specs, **_):
        ingest_calls.append(list(specs[0]["appids"]))
        return [{"id": 9}]

    monkeypatch.setattr(humble, "_fetch_page", fake_page)
    monkeypatch.setattr("app.domains.metadata.service._resolve_appid", fake_resolve)
    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run_sequential)
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
        # 档位落库（0.2.2）：两档、免费伪档被剔
        assert a.tiers_json and '"initial"' in a.tiers_json and '"bt20"' in a.tiers_json
        assert "less_than_initial" not in a.tiers_json
    # 刷新尾扫描：APP_A1 无 games 行 → 一次批量登记（两包同 appid 去重后 1 款）
    assert res["ingest"]["queued"] == 1
    assert ingest_calls == [[APP_A1]]


@pytest.mark.asyncio
async def test_refresh_refetches_missing_tiers_within_24h(monkeypatch):
    """0.3.1 档位自愈 + 0.3.2 欠账重试：updated_at 很新（24h 内）但
    tiers_json 为空的包立即重抓补档位；档位齐但仍有「未放弃欠账条目」
    （attempts<3）的包也重抓——解析/模糊规则升级后老数据吃到红利。"""
    import asyncio as _a
    from datetime import datetime

    monkeypatch.setattr(humble, "_REFRESH_LOCK", _a.Lock())

    now_dt = datetime(2026, 10, 7, 12, 0, 0)

    async def fake_page(session, proxy, url):
        if url.endswith("/games"):
            return _listing_html()
        return _detail_html("Alpha Pack" if SLUG_A in url else "Beta Pack")

    async def fake_resolve(session, title, proxy):
        return APP_A1

    async def fake_proxy():
        return None

    async def no_sleep(_: float) -> None:
        return None

    async def fake_run_sequential(specs, **_):
        return [{"id": 9}]

    monkeypatch.setattr(humble, "_fetch_page", fake_page)
    monkeypatch.setattr("app.domains.metadata.service._resolve_appid", fake_resolve)
    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run_sequential)
    monkeypatch.setattr(humble, "_strategy_proxy", fake_proxy)
    monkeypatch.setattr(humble.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        "app.crawler.utils.get_beijing_time_obj", lambda: now_dt.replace(tzinfo=None)
    )

    # 预置：两包都是 1 小时前刚刷过（updated_at 在 24h 内），A 缺档位、B 有档位
    async with get_session_factory()() as session:
        session.add(HumbleBundle(
            slug=SLUG_A, name="Alpha Pack", on_sale=True, game_count=1,
            first_seen_at=now_dt, last_seen_at=now_dt,
            updated_at=now_dt.replace(hour=11), tiers_json=None,
        ))
        session.add(HumbleBundle(
            slug=SLUG_B, name="Beta Pack", on_sale=True, game_count=1,
            first_seen_at=now_dt, last_seen_at=now_dt,
            updated_at=now_dt.replace(hour=11),
            tiers_json='[{"id": "initial", "price_cny_fen": 100, "header": "", "is_initial": true, "titles": ["Keep Me"]}]',
        ))
        # B 档位齐全但挂着未放弃欠账 → 0.3.2 也要重抓
        session.add(HumbleBundleGame(slug=SLUG_B, appid=None, title="Pending Debt", resolve_attempts=0))
        await session.commit()

    res = await humble.refresh_humble_bundles()
    assert res["ok"] is True
    # A 缺档位、B 有欠账 → 都进重抓（updated_at 都在 24h 内）
    assert {d.get("slug") for d in res["details"]} == {SLUG_A, SLUG_B}
    async with get_session_factory()() as session:
        a = await session.get(HumbleBundle, SLUG_A)
        assert a.tiers_json and '"bt20"' in a.tiers_json, "A 档位已补抓落库"
        b = await session.get(HumbleBundle, SLUG_B)
        assert '"Second Game"' in b.tiers_json, "B 重抓后档位随页面刷新"
        debt = (
            await session.execute(
                select(HumbleBundleGame).where(
                    HumbleBundleGame.slug == SLUG_B, HumbleBundleGame.title == "Pending Debt"
                )
            )
        ).scalars().first()
        # B 页面无此条目 → 旧欠账行清理（新页面条目另行落行）
        assert debt is None, "页面消失的欠账行清理"


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


@pytest.mark.asyncio
async def test_detail_tiers_cumulative_payload():
    """API 档位展开（0.2.3 累进售卖语义）：每档 games = 买那档实拿的全部
    （跨档去重累计、本档新增带 isNew），newGames/newCount/count 保留。"""
    import json as _json
    from datetime import datetime

    now_dt = datetime(2026, 10, 5)
    tiers_raw = [
        {"id": "initial", "price_cny_fen": 3353, "header": "", "is_initial": True,
         "titles": ["Matched Game"]},
        {"id": "bt20", "price_cny_fen": 6706, "header": "", "is_initial": False,
         "titles": ["Missing Game", "Matched Game"]},  # 重复标题跨档去重
    ]
    async with get_session_factory()() as session:
        session.add(HumbleBundle(
            slug=SLUG_A, name="Alpha Pack", on_sale=True, price_cny_fen=3353,
            game_count=2, first_seen_at=now_dt, last_seen_at=now_dt,
            acked_at=now_dt, tiers_json=_json.dumps(tiers_raw, ensure_ascii=False),
        ))
        session.add(HumbleBundleGame(slug=SLUG_A, appid=APP_A1, title="Matched Game"))
        session.add(HumbleBundleGame(slug=SLUG_A, appid=None, title="Missing Game"))
        await session.commit()

    d = await humble.bundle_detail(SLUG_A)
    assert d is not None
    tiers = d["tiers"]
    assert [t["id"] for t in tiers] == ["initial", "bt20"]

    # 累进清单：第 1 档 = 本档 1 款；第 2 档 = 第 1 档 + 本档新增（去重后 2 款）
    # unlockPriceCnyFen = 首次出现的档位价（最低解锁门槛，卡片角标数据源）
    assert tiers[0]["games"] == [
        {"title": "Matched Game", "appid": APP_A1, "isNew": True, "unlockPriceCnyFen": 3353}
    ]
    assert tiers[1]["games"] == [
        {"title": "Matched Game", "appid": APP_A1, "isNew": False, "unlockPriceCnyFen": 3353},
        {"title": "Missing Game", "appid": None, "isNew": True, "unlockPriceCnyFen": 6706},
    ]
    assert tiers[0]["newGames"][0] == {"title": "Matched Game", "appid": APP_A1}
    assert tiers[0]["count"] == 1
    # 第二档：重复的 Matched Game 被去重，只剩新增的 Missing（appid 未解析 None）
    assert tiers[1]["newGames"] == [{"title": "Missing Game", "appid": None}]
    assert tiers[1]["count"] == 2


@pytest.mark.asyncio
async def test_scan_missing_ingest_rules(monkeypatch):
    """收录扫描候选规则（0.2.2）：在售包 + appid 非空 + games 无行才登记；
    已有行 / 未解析 NULL / 下架包都不入候选；批量去重。"""
    from datetime import datetime

    from app.domains.games.models import Game
    from app.domains.humble.models import HumbleBundle

    calls: list[list[int]] = []

    async def fake_run(specs, **_):
        calls.append(sorted(specs[0]["appids"]))
        return [{"id": 1}]

    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run)

    now_dt = datetime(2026, 10, 5)
    async with get_session_factory()() as session:
        session.add(HumbleBundle(
            slug=SLUG_A, name="Alpha Pack", on_sale=True, game_count=3,
            first_seen_at=now_dt, last_seen_at=now_dt,
        ))
        session.add(HumbleBundle(
            slug=SLUG_B, name="Old Pack", on_sale=False, game_count=1,
            first_seen_at=now_dt, last_seen_at=now_dt,
        ))
        session.add(HumbleBundleGame(slug=SLUG_A, appid=APP_A1, title="Need Ingest"))
        session.add(HumbleBundleGame(slug=SLUG_A, appid=APP_A2, title="Already Have"))
        session.add(HumbleBundleGame(slug=SLUG_A, appid=None, title="Unresolved"))
        session.add(HumbleBundleGame(slug=SLUG_B, appid=APP_A1, title="OffSale Ignored"))
        session.add(Game(appid=APP_A2, name="Already Have", created_at=now_dt))
        await session.commit()

    res = await humble.scan_missing_ingest()
    assert res["ok"] is True
    # 唯一候选：APP_A1（在售包、已解析、无 games 行）；去重后一次批量
    assert res["candidates"] == 1 and res["queued"] == 1
    assert calls == [[APP_A1]]

    async with get_session_factory()() as session:
        g = await session.get(Game, APP_A1)
        if g is None:
            pass  # 本用例不真爬，只验登记；games 行由爬取链负责
        # 清理临时 Game 行
        have = await session.get(Game, APP_A2)
        if have is not None:
            await session.delete(have)
        await session.commit()


@pytest.mark.asyncio
async def test_scan_ingest_new_pack_first(monkeypatch):
    """0.2.3 NEW 包优先：候选按包 first_seen_at 降序——新扫出的包的欠账
    先入批（同日包内 appid 升序保底确定性）。"""
    from datetime import datetime

    calls: list[list[int]] = []

    async def fake_run(specs, **_):
        calls.append(list(specs[0]["appids"]))
        return [{"id": 1}]

    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run)

    async with get_session_factory()() as session:
        session.add(HumbleBundle(
            slug=SLUG_A, name="Old Pack", on_sale=True, game_count=0,
            first_seen_at=datetime(2026, 10, 1), last_seen_at=datetime(2026, 10, 1),
        ))
        session.add(HumbleBundle(
            slug=SLUG_B, name="New Pack", on_sale=True, game_count=0,
            first_seen_at=datetime(2026, 10, 6), last_seen_at=datetime(2026, 10, 6),
        ))
        # 旧包 appid 更大：证明排序吃的是包档期不是 appid 数值
        for appid in (990710, 990711):
            session.add(HumbleBundleGame(slug=SLUG_A, appid=appid, title=f"old{appid}"))
        for appid in (990701, 990702):
            session.add(HumbleBundleGame(slug=SLUG_B, appid=appid, title=f"new{appid}"))
        await session.commit()

    res = await humble.scan_missing_ingest()
    assert res["queued"] == 4 and res["batches"] == 1
    assert calls[0] == [990701, 990702, 990710, 990711], "新包欠账排前"


@pytest.mark.asyncio
async def test_scan_ingest_multi_batch_until_drained(monkeypatch):
    """0.2.3 轮内连续分批：候选超过单批上限时逐批吃满（每批跑完即下一批，
    本用例打桩直接「爬成」），不再留到下拍；批数被轮上限护栏封顶。"""
    from datetime import datetime

    calls: list[list[int]] = []

    async def fake_run(specs, **_):
        calls.append(list(specs[0]["appids"]))
        return [{"id": 1}]

    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run)
    monkeypatch.setattr(humble, "_INGEST_BATCH", 2)
    monkeypatch.setattr(humble, "_INGEST_MAX_BATCHES", 3)

    async with get_session_factory()() as session:
        session.add(HumbleBundle(
            slug=SLUG_A, name="Pack", on_sale=True, game_count=0,
            first_seen_at=datetime(2026, 10, 5), last_seen_at=datetime(2026, 10, 5),
        ))
        for appid in range(990721, 990729):  # 8 款欠账
            session.add(HumbleBundleGame(slug=SLUG_A, appid=appid, title=f"g{appid}"))
        await session.commit()

    res = await humble.scan_missing_ingest()
    # 8 候选、批 2、轮上限 3 → 3 批 6 款，余额 2 留下拍
    assert res == {"ok": True, "candidates": 8, "queued": 6, "batches": 3, "remaining": 2}
    assert calls == [[990721, 990722], [990723, 990724], [990725, 990726]]


def test_unesc_title():
    """0.3.2 标题转义还原：页面切段不经过 JSON 解码，\\u0027 会原样入库
    （A Juggler's Tale 关联永失败的根因）；畸形形态原样返回不炸。"""
    assert humble._unesc("Juggler\\u0027s Tale") == "Juggler's Tale"
    assert humble._unesc("A B") == "A B"
    assert humble._unesc('bare " quote') == 'bare " quote'


@pytest.mark.asyncio
async def test_resolve_attempts_exhaust_marks_not_found(monkeypatch):
    """0.3.2 非 Steam 标注：连续 3 轮解析失败 → 不再空转重试（storesearch
    省额度），bundle_detail pending 状态升 not_found（抽屉如实标注）。"""
    from datetime import datetime

    now_dt = datetime(2026, 10, 7)
    async with get_session_factory()() as session:
        session.add(HumbleBundle(
            slug=SLUG_A, name="Alpha Pack", on_sale=True, game_count=0,
            first_seen_at=now_dt, last_seen_at=now_dt,
        ))
        await session.commit()

    async def fake_page(session, proxy, url):
        return _detail_html("Alpha Pack")

    calls: list[str] = []

    async def never_resolves(session_, title, proxy_):
        calls.append(title)
        return None

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(humble, "_fetch_page", fake_page)
    monkeypatch.setattr("app.domains.metadata.service._resolve_appid", never_resolves)
    monkeypatch.setattr(humble.asyncio, "sleep", no_sleep)

    for _ in range(4):
        async with get_session_factory()() as s:
            bundle = await s.get(HumbleBundle, SLUG_A)
        await humble._sync_bundle_detail(None, None, bundle, mark_updated=False)

    # 第 4 轮不再尝试：只有前 3 轮各一次
    assert len(calls) == 3, "尝试 ≥3 后不再拾取（不永远空转）"

    d = await humble.bundle_detail(SLUG_A)
    assert d is not None
    assert [p["status"] for p in d["pending"]] == ["not_found"]


@pytest.mark.asyncio
async def test_scan_ingest_occupied_breaks_loop(monkeypatch):
    """爬虫被占（run_sequential 跳过全部 spec → 零结果）：本轮立即停，
    queued=0、欠账原样留在 remaining，由下拍/下轮续——不空转重试。"""
    from datetime import datetime

    calls: list[list[int]] = []

    async def fake_run(specs, **_):
        calls.append(list(specs[0]["appids"]))
        return []  # 占用跳过

    monkeypatch.setattr("app.domains.crawl.service.run_sequential", fake_run)

    async with get_session_factory()() as session:
        session.add(HumbleBundle(
            slug=SLUG_A, name="Pack", on_sale=True, game_count=0,
            first_seen_at=datetime(2026, 10, 5), last_seen_at=datetime(2026, 10, 5),
        ))
        for appid in (990741, 990742):
            session.add(HumbleBundleGame(slug=SLUG_A, appid=appid, title=f"g{appid}"))
        await session.commit()

    res = await humble.scan_missing_ingest()
    assert res["queued"] == 0 and res["batches"] == 0 and res["remaining"] == 2
    assert len(calls) == 1, "占用后不得继续排批空转"
