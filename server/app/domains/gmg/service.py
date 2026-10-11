"""GreenManGaming 捆绑包服务：列表 / 详情抓取 + 包内游戏统一检索关联。

数据链路（2026-10 探测定案，全 SSR HTML、无需登录/cookie）：
- 列表：GET https://www.greenmangaming.com/zh/bundles/（主站，CN 直连可达，
  **必须带 UA** 否则 406 空响应）——`.product-card` 卡片：子站链接（slug）、
  标题、封面、data-end-date、件数、价值/起价（CNY）。
- 详情：GET https://www.greenmangamingbundles.com/bundles/{slug}/（子站，
  **对中国出口软封锁：间歇 302/空响应，必须带重试**）——档位 radio 卡
  （`data-upgrade-tier-id` + `value="{id}:{price}"` + `bundle-type` 名称 +
  「N 款游戏」+ `.price`）与默认游戏网格（`.game-title`，最高档全量）。
- 分档归属：子站默认网格是**最高档全量**且顺序与档位无关；htmx 局部端点
  `switch_tier/?amount={id}:{price}` 以**最低档**请求时返回同一批游戏但按
  **档位升序**排列（实测 2 包验证：前缀切片与「N 款游戏」计数严丝合缝）——
  据此按累计数切出各档新增；切片校验失败（页面变动期）退化为单伪档
  （全量归最高一档，宁缺勿错）。
- appid：HTML 无 steam id，全部走统一检索引擎（metadata.resolver：storesearch
  + 店铺全文搜索两级源）；attempts 账本与 not_found 三态同 HB。

价格语义：页面价形如 `CN¥45.00` / `$12.99`——符号映射币种（CN¥/¥→CNY、
$→USD、€→EUR、£→GBP），×100 存分；多币种全存不折算。代理出口币种可能
与直连不同，实得什么存什么（展示层切换）。

安全约束：出网 URL 由常量域拼接（https + host 白名单 + slug 路径校验，
slug 只允许 [a-z0-9-]）；switch_tier 的 amount 参数取自页面 radio value
原样回传前经同校验。
"""
from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote, urlparse

import aiohttp
from sqlalchemy import delete, func, or_, select, update

from app.core.database import WritePriority, get_session_factory, write_gate
from app.domains.gmg.models import GmgBundle, GmgBundleGame

logger = logging.getLogger(__name__)

