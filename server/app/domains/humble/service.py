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
# 详情条目锚点：platforms_and_oses 前瞻（raw_decode 要从 { 起解）
_PLAT_RE = re.compile(r'"platforms_and_oses"\s*:\s*(?=\{)')
_NAME_RE = re.compile(r'"image_text"\s*:\s*"([^"]+)"')
# display 条目对象的机器名锚点：条目对象以 "machine_name": "<m>" 开头，
# 两锚之间切段可无窗重叠地把 platforms/image_text 归属到正确条目
_MACHINE_RE = re.compile(r'"machine_name":\s*"([^"]+)"')
# 档位数据（0.2.2）：每档价格与每档新增内容的机名清单
_TIER_PRICING_RE = re.compile(r'"tier_pricing_data"\s*:\s*(?=\{)')
_TIER_DISPLAY_RE = re.compile(r'"tier_display_data"\s*:\s*(?=\{)')
_PRICE_RE = re.compile(r'"preset_prices"\s*:\s*(\[)')
_ISO_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})')
# 慈善/通用角标条目不是游戏：机名或标题命中即剔
_NON_GAME_HINTS = ("charity", "child's play", "pay what you want", "coupon")
_SEGMENT_MAX = 9000

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


def _unesc(s: str) -> str:
    """还原页面内嵌 JSON 的转义序列（0.3.2）。

    详情页条目按 machine_name 锚切段、image_text 用正则取原文——不经过
    JSON 解码，`A Juggler\\u0027s Tale` 的 \\u0027（撇号）会原样入库：
    既脏了显示名，也让 storesearch 查询词被转义串拆碎永远关联不上。
    走一次 json 字符串解码；含裸引号等畸形形态解码失败时原样返回。"""
    if "\\" not in s:
        return s
    try:
        out = json.loads(f'"{s}"')
        return out if isinstance(out, str) else s
    except ValueError:
        return s


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


def _parse_game_items(html_text: str, bundle_name: str) -> dict[str, str]:
    """详情页 → {machine_name: title}（仅 Steam 游戏条目）。

    条目对象以 `"machine_name": "<m>"` 开头（availability/soundtrack 等长
    字段在后），用相邻 machine_name 锚切段——段内找 platforms_and_oses 与
    image_text，归属天然无窗重叠歧义（旧版「anchor 前 1500 字符回看」在
    条目密集处会串到上一条目）。慈善/soundtrack 等非游戏条目段没有
    platforms_and_oses，或平台里没有 steam，直接落选。"""
    lowered = (bundle_name or "").lower()
    anchors = [(m.start(), m.group(1)) for m in _MACHINE_RE.finditer(html_text)]
    items: dict[str, str] = {}
    for i, (pos, machine) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else min(len(html_text), pos + _SEGMENT_MAX)
        end = min(end, pos + _SEGMENT_MAX)
        seg = html_text[pos:end]
        pm = _PLAT_RE.search(seg)
        if pm is None:
            continue
        obj = _decode_at(seg, pm.end(), "{", "}")
        if not isinstance(obj, dict):
            continue
        game = obj.get("game") or obj.get("software") or {}
        if "steam" not in game:
            continue
        nm = _NAME_RE.search(seg)
        title = _unesc((nm.group(1) if nm else "").strip())
        low = title.lower()
        if not title or low == lowered:
            continue
        if any(k in low for k in _NON_GAME_HINTS) or any(k in machine.lower() for k in _NON_GAME_HINTS):
            continue
        items.setdefault(machine, title)
    return items


