"""HB 当月包游戏侧标记链验收（refresh_hb_choice，无账本表）。

纯逻辑：月份标签换算 / 页面解析（转义 JSON + 区块重复去重）/ storesearch
命中匹配。链路：合成 membership 页 + 合成解析结果，走真实标记落库
（合成 990xxx appid 播种 + 清理，不污染真实库），验证：
- steam 条目打标（is_hb + hb_data 月份标签），非 steam 条目跳过；
- 缺行占位 updated_at NULL（回补池判据），已有行保留旧标记并逗号续写；
- 解析失败条目进 unresolved 不阻塞其余条目；
- machine_name 未变时幂等跳过（app_settings 记账）；
- 补价通道：无任何价格行的标记行进首爬候选，已有价格行（missing 状态）
  的归补抓账本不进，空候选不发起爬取。
"""
import html
import json
import sqlite3
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_session_factory, init_db  # noqa: E402
from app.domains.games.models import Game, GameCurrentPrice  # noqa: E402
from app.domains.metadata import service as hb  # noqa: E402
from app.domains.settings.models import AppSetting  # noqa: E402

TEST_STATE_KEY = "hb_choice_last_month_test"
MACHINE = "septtest_2026_choice"
PRODUCT = "September 2026 Humble Choice"
LABEL = "HB慈善包26年9月包"

APP_NEW = 990_401  # 缺行占位
APP_EXISTING = 990_402  # 已有行（带旧月份标记，验证续写）
APP_UNRESOLVED = 990_403  # storesearch 未命中（只进 unresolved）
OLD_LABEL = "HB慈善包25年3月包"

_RESOLVE_MAP = {
    "Frosttest 2": APP_NEW,
    "Keytest": APP_EXISTING,
    "Missing Game": None,
}


def _item(title: str, delivery: list[str], steam_count: int | None = 100) -> dict:
    rating = {} if steam_count is None else {
        "steam_count": steam_count, "steam_percent": 0.9,
    }
    return {
        "recommendation_copy_dict": {"copy": " synth "},
        "delivery_methods": delivery,
        "title": title,
        "msrp": {"currency": "USD", "amount": 9.99},
        "user_rating": rating,
    }


def _membership_html() -> str:
    """合成 membership 页：条目为转义 JSON（引号 &#34;），清单重复两轮，
    另含一个 recommendation 文案内嵌花括号的条目（验证 raw_decode 抗性）。"""
    items = {
        "frosttest2": _item("Frosttest 2", ["steam"]),
        "keytest": _item("Keytest", ["steam"]),
        "plustest": _item("Plus Test", ["other-key"]),
        "missinggame": _item("Missing Game", ["steam"]),
    }
    # 花括号进字符串：模拟真实页 recommendation 文案含 {} 的场景
    items["keytest"]["recommendation_copy_dict"]["copy"] = "build {oil} coal"
    blocks = []
    for name, obj in items.items():
        blocks.append(f'"{name}": {json.dumps(obj, ensure_ascii=False)}')
    # quote=True：引号转义成 &#34;，与真实页转义形态一致
    blob = html.escape(", ".join(blocks))
    header = (
        f'{{"activeContentMachineName": "{MACHINE}", '
        f'"navbarOptions": {{"productHumanName": "{PRODUCT}", '
        f'"activeContentEndDate|datetime": "2026-10-06T17:00:00"}}}}'
    )
    return f"<html><script>var X = {header};</script><div data-json=\"{blob}\"></div>" \
           f"<div data-json=\"{blob}\"></div></html>"


# ─── 纯逻辑 ────────────────────────────────────────────────────────


def test_month_label_from_product_name():
    assert hb._choice_month_label(MACHINE, PRODUCT) == LABEL


def test_month_label_from_machine_name():
    assert hb._choice_month_label("july_2026_choice", None) == "HB慈善包26年7月包"


def test_month_label_fallback_generic():
    assert hb._choice_month_label("whatever", "Mystery Box") == "HB慈善包"