_GMG_LIST_URL = "https://www.greenmangaming.com/zh/bundles/"
_GMG_DETAIL_URL = "https://www.greenmangamingbundles.com/bundles/{slug}/"
_GMG_SWITCH_TIER_URL = (
    "https://www.greenmangamingbundles.com/zh-hans/bundles/{slug}/switch_tier/"
)
# 出网白名单：scheme + host 双校验
_ALLOWED_HOSTS = frozenset({
    "www.greenmangaming.com",
    "www.greenmangamingbundles.com",
})
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _gmg_headers() -> dict:
    return {
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "User-Agent": _UA,
        # 固定 zh 语区：子站按 geo 出价，语言头是我们能钉住币种形态的最强信号
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


async def _strategy_proxy() -> str | None:
    try:
        from app.domains.proxies import service as proxies_service

        return await proxies_service.resolve_proxy_url()
    except Exception:  # noqa: BLE001
        return None


# ── 解析层（纯函数，测试直接打点）─────────────────────────────

def _display_text(s: str) -> str:
    """入库显示名清洗：HTML 实体还原 + NFKC（与 humble 链同口径）。"""
    import unicodedata

    return unicodedata.normalize("NFKC", html_mod.unescape(s or "")).strip()


_CCY_SYMBOLS = (("CN¥", "CNY"), ("¥", "CNY"), ("￥", "CNY"), ("$", "USD"),
                ("€", "EUR"), ("£", "GBP"))


def _parse_money(text: str) -> tuple[str, int] | None:
    """`CN¥45.00` / `$12.99` / `&yen;591` → (币种, 分)；未知符号/畸形返回 None。"""
    t = html_mod.unescape((text or "").strip())
    for sym, ccy in _CCY_SYMBOLS:
        if sym in t:
            m = re.search(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)", t.replace(sym, "", 1))
            if not m:
                return None
            num = m.group(1)
            # 逗号双语义：结尾 1-2 位 = 欧式小数逗号（9,99→9.99）；其余按千分位剥除
            if re.search(r",\d{1,2}$", num) and "." not in num:
                num = num.replace(",", ".")
            else:
                num = num.replace(",", "")
            try:
                amount = float(num)
            except ValueError:
                return None
            return ccy, int(round(amount * 100))
    return None


def _money_map(text: str) -> dict[str, int] | None:
    pair = _parse_money(text)
    return {pair[0]: pair[1]} if pair else None


def parse_list(html_text: str) -> list[dict]:
    """主站 /zh/bundles/ → 在售游戏捆绑包行（data-bundle-state=live 且
    data-category=video-games；书类等其它 category 不入库）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for card_m in re.finditer(r'<div class="product-card"[^>]*>', html_text):
        tag = card_m.group(0)
        if 'data-bundle-state="live"' not in tag or 'data-category="video-games"' not in tag:
            continue
        seg_end = html_text.find('class="product-card"', card_m.end())
        seg = html_text[card_m.start(): seg_end if seg_end != -1 else card_m.start() + 4000]
        slug = None
        href_m = re.search(r'href="https://www\.greenmangamingbundles\.com/bundles/([a-z0-9-]+)/?', seg)
        if href_m:
            slug = href_m.group(1)
        if not slug or not _SLUG_RE.fullmatch(slug) or slug in seen:
            continue
        seen.add(slug)
        title_m = re.search(r'<h3 class="product-title">([^<]+)</h3>', seg)
        img_m = re.search(r'<img src="([^"]+)"', seg)
        end_m = re.search(r'data-end-date="([^"]+)"', tag)
        from_m = re.search(r'class="from-price">[^<]*<span>([^<]+)</span>', seg)
        out.append({
            "slug": slug,
            "name": _display_text(title_m.group(1)) if title_m else slug,
            "image": (img_m.group(1) if img_m else "").strip(),
            "end_at": _parse_naive_dt(end_m.group(1)) if end_m else None,
            "price_map": _money_map(html_mod.unescape(from_m.group(1))) if from_m else None,
        })
    return out


def _parse_naive_dt(raw: str) -> datetime | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", raw or "")
    if not m:
        return None
    try:
        y, mo, d, h, mi = (int(g) for g in m.groups())
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


# 档位 radio：value="{id}:{price}"（价格两处来源：value 与 .price 文案，
# 以 .price 文案为准——含币种符号；value 只取档 id 与回传参数）
_RADIO_RE = re.compile(
    r'<input[^>]*name="amount"[^>]*?data-upgrade-tier-id="([^"]+)"[^>]*?value="([^"]+)"',
    re.DOTALL,
)
_TIER_LABEL_RE = re.compile(
    r'<span class="bundle-type[^"]*">([^<]+)</span>\s*<small>\s*(\d+)\s*款游戏',
)
_PRICE_RE = re.compile(r'<span class="price">([^<]+)</span>')
_GAME_TITLE_RE = re.compile(r'<strong class="game-title">([^<]+)</strong>')
_EMPTY_BODY_MIN = 2000  # 子站软封锁的空响应（200 + 0 字节/壳页）按失败计


def parse_detail(html_text: str) -> dict | None:
    """子站详情页 → {tiers_raw, titles}；无档位 radio（壳页/过期跳转）返回 None。

    tiers_raw：[{id, header, count}]（累计件数，按 count 升序）；titles：
    默认网格全量（最高档内容，顺序与档位无关）。"""
    radios = _RADIO_RE.findall(html_text)
    if not radios:
        return None
    labels = _TIER_LABEL_RE.findall(html_text)
    prices = _PRICE_RE.findall(html_text)
    tiers_raw: list[dict] = []
    for i, (tid, _value) in enumerate(radios):
        name = _display_text(labels[i][0]) if i < len(labels) else tid
        count = int(labels[i][1]) if i < len(labels) else 0
        price_text = prices[i] if i < len(prices) else ""
        tiers_raw.append({
            "id": tid,
            "header": name,
            "count": count,
            "price_map": _money_map(html_mod.unescape(price_text)),
        })
    tiers_raw.sort(key=lambda t: (t["count"], t["id"]))
    titles = [_display_text(t) for t in _GAME_TITLE_RE.findall(html_text)]
    titles = list(dict.fromkeys(t for t in titles if t))
    return {"tiers_raw": tiers_raw, "titles": titles}


def parse_switch_tier(html_text: str) -> list[str]:
    """switch_tier（最低档）响应 → 全量游戏（按档位升序排列）。"""
    titles = [_display_text(t) for t in _GAME_TITLE_RE.findall(html_text)]
    return list(dict.fromkeys(t for t in titles if t))


def assign_tier_titles(tiers_raw: list[dict], ordered_titles: list[str]) -> list[dict] | None:
    """档位元数据 × 档位升序全量 → 各档新增 titles（PWY 累进语义）。

    校验：累计数严格递增且总数 = 全量长度、切片集合无重叠——失败返回 None
    （调用方退化为单伪档，宁缺勿错）。"""
    counts = [int(t["count"]) for t in tiers_raw]
    if not counts or counts != sorted(counts) or len(set(counts)) != len(counts):
        return None
    if counts[-1] != len(ordered_titles):
        return None
    out: list[dict] = []
    prev = 0
    seen: set[str] = set()
    for t, c in zip(tiers_raw, counts):
        addition = ordered_titles[prev:c]
        if any(x in seen for x in addition):
            return None
        seen.update(addition)
        out.append({**t, "titles": addition})
        prev = c
    return out


def build_tiers_fallback(tiers_raw: list[dict], titles: list[str]) -> list[dict]:
    """分档归属失败时的退化：全量归入最高档（买最高档实拿全部，语义不错）。"""
    top = tiers_raw[-1] if tiers_raw else {"id": "all", "header": "", "count": len(titles)}
    tiers = [{**t, "titles": []} for t in tiers_raw[:-1]]
    tiers.append({**top, "titles": list(titles)})
    return tiers


def merge_tiers(detail: dict, ordered_titles: list[str] | None) -> list[dict]:
    """详情解析 → tiers_json 结构（price_map 缺失保留 None，下轮自愈）。"""
    tiers_raw = detail["tiers_raw"]
    assigned = assign_tier_titles(tiers_raw, ordered_titles) if ordered_titles else None
    src = assigned if assigned else build_tiers_fallback(tiers_raw, detail["titles"])
    return [
        {
            "id": t["id"],
            "header": t.get("header") or "",
            "price_map": t.get("price_map"),
            "is_initial": i == 0,
            "titles": t["titles"],
        }
        for i, t in enumerate(src)
    ]


# ── 抓取层 ────────────────────────────────────────────────────────

async def _fetch_page(session: aiohttp.ClientSession, proxy: str | None, url: str,
                      attempts: int = 3) -> str | None:
    """白名单域 GET；子站对 CN 出口软封锁（200+空体/302 循环）——短重试，
    全部失败返回 None 由调用方留待 stale 自愈。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None
    for i in range(attempts):
        try:
            async with session.get(
                url, headers=_gmg_headers(), proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=40),
            ) as resp:
                if resp.status == 200:
                    body = await resp.text(errors="replace")
                    if len(body) >= _EMPTY_BODY_MIN:
                        return body
        except Exception:  # noqa: BLE001
            pass
        if i + 1 < attempts:
            await asyncio.sleep(2)
    return None


