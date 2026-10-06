"""HB 捆绑包（Humble Bundle 商店包）服务：列表 / 详情抓取 + 包内游戏关联。

数据链路（2026-10 探测定案，官方 JSON API 已全部下线，页面内嵌数据是唯一稳定来源）：
- 列表：GET https://zh.humblebundle.com/games —— 页内嵌 `"products": [` 数组
  （20 条，raw_decode 即得）。只留 type=bundle 且 product_url 以 /games/ 开头
  的游戏捆绑包（software 类资产包与 Steam 比价无关，不入库）。
- 详情：GET 包页（域常量拼接，路径必须是站内相对链接）。价格取
  preset_prices 最低档（zh 站点直接输出 CNY），条目取带
  platforms_and_oses{game.steam} 的 display 对象的 image_text（游戏名）。
- appid 关联：复用 metadata 的 storesearch 三级匹配（_resolve_appid），
  首轮未解析的条目表内 appid 留空，下一轮自动补解。

抓取策略：每日 cron 一轮 + 启动追赶；列表 1 页 + 在售包详情（≤20 页，
条目间隔 0.3s 礼貌限速，与月包链同型）。包行只增改不删——下架包
on_sale=false 保留历史（进包记录语义同月包）。

安全约束：出网 URL 全部由**常量域**（_HUMBLE_BASE，https + host 硬校验）
拼接站内相对路径构成；页面里出现的绝对 URL 只取末段 slug，绝不让
页面内容直接决定请求目标 host。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

import aiohttp
from sqlalchemy import func, or_, select, update

from app.core.database import WritePriority, get_session_factory, write_gate
from app.domains.humble.models import HumbleBundle, HumbleBundleGame

logger = logging.getLogger(__name__)

_HUMBLE_BASE = "https://zh.humblebundle.com"
_GAMES_LIST_PATH = "/games"
# 出网白名单：scheme + host 双校验，页面内容不得扩展可达域
_ALLOWED_HOSTS = frozenset({"zh.humblebundle.com", "www.humblebundle.com"})

_PRODUCT_ARR_RE = re.compile(r'"products"\s*:\s*\[')
# 详情条目锚点：platforms_and_oses 前最近的 image_text 即该 display 对象的游戏名
# （前瞻保留 { 在串内：raw_decode 必须从 { 起解）
_PLAT_RE = re.compile(r'"platforms_and_oses"\s*:\s*(?=\{)')
_NAME_RE = re.compile(r'"image_text"\s*:\s*"([^"]+)"')
_PRICE_RE = re.compile(r'"preset_prices"\s*:\s*(\[)')
_ISO_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})')

_REFRESH_LOCK = asyncio.Lock()
_refresh_task: asyncio.Task | None = None


def _hb_headers() -> dict:
    from app.domains.metadata.service import _hb_headers as _impl

    return _impl()


async def _strategy_proxy() -> str | None:
    from app.domains.metadata.service import _strategy_proxy as _impl

    return await _impl()


def _safe_game_path(product_url: str) -> str | None:
    """页面给的商品链接 → 站内路径（仅 /games/<slug>）。

    绝对 URL 只允许白名单域；路径含协议/外域/跳段一律拒绝——请求目标
    永远由常量基址 + 已校验路径构成，不给页面内容开宿主选择权。"""
    raw = (product_url or "").strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in _ALLOWED_HOSTS:
            return None
        raw = parsed.path
    if not raw.startswith("/games/") or ".." in raw or "//" in raw:
        return None
    return raw


def _slug_of(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def _parse_iso(dt: str | None) -> datetime | None:
    if not dt:
        return None
    m = _ISO_RE.match(dt)
    if not m:
        return None
    try:
        return datetime(*[int(g) for g in m.groups()])
    except ValueError:
        return None


def _cny_to_fen(money: dict | None) -> int | None:
    """zh 站点价格输出 {"currency": "CNY", "amount": 33.53} → 人民币分。
    非 CNY 返回 None（宁可无价也不拿未折算的数当人民币价）。"""
    if not isinstance(money, dict) or money.get("currency") != "CNY":
        return None
    try:
        return int(round(float(money.get("amount") or 0) * 100))
    except (TypeError, ValueError):
        return None


def _decode_at(text: str, brace_pos: int, opener: str, closer: str):
    """从锚点位起 raw_decode 一个 JSON 容器（页面文本里条目含嵌套花括号，
    手切不可靠；月包解析链同款做法）。"""
    dec = json.JSONDecoder()
    try:
        obj, _ = dec.raw_decode(text, brace_pos)
        return obj if isinstance(obj, (dict, list)) else None
    except ValueError:
        return None


def parse_games_list(html_text: str) -> list[dict]:
    """商店列表页 → 游戏捆绑包条目（type=bundle 且站内 /games/ 路径）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _PRODUCT_ARR_RE.finditer(html_text):
        arr = _decode_at(html_text, m.end() - 1, "[", "]")
        if not isinstance(arr, list):
            continue
        for p in arr:
            if not isinstance(p, dict) or p.get("type") != "bundle":
                continue
            path = _safe_game_path(str(p.get("product_url") or ""))
            if not path:
                continue
            slug = _slug_of(path)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append({
                "slug": slug,
                "machine_name": str(p.get("machine_name") or ""),
                "name": str(p.get("tile_name") or p.get("machine_name") or slug),
                "image": str(p.get("tile_image") or p.get("high_res_tile_image") or ""),
                "start_at": _parse_iso(str(p.get("start_date|datetime") or "")),
                "end_at": _parse_iso(str(p.get("end_date|datetime") or "")),
            })
    return out