def test_parse_choice_page_dedup_and_fields():
    parsed = hb._parse_choice_page(_membership_html())
    assert parsed["machineName"] == MACHINE
    assert parsed["productName"] == PRODUCT
    assert parsed["endDate"] == "2026-10-06T17:00:00"
    assert set(parsed["items"]) == {
        "frosttest2", "keytest", "plustest", "missinggame",
    }
    assert parsed["items"]["keytest"]["delivery_methods"] == ["steam"]
    # 区块重复去重：条目对象完整解析（花括号文案不截断）
    assert parsed["items"]["keytest"]["recommendation_copy_dict"]["copy"] == "build {oil} coal"


def test_pick_appid_matching_tiers():
    hits = [
        {"name": "Something Else", "id": "111"},
        {"name": "Keytest: Deluxe Edition", "id": "222"},
        {"name": "KEYTEST", "id": "333"},  # 大小写归一命中
    ]
    # exact 优先于清洗命中（Deluxe 副标题剥掉后同形，但不能抢先）
    assert hb._pick_appid("Keytest", hits) == 333
    assert hb._pick_appid("keytest !", hits) == 222  # 无 exact，清洗层兜底
    assert hb._pick_appid("Nothing", hits) is None


def test_pick_appid_playtest_normalized():
    hits = [{"name": "Eldtest Escape", "id": "444"}, {"name": "Eldtest Escape Demo", "id": "445"}]
    # Humble 给测试键：页内标题带 Playtest，商店侧无该条目 → 剥词对准本体
    assert hb._pick_appid("Eldtest Escape Playtest", hits) == 444


def test_pick_appid_prefix_subtitle_layer():
    """0.3.2 前缀层：HB 惯用简称对准商店全称副标题（真实案例 Disco Elysium
    → Disco Elysium - The Final Cut）；模糊层只认主游戏，原声/DLC 出局。"""
    hits = [
        {"name": "Disco Elysium - The Final Cut", "id": "546200", "type": "game"},
        {"name": "Disco Elysium Complete Soundtrack", "id": "1", "type": "soundtrack"},
    ]
    assert hb._pick_appid("Disco Elysium", hits) == 546200
    # 互为前缀方向也成立：HB 侧带全称、商店简称（少见但同源）
    hits2 = [{"name": "Shogun Showdown", "id": "3", "type": "game"}]
    assert hb._pick_appid("Shogun Showdown Samurai Edition", hits2) == 3
    # 前缀必须词边界（后接空格）：disco 不得撞 discover...
    hits3 = [{"name": "Discoverflow", "id": "9", "type": "game"}]
    assert hb._pick_appid("Disco", hits3) is None


def test_pick_appid_similarity_floor():
    """0.3.2 相似层：冠词/标点差异 ≥0.8 才认（Juggler's Tale 案例），
    阈值下宁缺勿猜；DLC 不参与模糊。"""
    hits = [{"name": "A Juggler's Tale", "id": "1207170", "type": "game"}]
    assert hb._pick_appid("Juggler's Tale", hits) == 1207170
    hits2 = [{"name": "Totally Different Game", "id": "9", "type": "game"}]
    assert hb._pick_appid("Disco Elysium", hits2) is None
    hits3 = [{"name": "Big Paintcast 2 All Stuff DLC", "id": "77", "type": "dlc"}]
    assert hb._pick_appid("Big Paintcast 2", hits3) is None


def test_month_page_url_from_machine_name():
    assert hb._month_page_url("september_2026_choice") == \
        "https://www.humblebundle.com/membership/September-2026"
    assert hb._month_page_url("july_2026_choice") == \
        "https://www.humblebundle.com/membership/July-2026"
    # 异形/缺失 machineName → 退回订阅主页（当月包同页可达）
    assert hb._month_page_url("whatever") == hb._MEMBERSHIP_URL
    assert hb._month_page_url(None) == hb._MEMBERSHIP_URL