async def _fetch_switch_tier(session: aiohttp.ClientSession, proxy: str | None,
                             slug: str, amount_value: str) -> str | None:
    """最低档 radio value（`{id}:{price}`）→ 档位升序全量游戏页。"""
    if not _SLUG_RE.fullmatch(slug) or not re.fullmatch(r"[a-z0-9-]+:[0-9.]+", amount_value):
        return None
    url = _GMG_SWITCH_TIER_URL.format(slug=slug) + f"?amount={quote(amount_value, safe=':')}"
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None
    for i in range(3):
        try:
            async with session.get(
                url, headers=_gmg_headers(), proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=40),
            ) as resp:
                if resp.status == 200:
                    body = await resp.text(errors="replace")
                    if len(body) >= _EMPTY_BODY_MIN:
                        return body
        except Exception:  # noqa: BLE001
            pass
        if i + 1 < 3:
            await asyncio.sleep(2)
    return None


# ── 入库层（与 humble 域同构：包行只增改不删，下架保留历史）────────

_RESOLVE_MAX_ATTEMPTS = 3
_REFRESH_LOCK = asyncio.Lock()
_refresh_task: asyncio.Task | None = None


async def _upsert_bundles(rows: list[dict]) -> int:
    from app.crawler.utils import get_beijing_time_obj

    now = get_beijing_time_obj().replace(tzinfo=None)
    slugs = [r["slug"] for r in rows]
    async with write_gate(WritePriority.BACKGROUND, label="gmg_sync"), \
            get_session_factory()() as session:
        for r in rows:
            existing = await session.get(GmgBundle, r["slug"])
            if existing is None:
                session.add(GmgBundle(
                    slug=r["slug"], name=r["name"], image=r["image"],
                    url=_GMG_DETAIL_URL.format(slug=r["slug"]),
                    price_map_json=(json.dumps(r["price_map"]) if r["price_map"] else None),
                    end_at=r["end_at"],
                    on_sale=True, first_seen_at=now, last_seen_at=now,
                ))
            else:
                existing.name = r["name"] or existing.name
                existing.image = r["image"] or existing.image
                if r["price_map"]:
                    existing.price_map_json = json.dumps(r["price_map"])
                if r["end_at"]:
                    existing.end_at = r["end_at"]
                existing.last_seen_at = now
                existing.on_sale = True
        if slugs:
            await session.execute(
                update(GmgBundle)
                .where(GmgBundle.on_sale.is_(True), GmgBundle.slug.notin_(slugs))
                .values(on_sale=False)
            )
        await session.commit()
    return len(slugs)