def _parse_tiers(html_text: str, machine_titles: dict[str, str]) -> list[dict]:
    """档位数据 → [{id, price_cny_fen, header, titles}]（每档**本档新增**）。

    tier_pricing_data：每档 CNY 价 + is_initial_tier/is_free/should_be_included；
    tier_display_data：每档页内文案 + tier_item_machine_names（本档新增机名）。
    档位是累进售卖：购买价 N 拿到 ≤N 全部档内容——本函数只给「新增」，
    累计展开由 API 层做（显示语义）。伪档（less_than_initial）、免费档、
    无 CNY 价的档剔除；机名映射不到游戏时保留占位标题（前端按未解析展示）。"""
    pm = _TIER_PRICING_RE.search(html_text)
    if pm is None:
        return []
    pricing = _decode_at(html_text, pm.end(), "{", "}")
    if not isinstance(pricing, dict):
        return []
    dm = _TIER_DISPLAY_RE.search(html_text)
    display = _decode_at(html_text, dm.end(), "{", "}") if dm else {}
    if not isinstance(display, dict):
        display = {}
    tiers: list[dict] = []
    for tid, pdata in pricing.items():
        if not isinstance(pdata, dict):
            continue
        if pdata.get("is_free") or not pdata.get("should_be_included_in_tier_list", True):
            continue
        price = _cny_to_fen(pdata.get("price|money"))
        if not price:
            continue
        d = display.get(tid) if isinstance(display.get(tid), dict) else {}
        machines = d.get("tier_item_machine_names") or []
        titles = [
            machine_titles.get(str(m), "") or str(m).replace("_", " ").title()
            for m in machines if isinstance(m, str) and m
        ]
        tiers.append({
            "id": tid,
            "price_cny_fen": price,
            "header": str(d.get("header") or ""),
            "titles": titles,
            "is_initial": bool(pdata.get("is_initial_tier")),
        })
    tiers.sort(key=lambda t: (t["price_cny_fen"], not t["is_initial"], t["id"]))
    return tiers