def test_past_month_specs_includes_current_month():
    """0.2.1：窗口含当月（当月任务错过定点时历史链兜底），往前数 months_back 期。"""
    from app.crawler.utils import get_beijing_time_obj

    now = get_beijing_time_obj()
    specs = hb._past_month_specs(6)
    assert len(specs) == 6
    assert specs[0]["machine"] == f"{_MONTH_NAMES_EN[now.month].lower()}_{now.year}"
    # 窗口边界：第 6 条 = 5 个月前
    total = now.year * 12 + now.month - 1 - 5
    assert specs[5]["machine"].endswith(f"_{total // 12}")
    # 全部走 membership 往期页 URL（含当月）
    assert all(s["url"].startswith("https://www.humblebundle.com/membership/") for s in specs)


_MONTH_NAMES_EN = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


@pytest.mark.asyncio
async def test_history_months_back_pref_clamp(monkeypatch):
    """抓取窗口偏好：默认 6 期；越界钳到 [1,24]（上限 2 年）；坏值回默认。"""
    from app.domains.settings.service import set_value

    monkeypatch.setattr(hb, "_HB_HISTORY_PREF_KEY", "hb_history_months_back_test")
    assert await hb.get_history_months_back() == 6
    await set_value("hb_history_months_back_test", 12)
    assert await hb.get_history_months_back() == 12
    await set_value("hb_history_months_back_test", 99)
    assert await hb.get_history_months_back() == 24
    await set_value("hb_history_months_back_test", "abc")
    assert await hb.get_history_months_back() == 6
    # 显式窗口写进 start 即存偏好（钳到上限），并透传给刷新任务
    import asyncio

    captured: dict = {}
    async def fake_refresh(months_back=None):
        captured["got"] = months_back
        return {"ok": True}
    monkeypatch.setattr(hb, "refresh_hb_history", fake_refresh)
    await hb.start_hb_history_refresh(40)
    await asyncio.sleep(0)  # 放行 create_task 排的一拍
    assert captured.get("got") == 24
    assert await hb.get_history_months_back() == 24


@pytest.mark.asyncio
async def test_offers_payload_and_empty_month(monkeypatch):
    """展示链：有游标 + 标记行 → ok 载荷（URL 推导/国区价/评价排序）；
    游标指向查无标记行的月份 → ok=False 空态（2019 年在 HB 档案范围外，
    真实库不会有该标签，断言与库内其他数据无关）。"""
    from app.domains.settings.service import set_value

    monkeypatch.setattr(hb, "_HB_STATE_KEY", TEST_STATE_KEY)
    await set_value(TEST_STATE_KEY, {"machineName": MACHINE, "productName": PRODUCT})

    async with get_session_factory()() as session:
        existing = await session.get(Game, APP_EXISTING)
        existing.hb_data = f"{OLD_LABEL}, {LABEL}"
        existing.review_count = 500
        # 先清价格行残留（前次运行中断可能已落库；真实库有同 appid 行同理）
        await session.execute(
            delete(GameCurrentPrice).where(
                GameCurrentPrice.appid.in_([APP_NEW, APP_EXISTING])
            )
        )
        session.add(
            Game(appid=APP_NEW, name="Frosttest 2", is_hb=True,
                 hb_data=LABEL, created_at=datetime_now())
        )
        session.add(GameCurrentPrice(
            appid=APP_NEW, region_code="CN", currency="CNY",
            price=14900, original_price=22900, discount_percent=35,
            price_status="ok",
        ))
        session.add(GameCurrentPrice(
            appid=APP_EXISTING, region_code="CN", currency="CNY",
            price=9900, original_price=9900, discount_percent=0,
            price_status="ok",
        ))
        await session.commit()

    res = await hb.hb_choice_offers()
    assert res["ok"] is True
    assert res["label"] == LABEL
    assert res["monthUrl"] == "https://www.humblebundle.com/membership/Septtest-2026"
    assert res["skipUrl"].endswith("/user/skip-month")
    by_id = {g["appid"]: g for g in res["games"]}
    assert by_id[APP_NEW]["priceFen"] == 14900
    assert by_id[APP_NEW]["discount"] == 35
    assert by_id[APP_EXISTING]["name"] == "Keytest"
    # 评价量多的在前（库内其余标记行可能混入，只断言合成行相对序）
    ids = [g["appid"] for g in res["games"]]
    assert ids.index(APP_EXISTING) < ids.index(APP_NEW)

    await set_value(TEST_STATE_KEY, {"machineName": "september_2019_choice"})
    empty = await hb.hb_choice_offers()
    assert empty["ok"] is False