async def _sync_bundle_detail(
    session: aiohttp.ClientSession, proxy: str | None, bundle: GmgBundle,
    mark_updated: bool,
) -> dict:
    """单包详情：档位 + 全部包内条目入库（统一检索引擎关联，同 HB 语义）。"""
    from app.domains.games.models import Game
    from app.domains.metadata import service as metadata_service

    page = await _fetch_page(session, proxy, bundle.url)
    if not page:
        return {"slug": bundle.slug, "ok": False}
    detail = parse_detail(page)
    if detail is None:
        return {"slug": bundle.slug, "ok": False}
    titles: list[str] = detail["titles"]

    # 分档归属：最低档 switch_tier 响应给出档位升序全量
    lowest = detail["tiers_raw"][0] if detail["tiers_raw"] else None
    radio_value = _radio_value_of(page, lowest["id"]) if lowest else None
    ordered: list[str] | None = None
    if lowest and radio_value:
        sw_page = await _fetch_switch_tier(session, proxy, bundle.slug, radio_value)
        if sw_page:
            cand = parse_switch_tier(sw_page)
            if set(cand) == set(titles):
                ordered = cand
    tiers = merge_tiers(detail, ordered)

    async with get_session_factory()() as s:
        rows = (
            await s.execute(
                select(
                    GmgBundleGame.title, GmgBundleGame.appid,
                    GmgBundleGame.resolve_attempts, GmgBundleGame.bundle_id,
                ).where(GmgBundleGame.slug == bundle.slug)
            )
        ).all()
    resolved_known = {t for t, a, _n, _b in rows if a is not None}
    bundle_known = {t for t, _a, _n, b in rows if b is not None}
    pending_known = {t for t, a, _n, _b in rows if a is None}
    exhausted = {
        t for t, a, n, b in rows
        if a is None and b is None and int(n or 0) >= _RESOLVE_MAX_ATTEMPTS
    }
    to_resolve = [
        t for t in titles
        if t not in resolved_known and t not in exhausted and t not in bundle_known
    ]

    resolved: dict[str, int] = {}
    resolved_bundle: dict[str, int] = {}
    unresolved: list[str] = []
    for title in to_resolve:
        appid, bundleid = await metadata_service._resolve_target(session, title, proxy)
        if appid is not None:
            resolved[title] = appid
        elif bundleid is not None:
            resolved_bundle[title] = bundleid
        else:
            unresolved.append(title)
        await asyncio.sleep(0.3)

    need_ingest: list[int] = []
    if resolved:
        async with get_session_factory()() as s:
            have = {
                r[0] for r in await s.execute(
                    select(Game.appid).where(Game.appid.in_(list(resolved.values())))
                )
            }
        need_ingest = sorted({a for a in resolved.values() if a not in have})

    from app.crawler.utils import get_beijing_time_obj as _now

    # bundle 命中入库（0.6.0 根修 database is locked）：ensure 自带写闸且
    # 另开连接写 bundles 主档——必须在外层写事务之前（fanatical/humble 同型）
    for bid in dict.fromkeys(resolved_bundle.values()):
        try:
            from app.domains.bundles import refresh as bundles_refresh

            await bundles_refresh.ensure_bundle_stored(bid)
        except Exception:  # noqa: BLE001
            logger.exception("[gmg-bundles] bundle %d 联动入库异常", bid)

    now = _now().replace(tzinfo=None)
    async with write_gate(WritePriority.BACKGROUND, label="gmg_sync"), \
            get_session_factory()() as s:
        if detail["tiers_raw"]:
            await s.execute(
                update(GmgBundle).where(GmgBundle.slug == bundle.slug)
                .values(tiers_json=json.dumps(tiers, ensure_ascii=False))
            )
        if mark_updated:
            await s.execute(
                update(GmgBundle).where(GmgBundle.slug == bundle.slug)
                .values(updated_at=now)
            )
        existing = {
            t: (a, b) for t, a, b in await s.execute(
                select(
                    GmgBundleGame.title, GmgBundleGame.appid, GmgBundleGame.bundle_id,
                ).where(GmgBundleGame.slug == bundle.slug)
            )
        }
        for title in titles:
            if title in resolved:
                if title in existing:
                    if existing[title][0] is None:
                        await s.execute(
                            update(GmgBundleGame)
                            .where(GmgBundleGame.slug == bundle.slug,
                                   GmgBundleGame.title == title)
                            .values(appid=resolved[title])
                        )
                else:
                    s.add(GmgBundleGame(slug=bundle.slug, appid=resolved[title], title=title))
            elif title in resolved_bundle:
                bid = resolved_bundle[title]
                if title in existing:
                    if existing[title][1] is None:
                        await s.execute(
                            update(GmgBundleGame)
                            .where(GmgBundleGame.slug == bundle.slug,
                                   GmgBundleGame.title == title)
                            .values(bundle_id=bid)
                        )
                else:
                    s.add(GmgBundleGame(
                        slug=bundle.slug, appid=None, bundle_id=bid, title=title,
                    ))
            elif title not in existing:
                s.add(GmgBundleGame(
                    slug=bundle.slug, appid=None, title=title,
                    resolve_attempts=1 if title in unresolved else 0,
                ))
        for title in unresolved:
            if title in existing:
                await s.execute(
                    update(GmgBundleGame)
                    .where(GmgBundleGame.slug == bundle.slug, GmgBundleGame.title == title)
                    .values(resolve_attempts=GmgBundleGame.resolve_attempts + 1)
                )
        title_set = set(titles)
        stale_unresolved = [
            t for t, (a, b) in existing.items()
            if t not in title_set and a is None and b is None
        ]
        if stale_unresolved:
            for g in (
                await s.execute(
                    select(GmgBundleGame).where(
                        GmgBundleGame.slug == bundle.slug,
                        GmgBundleGame.title.in_(stale_unresolved),
                    )
                )
            ).scalars():
                await s.delete(g)
        count_sq = (
            select(func.count())
            .select_from(GmgBundleGame)
            .where(GmgBundleGame.slug == bundle.slug)
            .scalar_subquery()
        )
        await s.execute(
            update(GmgBundle).where(GmgBundle.slug == bundle.slug)
            .values(game_count=count_sq)
        )
        await s.commit()
    return {"slug": bundle.slug, "ok": True, "titles": len(titles),
            "resolved": len(resolved), "pendingRetry": len(pending_known),
            "ingestQueued": len(need_ingest), "unresolved": unresolved}