def parse_bundle_detail(html_text: str, bundle_name: str) -> dict:
    """详情页 → {price_cny_fen, titles, tiers}。

    价格：preset_prices 最低档（PWYW 包起步价 = 用户口中的「捆绑包价格」）。
    条目：全部 Steam 游戏标题（0.2.2 起 = 各档新增的并集，由 machine_name
    切段判定的游戏条目）。档位：见 _parse_tiers。"""
    price_fen: int | None = None
    pm = _PRICE_RE.search(html_text)
    if pm:
        arr = _decode_at(html_text, pm.end() - 1, "[", "]")
        if isinstance(arr, list):
            amounts = [_cny_to_fen(x.get("price|money")) for x in arr if isinstance(x, dict)]
            amounts = [a for a in amounts if a is not None and a > 0]
            if amounts:
                price_fen = min(amounts)

    machine_titles = _parse_game_items(html_text, bundle_name)
    titles = list(dict.fromkeys(machine_titles.values()))
    tiers = _parse_tiers(html_text, machine_titles)
    return {"price_cny_fen": price_fen, "titles": titles, "tiers": tiers}


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
                select(
                    HumbleBundleGame.title, HumbleBundleGame.appid,
                    HumbleBundleGame.resolve_attempts,
                ).where(HumbleBundleGame.slug == bundle.slug)
            )
        ).all()
    resolved_known = {t for t, a, _n in rows if a is not None}
    pending_known = {t for t, a, _n in rows if a is None}
    # 只解「新面孔 + 未放弃的欠账」；已有关联跳过（storesearch 省额度）。
    # 尝试 ≥3 轮的欠账不再重试（0.3.2 非 Steam 标注）：GOG 独占/商店下架
    # 之类永远解不出，空转每轮白烧一次查询；抽屉如实标「未找到」。
    exhausted = {t for t, a, n in rows if a is None and int(n or 0) >= RESOLVE_MAX_ATTEMPTS}
    to_resolve = [t for t in titles if t not in resolved_known and t not in exhausted]

    resolved: dict[str, int] = {}
    unresolved: list[str] = []
    for title in to_resolve:
        appid = await metadata_service._resolve_appid(session, title, proxy)
        if appid is None:
            unresolved.append(title)
        else:
            resolved[title] = appid
        await asyncio.sleep(0.3)

    # 解析成功但目录无行的 appid：只收集回传（0.2.2 收录链解耦——刷新末尾
    # 批量登记首爬，独立 ingest_scan 每日续跑；不在每包循环里各发任务）
    need_ingest: list[int] = []
    if resolved:
        async with get_session_factory()() as s:
            have = {
                r[0] for r in await s.execute(
                    select(Game.appid).where(Game.appid.in_(list(resolved.values())))
                )
            }
        need_ingest = sorted({a for a in resolved.values() if a not in have})

    now = get_beijing_time_obj().replace(tzinfo=None)
    async with write_gate(WritePriority.BACKGROUND, label="humble_sync"), get_session_factory()() as s:
        if detail["price_cny_fen"] is not None:
            await s.execute(
                update(HumbleBundle).where(HumbleBundle.slug == bundle.slug)
                .values(price_cny_fen=detail["price_cny_fen"])
            )
        if detail.get("tiers"):
            # 档位有结果才覆盖：页面结构变动解析为空时保留旧档不清空
            await s.execute(
                update(HumbleBundle).where(HumbleBundle.slug == bundle.slug)
                .values(tiers_json=json.dumps(detail["tiers"], ensure_ascii=False))
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
                # 未解析也落行（appid 留空）：外层计数 = 全部条目，欠账下轮重试；
                # 本轮已尝试过一次失败的直接记 1（0.3.2 尝试记账）
                s.add(HumbleBundleGame(
                    slug=bundle.slug, appid=None, title=title,
                    resolve_attempts=1 if title in unresolved else 0,
                ))
        # 欠账尝试记账（0.3.2）：本轮解析失败且已有行 → attempts+1；
        # 达到 RESOLVE_MAX_ATTEMPTS 后 to_resolve 不再拾取（above 过滤）
        for title in unresolved:
            if title in existing:
                await s.execute(
                    update(HumbleBundleGame)
                    .where(
                        HumbleBundleGame.slug == bundle.slug,
                        HumbleBundleGame.title == title,
                    )
                    .values(resolve_attempts=HumbleBundleGame.resolve_attempts + 1)
                )
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

    详情抓取范围：新包（updated_at 为空）、超 24h 的在售包、**档位数据缺失的
    在售包**（tiers_json 为空——0.2.2 前入库的存量包或 HB 页面结构变动期抓的
    空结果，不补抓永远等不到下一轮 24h 阈值外的机会）、**还有未放弃欠账
    条目的包**（0.3.2：解析修复/模糊升级后老数据要吃到红利——档位已齐的包
    若不再重抓，Disco Elysium 这类欠账会永远挂着）；下架包不重抓。"""
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
                # 还有未放弃欠账条目的包也重抓（0.3.2）：解析/模糊规则升级后
                # 老数据要吃到红利；attempts 到顶的「未找到」行不再拖着重抓
                has_pending = (
                    select(HumbleBundleGame.id)
                    .where(
                        HumbleBundleGame.slug == HumbleBundle.slug,
                        HumbleBundleGame.appid.is_(None),
                        HumbleBundleGame.resolve_attempts < RESOLVE_MAX_ATTEMPTS,
                    )
                    .exists()
                )
                stale = (
                    await s.execute(
                        select(HumbleBundle).where(
                            HumbleBundle.on_sale.is_(True),
                            or_(
                                HumbleBundle.updated_at.is_(None),
                                HumbleBundle.updated_at < cutoff,
                                # 档位数据缺失的在售包随时补抓（不等 24h 阈值）：
                                # 存量包入库早于档位解析上线，HB 模板切换期抓回的
                                # 空结果也被「有结果才覆盖」保护性保留为 NULL
                                HumbleBundle.tiers_json.is_(None),
                                has_pending,
                            ),
                        )
                    )
                ).scalars().all()
            done: list[dict] = []
            for bundle in stale[:24]:
                done.append(await _sync_bundle_detail(session, proxy, bundle, mark_updated=True))
        unresolved_total = sum(len(d.get("unresolved") or []) for d in done)
        # 刷新收敛后跑一轮收录扫描（0.2.2）：本轮新解析出的未入目录 appid
        # 连同历史欠账一起批量登记首爬——收录链独立于详情抓取，抓取失败时
        # 也能单独续跑（调度器另有每日一拍）
        ingest = await scan_missing_ingest()
        logger.info("[humble-bundles] 刷新完成：列表 %d 包，详情 %d 包（未解析 %d，收录 %d）",
                    len(rows), len(done), unresolved_total, ingest.get("queued", 0))
        return {"ok": True, "listed": len(rows), "details": done,
                "unresolvedTotal": unresolved_total, "ingest": ingest}


async def start_refresh() -> dict:
    """后台启动一轮刷新（HTTP 立即返回；进度轮询 bundles 接口的 running）。"""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return {"ok": True, "started": False, "running": True}
    _refresh_task = asyncio.create_task(refresh_humble_bundles())
    return {"ok": True, "started": True, "running": True}


# 单批首爬上限 × 单轮批次上限：防极端候选打爆队列；一轮最多 2000 款，
# 余额（或爬虫被占）留待下拍——候选判定是 games 行存在性，天然续跑
_INGEST_BATCH = 200
_INGEST_MAX_BATCHES = 10

# storesearch 关联尝试上限（0.3.2 非 Steam 标注）：超过即放弃重试并在
# 抽屉标「未找到」——GOG 独占/商店无此条目类永远解不出，别每轮空烧额度
RESOLVE_MAX_ATTEMPTS = 3


async def _queue_ingest(appids: list[int], source: str) -> int:
    """批量登记一次性首爬（scope=appids，与月包补价同管线）。

    爬虫占用（start_job RuntimeError → run_sequential 跳过该 spec）或
    异常都表现为「本批零任务跑完」，返回 0 让调用方停止本轮循环；
    欠账由下拍候选重算自然续。"""
    ids = list(dict.fromkeys(appids))[:_INGEST_BATCH]
    if not ids:
        return 0
    try:
        from app.domains.crawl import service as crawl_service

        results = await crawl_service.run_sequential(
            [{"scope": "appids", "appids": ids, "kind": "humble_backfill"}]
        )
    except Exception:  # noqa: BLE001
        logger.info("[humble-ingest] %s 首爬登记跳过（%d 款留待下拍）", source, len(ids))
        return 0
    if not results:
        logger.info("[humble-ingest] %s 爬虫占用跳过本批（%d 款留待下拍）", source, len(ids))
        return 0
    logger.info("[humble-ingest] %s 登记首爬 %d 款", source, len(ids))
    return len(ids)


async def _ingest_candidates() -> list[int]:
    """未收录候选（在售包、appid 已解析、games 无行），NEW 包优先序：
    同一 appid 挂多包时取最新包的档期；包按 first_seen_at 降序（今日
    新扫出的包先入批），无档期信息垫底，同包内按 appid 升序保底确定性。"""
    from app.domains.games.models import Game

    async with get_session_factory()() as s:
        rows = (
            await s.execute(
                select(HumbleBundleGame.appid, HumbleBundle.first_seen_at)
                .join(HumbleBundle, HumbleBundle.slug == HumbleBundleGame.slug)
                .where(HumbleBundle.on_sale.is_(True), HumbleBundleGame.appid.is_not(None))
            )
        ).all()
        freshness: dict[int, object] = {}
        for a, seen in rows:
            a = int(a)
            cur = freshness.get(a)
            if seen is not None and (cur is None or seen > cur):  # type: ignore[operator]
                freshness[a] = seen
        appids = list(freshness)
        have: set[int] = set()
        if appids:
            have = {
                r[0] for r in await s.execute(
                    select(Game.appid).where(Game.appid.in_(appids))
                )
            }
    missing = [a for a in appids if a not in have]
    # 两步稳定排序=单键复合序：先按 appid 升序保底，再按包档期降序
    # （新包在前；无档期行视作最旧垫底，稳定保持组内 appid 升序）
    missing.sort()
    missing.sort(
        key=lambda a: freshness[a] if freshness[a] is not None else datetime.min,  # type: ignore[arg-type,return-value]
        reverse=True,
    )
    return missing


async def scan_missing_ingest() -> dict:
    """收录扫描（0.2.3 强化：appid 先行、轮内连续分批）：在售包内已解析
    appid、但 Steam 目录还没有行的游戏 → 按批登记首爬，直到候选清空、
    爬不动（占用/失败）或吃满单轮上限。

    与详情抓取解耦：刷新链尾跑一轮，调度器另有每日独立一拍，面板还可
    手动触发（ingest-now）。幂等账本 = games 行存在（首爬成功建行即
    退出候选；行存在但价格未出也不重复爬，价格轮自己管）。轮内以
    attempted 记账防同批死循环：本批爬失败（行没长出来）不再重试，
    留给下一拍。"""
    candidates = await _ingest_candidates()
    queued = 0
    batches = 0
    attempted: set[int] = set()
    while batches < _INGEST_MAX_BATCHES:
        pool = [a for a in candidates if a not in attempted]
        if not pool:
            break
        batch = pool[:_INGEST_BATCH]
        got = await _queue_ingest(batch, "scan")
        if got == 0:
            break
        attempted.update(batch)
        queued += got
        batches += 1
    return {
        "ok": True,
        "candidates": len(candidates),  # 本轮扫描起点的欠账款数
        "queued": queued,
        "batches": batches,
        "remaining": len(candidates) - queued,  # 占用截断/轮上限未吃满的余额
    }


# 手动收录轮（ingest-now）：同一时刻只允许一轮（与 _refresh_task 同款防重）
_ingest_task: asyncio.Task | None = None


async def start_ingest_now() -> dict:
    """后台立即跑一轮收录扫描（轮内连续分批；前端经 bundles 的
    ingestRunning 轮询收尾）。已在跑则不重复启动。"""
    global _ingest_task
    if _ingest_task is not None and not _ingest_task.done():
        return {"ok": True, "started": False, "running": True}
    _ingest_task = asyncio.create_task(scan_missing_ingest())
    return {"ok": True, "started": True, "running": True}


async def list_bundles() -> dict:
    """在售包列表（前端中心面板数据源；纯本地读，零外网）。"""
    from app.crawler.utils import get_beijing_time_obj

    running = _refresh_task is not None and not _refresh_task.done()
    ingest_running = _ingest_task is not None and not _ingest_task.done()
    # 目录收录欠账（appid 已解析、games 无行）：面板「收录中 N」汇总口径
    try:
        ingest_pending = len(await _ingest_candidates())
    except Exception:  # noqa: BLE001 —— 汇总数读失败不阻断列表
        ingest_pending = 0
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
    return {"running": running, "ingestRunning": ingest_running,
            "ingestPending": ingest_pending, "bundles": items}


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
                select(
                    HumbleBundleGame.appid, HumbleBundleGame.title,
                    HumbleBundleGame.resolve_attempts,
                ).where(HumbleBundleGame.slug == slug)
            )
        ).all()
        appids = [int(a) for a, _t, _n in rows if a is not None]
        # 有 appid 但 games 目录还没行的 = 首爬收录中
        have: set[int] = set()
        if appids:
            have = {
                r[0] for r in await s.execute(
                    select(Game.appid).where(Game.appid.in_(appids))
                )
            }
    pending = [
        {
            "appid": int(a) if a is not None else None, "title": title,
            # 三态（0.3.2）：解析放弃（尝试≥3 仍无果，大概率非 Steam 发行）
            # > 已解析待目录收录（ingesting）> 关联欠账重试中（resolving）
            "status": (
                "not_found"
                if a is None and int(n or 0) >= RESOLVE_MAX_ATTEMPTS
                else "ingesting" if a is not None and int(a) not in have
                else "resolving"
            ),
        }
        for a, title, n in rows
        if a is None or int(a) not in have
    ]
    # 档位（0.2.2 存本档新增；0.2.3 展开累进售卖语义）：每档 games =
    # 买那一档实际能拿到的全部游戏（跨档去重累计，本档新增带 isNew 标），
    # newGames/newCount 保留作「本档新增」小标。
    # 旧数据 tiers_json NULL → 空数组，前端回退平铺。
    title_appid: dict[str, int | None] = {}
    for a, title, _n in rows:
        title_appid.setdefault(title, int(a) if a is not None else None)
    tiers_out: list[dict] = []
    if bundle.tiers_json:
        try:
            raw_tiers = json.loads(bundle.tiers_json)
        except (ValueError, TypeError):
            raw_tiers = []
        seen: set[str] = set()
        acc: list[dict] = []  # 累进清单：截至当前档的全部去重条目
        for idx, t in enumerate(raw_tiers if isinstance(raw_tiers, list) else []):
            if not isinstance(t, dict):
                continue
            new_titles = [
                x for x in (t.get("titles") or [])
                if isinstance(x, str) and x not in seen and not seen.add(x)
            ]
            # unlockPriceCnyFen：首次出现的档位价 = 拿到这款游戏的最低解锁
            # 门槛（累进售卖下首现档即最低价），卡片角标免点击可辨价位
            news = [
                {"title": x, "appid": title_appid.get(x),
                 "unlockPriceCnyFen": t.get("price_cny_fen")}
                for x in new_titles
            ]
            acc.extend(news)
            cutoff = len(acc) - len(news)  # 尾段 = 本档新增
            tiers_out.append({
                "id": str(t.get("id") or f"t{idx}"),
                "priceCnyFen": t.get("price_cny_fen"),
                "isInitial": bool(t.get("is_initial")),
                "newGames": [{"title": g["title"], "appid": g["appid"]} for g in news],
                "newCount": len(news),
                "count": len(seen),  # 累计：买这档一共拿多少款
                "games": [
                    {**g, "isNew": i >= cutoff} for i, g in enumerate(acc)
                ],
            })
    payload: dict = {
        "slug": bundle.slug, "name": bundle.name, "image": bundle.image, "url": bundle.url,
        "priceCnyFen": bundle.price_cny_fen,
        "startAt": bundle.start_at.isoformat() if bundle.start_at else None,
        "endAt": bundle.end_at.isoformat() if bundle.end_at else None,
        "gameCount": len(rows),
        "items": [], "total": 0, "pending": pending, "tiers": tiers_out,
    }
    if appids:
        cards = await games_service.list_items_by_appids(appids)
        payload["items"] = cards["items"]
        payload["total"] = cards["total"]
    return payload