# ─── 链路（真实落库 + 合成数据）──────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def _seed():
    await init_db()
    async with get_session_factory()() as session:
        session.add(
            Game(
                appid=APP_EXISTING, name="Keytest",
                is_hb=True, hb_data=OLD_LABEL,
                created_at=datetime_now(),
            )
        )
        await session.commit()
    yield
    async with get_session_factory()() as session:
        await session.execute(
            delete(Game).where(
                Game.appid.in_([APP_NEW, APP_EXISTING, APP_UNRESOLVED])
            )
        )
        await session.execute(
            delete(GameCurrentPrice).where(
                GameCurrentPrice.appid.in_([APP_NEW, APP_EXISTING, APP_UNRESOLVED])
            )
        )
        await session.execute(
            delete(AppSetting).where(AppSetting.key == TEST_STATE_KEY)
        )
        await session.execute(
            delete(AppSetting).where(AppSetting.key == HISTORY_STATE_KEY)
        )
        await session.commit()

def datetime_now():
    from app.crawler.utils import get_beijing_time_obj

    return get_beijing_time_obj().replace(tzinfo=None)


class _NoSleep:
    """asyncio.sleep 打桩（链路内 storesearch 礼貌间隔）。"""

    @staticmethod
    async def sleep(_: float) -> None:
        return None


@pytest.mark.asyncio
async def test_refresh_partial_unresolved_keeps_retrying(monkeypatch):
    """有未解析条目 → 打标生效但不记账（次日重试），重跑幂等不重复续写。"""
    monkeypatch.setattr(hb, "_HB_STATE_KEY", TEST_STATE_KEY)
    monkeypatch.setattr(hb, "asyncio", _NoSleep)

    async def fake_fetch(session, proxy):
        return _membership_html()

    async def fake_resolve(session, title, proxy):
        return _RESOLVE_MAP.get(title)  # Missing Game → None

    monkeypatch.setattr(hb, "_fetch_membership_page", fake_fetch)
    monkeypatch.setattr(hb, "_resolve_appid", fake_resolve)

    first = await hb.refresh_hb_choice()
    assert first["ok"] is True
    assert [m["appid"] for m in first["marked"]] == [APP_NEW, APP_EXISTING]
    assert first["unresolved"] == ["Missing Game"]
    assert first["recorded"] is False

    async with get_session_factory()() as session:
        assert await session.get(AppSetting, TEST_STATE_KEY) is None

    async with get_session_factory()() as session:
        new_row = await session.get(Game, APP_NEW)
        assert new_row is not None and new_row.is_hb is True
        assert new_row.hb_data == LABEL
        assert new_row.updated_at is None  # 占位行进回补池
        assert new_row.name == "Frosttest 2"

        existing = await session.get(Game, APP_EXISTING)
        assert existing.hb_data == f"{OLD_LABEL}, {LABEL}"
        assert existing.is_hb is True

    # 未记账 → 再次运行不跳过；打标幂等（hb_data 不重复续写）
    second = await hb.refresh_hb_choice()
    assert second["skipped"] is False
    async with get_session_factory()() as session:
        existing = await session.get(Game, APP_EXISTING)
        assert existing.hb_data == f"{OLD_LABEL}, {LABEL}"


@pytest.mark.asyncio
async def test_refresh_records_state_and_skips_when_resolved(monkeypatch):
    """全量解析结案 → 记账，machine_name 未变时幂等跳过。"""
    monkeypatch.setattr(hb, "_HB_STATE_KEY", TEST_STATE_KEY)
    monkeypatch.setattr(hb, "asyncio", _NoSleep)

    async def fake_fetch(session, proxy):
        return _membership_html()

    async def fake_resolve(session, title, proxy):
        if title == "Missing Game":
            return APP_UNRESOLVED
        return _RESOLVE_MAP[title]

    monkeypatch.setattr(hb, "_fetch_membership_page", fake_fetch)
    monkeypatch.setattr(hb, "_resolve_appid", fake_resolve)

    first = await hb.refresh_hb_choice()
    assert first["recorded"] is True
    assert first["unresolved"] == []

    async with get_session_factory()() as session:
        state = await session.get(AppSetting, TEST_STATE_KEY)
        assert state is not None
        assert state.value_json["machineName"] == MACHINE
        assert state.value_json["marked"] == 3

    second = await hb.refresh_hb_choice()
    assert second["skipped"] is True
    assert second["lastMarked"] == 3

    # 幂等跳过：二次运行不再触达条目，hb_data 保持单月标记不重复续写
    async with get_session_factory()() as session:
        row = await session.get(Game, APP_UNRESOLVED)
        assert row is not None and row.hb_data == LABEL