def _radio_value_of(page: str, tier_id: str) -> str | None:
    """详情页 radio value（`{id}:{price}`，switch_tier 回传参数）。"""
    m = re.search(
        rf'data-upgrade-tier-id="{re.escape(tier_id)}"[^>]*?value="([^"]+)"', page, re.DOTALL
    )
    return m.group(1) if m else None


async def _heal_bundle_refs() -> None:
    """联动悬空引用自愈（0.6.0）：条目带 bundle_id 但包主档缺失（ensure
    偶发失败=瞬时写锁/网络抖动）→ 补抓。bundle_known 使这类条目不再进 sync，
    不补就永远缺主档（捆绑包卡无价无图）。"""
    from app.domains.bundles.refresh import heal_bundle_refs

    async with get_session_factory()() as s:
        refs = (
            await s.execute(
                select(GmgBundleGame.bundle_id)
                .where(GmgBundleGame.bundle_id.is_not(None))
            )
        ).scalars().all()
    healed = await heal_bundle_refs([int(r) for r in refs])
    if healed:
        logger.info("[gmg-bundles] 联动悬空引用补抓 %d 个", healed)


async def refresh_gmg_bundles() -> dict:
    """列表 + 在售包详情全链路（每日调度与手动触发同入口，幂等）。

    重抓范围与 humble 同矩阵：新包 / 超 24h 在售包 / 档位缺失 / 有未放弃
    欠账条目；下架包保留历史不重抓。"""
    from app.crawler.utils import get_beijing_time_obj

    if _REFRESH_LOCK.locked():
        return {"ok": False, "error": "已有 GMG 捆绑包刷新在进行"}
    async with _REFRESH_LOCK:
        proxy = await _strategy_proxy()
        connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=60)
        async with aiohttp.ClientSession(connector=connector) as session:
            listing = await _fetch_page(session, proxy, _GMG_LIST_URL)
            if not listing:
                return {"ok": False, "error": "GMG 列表页抓取失败"}
            rows = parse_list(listing)
            if not rows:
                return {"ok": False, "error": "GMG 列表无在售游戏捆绑包"}
            await _upsert_bundles(rows)

            now = get_beijing_time_obj().replace(tzinfo=None)
            cutoff = datetime(now.year, now.month, now.day) - timedelta(hours=24)
            async with get_session_factory()() as s:
                has_pending = (
                    select(GmgBundleGame.id)
                    .where(
                        GmgBundleGame.slug == GmgBundle.slug,
                        GmgBundleGame.appid.is_(None),
                        GmgBundleGame.resolve_attempts < _RESOLVE_MAX_ATTEMPTS,
                    )
                    .exists()
                )
                stale = (
                    await s.execute(
                        select(GmgBundle).where(
                            GmgBundle.on_sale.is_(True),
                            or_(
                                GmgBundle.updated_at.is_(None),
                                GmgBundle.updated_at < cutoff,
                                GmgBundle.tiers_json.is_(None),
                                has_pending,
                            ),
                        )
                    )
                ).scalars().all()
            done: list[dict] = []
            for bundle in stale[:24]:
                done.append(await _sync_bundle_detail(session, proxy, bundle, mark_updated=True))
        unresolved_total = sum(len(d.get("unresolved") or []) for d in done)
        ingest = await scan_missing_ingest()
        await _heal_bundle_refs()
        logger.info("[gmg-bundles] 刷新完成：列表 %d 包，详情 %d 包（未解析 %d，收录 %d）",
                    len(rows), len(done), unresolved_total, ingest.get("queued", 0))
        return {"ok": True, "listed": len(rows), "details": done,
                "unresolvedTotal": unresolved_total, "ingest": ingest}