def parse_bundle_detail(html_text: str, bundle_name: str) -> dict:
    """详情页 → {price_cny_fen, titles}。

    价格：preset_prices 最低档（PWYW 包起步价 = 用户口中的「捆绑包价格」）。
    条目：带 platforms_and_oses.game.steam 的 display 对象的 image_text；
    剔除慈善条目与包名自身（同一名字可能出现在头图/角标位）。"""
    price_fen: int | None = None
    pm = _PRICE_RE.search(html_text)
    if pm:
        arr = _decode_at(html_text, pm.end() - 1, "[", "]")
        if isinstance(arr, list):
            amounts = [_cny_to_fen(x.get("price|money")) for x in arr if isinstance(x, dict)]
            amounts = [a for a in amounts if a is not None and a > 0]
            if amounts:
                price_fen = min(amounts)

    titles: list[str] = []
    lowered = (bundle_name or "").lower()
    for m in _PLAT_RE.finditer(html_text):
        obj = _decode_at(html_text, m.end(), "{", "}")
        if not isinstance(obj, dict):
            continue
        game = obj.get("game") or obj.get("software") or {}
        if "steam" not in game:
            continue
        seg = html_text[max(0, m.start() - 1500):m.start()]
        names = _NAME_RE.findall(seg)
        if not names:
            continue
        title = names[-1]
        if not title or title.lower() == lowered:
            continue
        if any(k in title.lower() for k in ("charity", "child's play", "pay what you want")):
            continue
        if title not in titles:
            titles.append(title)
    return {"price_cny_fen": price_fen, "titles": titles}

    titles: list[str] = []
    lowered = (bundle_name or "").lower()
    for m in _PLAT_RE.finditer(html_text):
        obj = _decode_at(html_text, m.end(), "{", "}")
    return {"price_cny_fen": price_fen, "titles": titles}