@pytest.mark.asyncio
async def test_unpriced_hb_appids_filters_priced():
    """候选查询两通道判据：无任何价格行的标记行进候选；已有价格行
    （missing 状态）的归补抓账本不进（合成 appid 断言，容忍库内真实行）。"""
    async with get_session_factory()() as session:
        # 合成行重建（前序用例 teardown 会清掉）+ 清价格残留
        session.add(Game(appid=APP_NEW, name="Frosttest 2", is_hb=True,
                         hb_data=LABEL, created_at=datetime_now()))
        await session.execute(
            delete(GameCurrentPrice).where(
                GameCurrentPrice.appid.in_([APP_NEW, APP_EXISTING])
            )
        )
        # APP_EXISTING 留一条 missing 状态价格行：归补抓账本，不进候选
        session.add(GameCurrentPrice(
            appid=APP_EXISTING, region_code="CN", currency="CNY",
            price=None, price_status="missing",
        ))
        await session.commit()

    appids = await hb.unpriced_hb_appids()
    assert APP_NEW in appids
    assert APP_EXISTING not in appids


@pytest.mark.asyncio
async def test_backfill_hb_prices_trigger(monkeypatch):
    """触发行为：候选为空不发起爬取；候选非空以 appids spec 进
    run_sequential（kind=hb_backfill），返回任务 id。"""
    from app.domains.crawl import service as crawl_service

    calls: list[list[dict]] = []

    async def fake_run_sequential(specs, **_):
        calls.append(specs)
        return [{"id": 7}]

    monkeypatch.setattr(crawl_service, "run_sequential", fake_run_sequential)

    async def fake_unpriced(limit=60):
        return [APP_NEW, APP_EXISTING]

    monkeypatch.setattr(hb, "unpriced_hb_appids", fake_unpriced)
    res = await hb.backfill_hb_prices()
    assert res["started"] is True and res["jobId"] == 7
    assert calls[0][0]["kind"] == "hb_backfill"
    assert calls[0][0]["appids"] == [APP_NEW, APP_EXISTING]

    async def fake_unpriced_empty(limit=60):
        return []

    monkeypatch.setattr(hb, "unpriced_hb_appids", fake_unpriced_empty)
    res2 = await hb.backfill_hb_prices()
    assert res2["pending"] == 0 and res2["started"] is False
    assert len(calls) == 1


# ─── plus.3：多月解析 / 导入合并 / 往期补抓 / 进包记录 ─────────────


HISTORY_STATE_KEY = "hb_history_months_test"


def test_fmt_hb_data_multi_month():
    """多月源值全量解析（此前只取首个匹配，其余月份丢失）。"""
    raw = "Humble Choice (Sep 2025), Humble Choice (Mar 2026), Humble Choice (Sep 2025)"
    assert hb._fmt_hb_data(raw) == "HB慈善包25年9月包, HB慈善包26年3月包"
    assert hb._fmt_hb_data("Humble Choice (Aug 2026)") == "HB慈善包26年8月包"
    assert hb._fmt_hb_data("") == "HB慈善包"


def test_merge_hb_data_union_and_generic_dedup():
    # 已有月份在前、导入补在后、跨源去重
    assert hb._merge_hb_data(
        "HB慈善包25年9月包", "HB慈善包26年3月包, HB慈善包25年9月包"
    ) == "HB慈善包25年9月包, HB慈善包26年3月包"
    # 有了带月份的标签，无月份的通用标记即冗余
    assert hb._merge_hb_data("HB慈善包", "HB慈善包26年1月包") == "HB慈善包26年1月包"
    # 只剩通用标记时保留（宁标通用形态不错过打标）
    assert hb._merge_hb_data("HB慈善包", "HB慈善包") == "HB慈善包"