async def start_refresh() -> dict:
    """后台启动一轮刷新（HTTP 立即返回；进度轮询 bundles 接口的 running）。"""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return {"ok": True, "started": False, "running": True}
    _refresh_task = asyncio.create_task(refresh_gmg_bundles())
    return {"ok": True, "started": True, "running": True}


_reset_task: asyncio.Task | None = None


async def reset_bundle(slug: str) -> dict:
    """单包删数据 + 重抓（0.5.1）：条目行/档位清零 → 后台单包重抓。

    包主档保留（列表级元数据，删除会让面板少一行而下轮列表抓取原样
    重建）；acked_at 保留（重抓不复活 NEW 徽章）。重抓串行于
    _REFRESH_LOCK；重抓失败（下架/网络/软封锁）数据保持清空，stale
    矩阵按 tiers_json NULL 自愈重抓。"""
    global _reset_task
    if _REFRESH_LOCK.locked():
        return {"ok": False, "error": "已有 GMG 捆绑包刷新在进行"}
    if _reset_task is not None and not _reset_task.done():
        return {"ok": False, "error": "已有 GMG 单包重抓在进行"}
    async with write_gate(WritePriority.BACKGROUND, label="gmg_reset"), \
            get_session_factory()() as s:
        bundle = await s.get(GmgBundle, slug)
        if bundle is None:
            return {"ok": False, "error": "包不存在"}
        snapshot = {"name": bundle.name, "url": bundle.url}
        await s.execute(
            delete(GmgBundleGame).where(GmgBundleGame.slug == slug)
        )
        await s.execute(
            update(GmgBundle).where(GmgBundle.slug == slug)
            .values(tiers_json=None, game_count=0)
        )
        await s.commit()
    # 强引用登记（防 GC 静默回收，同 HB 0.6.0 修）
    _reset_task = asyncio.create_task(_reset_refetch(slug, snapshot))
    return {"ok": True, "started": True}