async def _fetch_page(session: aiohttp.ClientSession, proxy: str | None, url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None  # 防御纵深：调用点已保证，这里再闸一次
    try:
        async with session.get(
            url, headers=_hb_headers(), proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.text(errors="replace")
    except Exception:  # noqa: BLE001
        return None


async def _upsert_bundles(rows: list[dict]) -> int:
    """列表入库：新行插入，已有行刷 last_seen/on_sale；本轮未见的在售包置下架。"""
    from app.crawler.utils import get_beijing_time_obj

    now = get_beijing_time_obj().replace(tzinfo=None)
    slugs = [r["slug"] for r in rows]
    async with write_gate(WritePriority.BACKGROUND, label="humble_sync"), get_session_factory()() as session:
        for r in rows:
            existing = await session.get(HumbleBundle, r["slug"])
            if existing is None:
                session.add(HumbleBundle(
                    slug=r["slug"], machine_name=r["machine_name"], name=r["name"],
                    image=r["image"], url=f"{_HUMBLE_BASE}/games/{r['slug']}",
                    start_at=r["start_at"], end_at=r["end_at"],
                    on_sale=True, first_seen_at=now, last_seen_at=now,
                ))
            else:
                existing.name = r["name"] or existing.name
                existing.image = r["image"] or existing.image
                if r["end_at"]:
                    existing.end_at = r["end_at"]
                existing.last_seen_at = now
                existing.on_sale = True
        # 本轮未露面的包 = 已下架（保留历史行；列表只出在售包，一次抓全 ≤20）
        if slugs:
            await session.execute(
                update(HumbleBundle)
                .where(HumbleBundle.on_sale.is_(True), HumbleBundle.slug.notin_(slugs))
                .values(on_sale=False)
            )
        await session.commit()
    return len(slugs)


async def _sync_bundle_detail(
    session: aiohttp.ClientSession, proxy: str | None, bundle: HumbleBundle,
    mark_updated: bool,
) -> dict:
    """单包详情：价格 + 全部包内条目入库（0.2.1 全量语义）。

    - 条目全量落 humble_bundle_games：已解析的带 appid，未解析的 appid NULL
      留行——外层计数与抽屉可见条目从此一致（「外面说 14 点进去 6 个」的根修）。
    - 欠账重试：已有行 appid 为 NULL 的标题参与本轮解析，成功即回填。
    - 未入目录的 appid（games 表无行，比价无数据源）：单独触发一次性首爬
      （scope=appids，与 HB 月包补价同型），下一轮/刷新后卡片自然齐全。"""
    from app.crawler.utils import get_beijing_time_obj
    from app.domains.games.models import Game
    from app.domains.metadata import service as metadata_service

    page = await _fetch_page(session, proxy, bundle.url)
    if not page:
        return {"slug": bundle.slug, "ok": False}
    detail = parse_bundle_detail(page, bundle.name)
    titles: list[str] = detail["titles"]

    async with get_session_factory()() as s:
        rows = (
            await s.execute(
                select(HumbleBundleGame.title, HumbleBundleGame.appid).where(
                    HumbleBundleGame.slug == bundle.slug
                )
            )
        ).all()
    resolved_known = {t for t, a in rows if a is not None}
    pending_known = {t for t, a in rows if a is None}
    # 只解「新面孔 + 上轮欠账」；已有关联跳过（storesearch 省额度）
    to_resolve = [t for t in titles if t not in resolved_known]

    resolved: dict[str, int] = {}
    unresolved: list[str] = []
    for title in to_resolve:
        appid = await metadata_service._resolve_appid(session, title, proxy)
        if appid is None:
            unresolved.append(title)
        else:
            resolved[title] = appid
        await asyncio.sleep(0.3)

    # 解析成功但目录无行的 appid → 单独首爬补收录（与月包补价同款语义）
    need_ingest: list[int] = []
    if resolved:
        async with get_session_factory()() as s:
            have = {
                r[0] for r in await s.execute(
                    select(Game.appid).where(Game.appid.in_(list(resolved.values())))
                )
            }
        need_ingest = sorted({a for a in resolved.values() if a not in have})
        if need_ingest:
            try:
                from app.domains.crawl import service as crawl_service

                await crawl_service.run_sequential(
                    [{"scope": "appids", "appids": need_ingest, "kind": "humble_backfill"}]
                )
            except Exception:  # noqa: BLE001
                # 任务占用等登记失败不阻塞本轮入库——下轮刷新重试
                logger.info("[humble-bundles] %s 首爬登记跳过：%d 款", bundle.slug, len(need_ingest))

    now = get_beijing_time_obj().replace(tzinfo=None)
    async with write_gate(WritePriority.BACKGROUND, label="humble_sync"), get_session_factory()() as s:
        if detail["price_cny_fen"] is not None:
            await s.execute(
                update(HumbleBundle).where(HumbleBundle.slug == bundle.slug)
                .values(price_cny_fen=detail["price_cny_fen"])
            )
        if mark_updated:
            await s.execute(
                update(HumbleBundle).where(HumbleBundle.slug == bundle.slug)
                .values(updated_at=now)
            )
        existing = {
            t: a for t, a in await s.execute(
                select(HumbleBundleGame.title, HumbleBundleGame.appid).where(
                    HumbleBundleGame.slug == bundle.slug
                )
            )
        }
        for title in titles:
            if title in resolved:
                if title in existing:
                    if existing[title] is None:
                        await s.execute(
                            update(HumbleBundleGame)
                            .where(
                                HumbleBundleGame.slug == bundle.slug,
                                HumbleBundleGame.title == title,
                            )
                            .values(appid=resolved[title])
                        )
                else:
                    s.add(HumbleBundleGame(slug=bundle.slug, appid=resolved[title], title=title))
            elif title not in existing:
                # 未解析也落行（appid 留空）：外层计数 = 全部条目，欠账下轮重试
                s.add(HumbleBundleGame(slug=bundle.slug, appid=None, title=title))
        # 页面已消失的旧条目清理（仅删未解析欠账行；已有关联的保留历史）
        title_set = set(titles)
        stale_unresolved = [t for t in existing if t not in title_set and existing[t] is None]
        if stale_unresolved:
            for g in (
                await s.execute(
                    select(HumbleBundleGame).where(
                        HumbleBundleGame.slug == bundle.slug,
                        HumbleBundleGame.title.in_(stale_unresolved),
                    )
                )
            ).scalars():
                await s.delete(g)
        count_sq = (
            select(func.count())
            .select_from(HumbleBundleGame)
            .where(HumbleBundleGame.slug == bundle.slug)
            .scalar_subquery()
        )
        await s.execute(
            update(HumbleBundle)
            .where(HumbleBundle.slug == bundle.slug)
            .values(game_count=count_sq)
        )
        await s.commit()
    return {"slug": bundle.slug, "ok": True, "titles": len(titles),
            "resolved": len(resolved), "pendingRetry": len(pending_known),
            "ingestQueued": len(need_ingest), "unresolved": unresolved}


async def refresh_humble_bundles() -> dict:
    """列表 + 在售包详情全链路（每日调度与手动触发同入口，幂等）。

    详情抓取范围：新包（updated_at 为空）与超 24h 的在售包；下架包不重抓。"""
    from app.crawler.utils import get_beijing_time_obj

    if _REFRESH_LOCK.locked():
        return {"ok": False, "error": "已有 HB 捆绑包刷新在进行"}
    async with _REFRESH_LOCK:
        proxy = await _strategy_proxy()
        connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=60)
        async with aiohttp.ClientSession(connector=connector) as session:
            listing = await _fetch_page(session, proxy, _HUMBLE_BASE + _GAMES_LIST_PATH)
            if not listing:
                return {"ok": False, "error": "列表页抓取失败"}
            rows = parse_games_list(listing)
            if not rows:
                return {"ok": False, "error": "列表无游戏捆绑包"}
            await _upsert_bundles(rows)

            now = get_beijing_time_obj().replace(tzinfo=None)
            cutoff = datetime(now.year, now.month, now.day) - timedelta(hours=24)
            async with get_session_factory()() as s:
                stale = (
                    await s.execute(
                        select(HumbleBundle).where(
                            HumbleBundle.on_sale.is_(True),
                            or_(
                                HumbleBundle.updated_at.is_(None),
                                HumbleBundle.updated_at < cutoff,
                            ),
                        )
                    )
                ).scalars().all()
            done: list[dict] = []
            for bundle in stale[:24]:
                done.append(await _sync_bundle_detail(session, proxy, bundle, mark_updated=True))
        unresolved_total = sum(len(d.get("unresolved") or []) for d in done)
        logger.info("[humble-bundles] 刷新完成：列表 %d 包，详情 %d 包（未解析 %d）",
                    len(rows), len(done), unresolved_total)
        return {"ok": True, "listed": len(rows), "details": done,
                "unresolvedTotal": unresolved_total}


async def start_refresh() -> dict:
    """后台启动一轮刷新（HTTP 立即返回；进度轮询 bundles 接口的 running）。"""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return {"ok": True, "started": False, "running": True}
    _refresh_task = asyncio.create_task(refresh_humble_bundles())
    return {"ok": True, "started": True, "running": True}


async def list_bundles() -> dict:
    """在售包列表（前端中心面板数据源；纯本地读，零外网）。"""
    from app.crawler.utils import get_beijing_time_obj

    running = _refresh_task is not None and not _refresh_task.done()
    async with get_session_factory()() as s:
        rows = (
            await s.execute(
                select(HumbleBundle)
                .order_by(HumbleBundle.end_at.asc().nulls_last(), HumbleBundle.slug)
            )
        ).scalars().all()
    now = get_beijing_time_obj().replace(tzinfo=None)
    items = [
        {
            "slug": r.slug, "name": r.name, "image": r.image, "url": r.url,
            "priceCnyFen": r.price_cny_fen,
            "startAt": r.start_at.isoformat() if r.start_at else None,
            "endAt": r.end_at.isoformat() if r.end_at else None,
            "onSale": r.on_sale and (r.end_at is None or r.end_at >= now),
            "gameCount": r.game_count,
            # 本轮扫出的新包未读（NEW 徽章）；用户点开包 = 已读（mark_seen）
            "isNew": r.acked_at is None,
        }
        for r in rows
        if r.on_sale and (r.end_at is None or r.end_at >= now)
    ]
    return {"running": running, "bundles": items}


async def mark_seen(slug: str) -> bool:
    """点开包 = 已读：清 NEW 徽章（幂等，已有时刻不覆盖）。"""
    from app.crawler.utils import get_beijing_time_obj

    now = get_beijing_time_obj().replace(tzinfo=None)
    async with write_gate(WritePriority.BACKGROUND, label="humble_seen"), get_session_factory()() as s:
        bundle = await s.get(HumbleBundle, slug)
        if bundle is None:
            return False
        if bundle.acked_at is None:
            bundle.acked_at = now
            await s.commit()
    return True


async def bundle_detail(slug: str) -> dict | None:
    """单包详情：包信息 + 包内游戏。

    0.2.1 条目一致语义：items = 已收录游戏（/games 同款卡，点进详情比价）；
    pending = 尚无卡片的条目——未解析（resolving，storesearch 欠账下轮重试）
    与已解析但目录收录中（ingesting，首爬排队）。抽屉可见行数 = 全部条目，
    与外层 gameCount 对得上。"""
    from app.domains.games import service as games_service
    from app.domains.games.models import Game

    async with get_session_factory()() as s:
        bundle = await s.get(HumbleBundle, slug)
        if bundle is None:
            return None
        rows = (
            await s.execute(
                select(HumbleBundleGame.appid, HumbleBundleGame.title).where(
                    HumbleBundleGame.slug == slug
                )
            )
        ).all()
        appids = [int(a) for a, _ in rows if a is not None]
        # 有 appid 但 games 目录还没行的 = 首爬收录中
        have: set[int] = set()
        if appids:
            have = {
                r[0] for r in await s.execute(
                    select(Game.appid).where(Game.appid.in_(appids))
                )
            }
    pending = [
        {"appid": int(a) if a is not None else None, "title": title,
         "status": "ingesting" if a is not None and int(a) not in have else "resolving"}
        for a, title in rows
        if a is None or int(a) not in have
    ]
    payload: dict = {
        "slug": bundle.slug, "name": bundle.name, "image": bundle.image, "url": bundle.url,
        "priceCnyFen": bundle.price_cny_fen,
        "startAt": bundle.start_at.isoformat() if bundle.start_at else None,
        "endAt": bundle.end_at.isoformat() if bundle.end_at else None,
        "gameCount": len(rows),
        "items": [], "total": 0, "pending": pending,
    }
    if appids:
        cards = await games_service.list_items_by_appids(appids)
        payload["items"] = cards["items"]
        payload["total"] = cards["total"]
    return payload