@pytest.mark.asyncio
async def test_import_hb_merges_instead_of_overwrite(tmp_path):
    """外部史低库导入改合并语义：库里已有月份（当月包/历史补抓写的）不被冲掉。"""
    src = tmp_path / "steam_historical_lows.db"
    con = sqlite3.connect(str(src))
    con.execute("CREATE TABLE game_lows (appid INTEGER, humble_choice TEXT)")
    con.execute(
        "INSERT INTO game_lows VALUES (?, ?)",
        (APP_EXISTING, "Humble Choice (Dec 2025), Humble Choice (Jan 2026)"),
    )
    con.commit()
    con.close()

    res = await hb.import_hb(str(tmp_path))
    assert res["ok"] is True and res["updated"] >= 1
    async with get_session_factory()() as session:
        row = await session.get(Game, APP_EXISTING)
        assert row.hb_data == f"{OLD_LABEL}, HB慈善包25年12月包, HB慈善包26年1月包"


def _past_month_html() -> str:
    """合成往期月包页：只有条目块，无 activeContentMachineName 等当月字段。"""
    items = {
        "frosttest2": _item("Frosttest 2", ["steam"]),
        "keytest": _item("Keytest", ["steam"]),
        "missinggame": _item("Missing Game", ["steam"]),
    }
    blocks = [
        f'"{name}": {json.dumps(obj, ensure_ascii=False)}' for name, obj in items.items()
    ]
    blob = html.escape(", ".join(blocks))
    return f"<html><div data-json=\"{blob}\"></div></html>"


@pytest.mark.asyncio
async def test_refresh_hb_history_marks_and_records(monkeypatch):
    """往期补抓：月份从 URL 推导打标；全量解析才记账；记账后幂等跳过。"""
    monkeypatch.setattr(hb, "_HB_HISTORY_KEY", HISTORY_STATE_KEY)
    monkeypatch.setattr(hb, "asyncio", _NoSleep)

    async def fake_fetch(session, proxy, url):
        return _past_month_html()

    async def fake_resolve(session, title, proxy):
        return APP_UNRESOLVED if title == "Missing Game" else _RESOLVE_MAP[title]

    monkeypatch.setattr(hb, "_fetch_past_month_page", fake_fetch)
    monkeypatch.setattr(hb, "_resolve_appid", fake_resolve)

    specs = hb._past_month_specs(1)
    res = await hb.refresh_hb_history(1)
    assert res["ok"] is True
    assert res["months"][0]["machine"] == specs[0]["machine"]
    assert res["months"][0]["marked"] == 3
    assert res["months"][0].get("unresolved") is None  # 全解析 → 记账

    second = await hb.refresh_hb_history(1)
    assert second["months"][0]["skipped"] is True

    async with get_session_factory()() as session:
        row = await session.get(Game, APP_NEW)
        assert row is not None and row.is_hb is True
        assert row.hb_data == specs[0]["label"]


@pytest.mark.asyncio
async def test_hb_history_months_and_month_items():
    """进包记录：月份清单聚合 + 指定期条目（/games 同构）。"""
    async with get_session_factory()() as session:
        session.add(
            Game(appid=APP_NEW, name="Frosttest 2", is_hb=True,
                 hb_data=f"{LABEL}, {OLD_LABEL}", created_at=datetime_now())
        )
        await session.commit()

    res = await hb.hb_history()
    labels = {m["label"] for m in res["months"]}
    assert LABEL in labels and OLD_LABEL in labels
    counts = {m["label"]: m["count"] for m in res["months"]}
    assert counts[LABEL] >= 1 and counts[OLD_LABEL] >= 1

    per = await hb.hb_history(LABEL)
    assert per["month"] == LABEL and per["total"] >= 1
    assert APP_NEW in {g["appid"] for g in per["items"]}