async def _reset_refetch(slug: str, snapshot: dict) -> None:
    """单包重抓后台任务：抓详情页全量重建条目/档位（与刷新同链同语义）。"""
    if _REFRESH_LOCK.locked():
        logger.info("[gmg-bundles] 单包重抓跳过（全量刷新进行中，会顺带重建）: %s", slug)
        return
    async with _REFRESH_LOCK:
        proxy = await _strategy_proxy()
        connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=60)
        async with aiohttp.ClientSession(connector=connector) as session:
            bundle = GmgBundle(slug=slug, name=snapshot["name"], url=snapshot["url"])
            res = await _sync_bundle_detail(session, proxy, bundle, mark_updated=True)
        await scan_missing_ingest()
        await _heal_bundle_refs()
        logger.info("[gmg-bundles] 单包重抓完成: %s → ok=%s", slug, res.get("ok"))


# ── 收录链（与 humble 域同型）─────────────────────────────────────

_INGEST_BATCH = 200
_INGEST_MAX_BATCHES = 10

_ingest_task: asyncio.Task | None = None


async def _queue_ingest(appids: list[int], source: str) -> int:
    ids = list(dict.fromkeys(appids))[:_INGEST_BATCH]
    if not ids:
        return 0
    try:
        from app.domains.crawl import service as crawl_service

        results = await crawl_service.run_sequential(
            [{"scope": "appids", "appids": ids, "kind": "gmg_backfill"}]
        )
    except Exception:  # noqa: BLE001
        logger.info("[gmg-ingest] %s 首爬登记跳过（%d 款留待下拍）", source, len(ids))
        return 0
    if not results:
        logger.info("[gmg-ingest] %s 爬虫占用跳过本批（%d 款留待下拍）", source, len(ids))
        return 0
    logger.info("[gmg-ingest] %s 登记首爬 %d 款", source, len(ids))
    return len(ids)


async def _ingest_candidates() -> list[int]:
    from app.domains.games.models import Game

    async with get_session_factory()() as s:
        rows = (
            await s.execute(
                select(GmgBundleGame.appid, GmgBundle.first_seen_at)
                .join(GmgBundle, GmgBundle.slug == GmgBundleGame.slug)
                .where(GmgBundle.on_sale.is_(True), GmgBundleGame.appid.is_not(None))
            )
        ).all()
        freshness: dict[int, object] = {}
        for a, seen_at in rows:
            a = int(a)
            cur = freshness.get(a)
            if seen_at is not None and (cur is None or seen_at > cur):
                freshness[a] = seen_at
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
    missing.sort()
    missing.sort(
        key=lambda a: freshness[a] if freshness[a] is not None else datetime.min,  # type: ignore[arg-type,return-value]
        reverse=True,
    )
    return missing


async def scan_missing_ingest() -> dict:
    """目录收录扫描（kind=gmg_backfill）。"""
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
    return {"ok": True, "candidates": len(candidates), "queued": queued,
            "batches": batches, "remaining": len(candidates) - queued}


async def start_ingest_now() -> dict:
    global _ingest_task
    if _ingest_task is not None and not _ingest_task.done():
        return {"ok": True, "started": False, "running": True}
    _ingest_task = asyncio.create_task(scan_missing_ingest())
    return {"ok": True, "started": True, "running": True}


# ── API 服务层 ────────────────────────────────────────────────────

def _price_map_out(raw: str | None) -> dict[str, int] | None:
    if not raw:
        return None
    try:
        m = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return m if isinstance(m, dict) and m else None


async def list_bundles() -> dict:
    """在售包列表（前端中心面板数据源；纯本地读，零外网）。"""
    from app.crawler.utils import get_beijing_time_obj

    running = _refresh_task is not None and not _refresh_task.done()
    ingest_running = _ingest_task is not None and not _ingest_task.done()
    try:
        ingest_pending = len(await _ingest_candidates())
    except Exception:  # noqa: BLE001
        ingest_pending = 0
    async with get_session_factory()() as s:
        rows = (
            await s.execute(
                select(GmgBundle).order_by(
                    GmgBundle.end_at.asc().nulls_last(), GmgBundle.slug
                )
            )
        ).scalars().all()
    now = get_beijing_time_obj().replace(tzinfo=None)
    items = [
        {
            "slug": r.slug, "name": r.name, "image": r.image, "url": r.url,
            "priceMap": _price_map_out(r.price_map_json),
            "startAt": r.start_at.isoformat() if r.start_at else None,
            "endAt": r.end_at.isoformat() if r.end_at else None,
            "onSale": r.on_sale and (r.end_at is None or r.end_at >= now),
            "gameCount": r.game_count,
            "isNew": r.acked_at is None,
        }
        for r in rows
        if r.on_sale and (r.end_at is None or r.end_at >= now)
    ]
    return {"running": running, "ingestRunning": ingest_running,
            "ingestPending": ingest_pending, "bundles": items}


async def mark_seen(slug: str) -> bool:
    from app.crawler.utils import get_beijing_time_obj

    now = get_beijing_time_obj().replace(tzinfo=None)
    async with write_gate(WritePriority.BACKGROUND, label="gmg_seen"), \
            get_session_factory()() as s:
        bundle = await s.get(GmgBundle, slug)
        if bundle is None:
            return False
        if bundle.acked_at is None:
            bundle.acked_at = now
            await s.commit()
    return True


async def bundle_detail(slug: str) -> dict | None:
    """单包详情：包信息 + 包内游戏（pending 三态）+ 累进档位展开（同 HB）。"""
    from app.domains.games import service as games_service
    from app.domains.games.models import Game

    async with get_session_factory()() as s:
        bundle = await s.get(GmgBundle, slug)
        if bundle is None:
            return None
        rows = (
            await s.execute(
                select(
                    GmgBundleGame.appid, GmgBundleGame.title,
                    GmgBundleGame.resolve_attempts, GmgBundleGame.bundle_id,
                ).where(GmgBundleGame.slug == slug)
            )
        ).all()
        appids = [int(a) for a, _t, _n, _b in rows if a is not None]
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
            "bundleId": int(b) if b is not None else None,
            "status": (
                "bundle"
                if a is None and b is not None
                else "not_found"
                if a is None and int(n or 0) >= _RESOLVE_MAX_ATTEMPTS
                else "ingesting" if a is not None and int(a) not in have
                else "resolving"
            ),
        }
        for a, title, n, b in rows
        if a is None or int(a) not in have
    ]
    title_appid: dict[str, int | None] = {}
    title_bundle: dict[str, int | None] = {}
    for a, title, _n, b in rows:
        title_appid.setdefault(title, int(a) if a is not None else None)
        title_bundle.setdefault(title, int(b) if b is not None else None)

    # 累进档位展开（与 humble 同语义）：每档 games = 买该档实拿全部，
    # newGames = 本档新增，unlockPriceMap = 首现档价
    tiers_out: list[dict] = []
    if bundle.tiers_json:
        try:
            raw_tiers = json.loads(bundle.tiers_json)
        except (ValueError, TypeError):
            raw_tiers = []
        seen: set[str] = set()
        acc: list[dict] = []
        for idx, t in enumerate(raw_tiers if isinstance(raw_tiers, list) else []):
            if not isinstance(t, dict):
                continue
            new_titles: list[str] = []
            for x in t.get("titles") or []:
                if isinstance(x, str) and x not in seen:
                    seen.add(x)
                    new_titles.append(x)
            news = [
                {"title": x, "appid": title_appid.get(x),
                 "bundleId": title_bundle.get(x),
                 "unlockPriceMap": t.get("price_map")}
                for x in new_titles
            ]
            acc.extend(news)
            cutoff = len(acc) - len(news)
            tiers_out.append({
                "id": str(t.get("id") or f"t{idx}"),
                "header": str(t.get("header") or ""),
                "priceMap": t.get("price_map"),
                "isInitial": bool(t.get("is_initial")),
                "newGames": [{"title": g["title"], "appid": g["appid"]} for g in news],
                "newCount": len(news),
                "count": len(seen),
                "games": [{**g, "isNew": i >= cutoff} for i, g in enumerate(acc)],
            })

    payload: dict = {
        "slug": bundle.slug, "name": bundle.name, "image": bundle.image, "url": bundle.url,
        "priceMap": _price_map_out(bundle.price_map_json),
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
