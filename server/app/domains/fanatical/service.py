"""Fanatical 捆绑包（fanatical.com）服务：列表 / 档位抓取 + 包内游戏关联。

数据链路（2026-10 探测定案，全 JSON、无需登录；4 类请求 ≈10 次/轮）：
- 列表 + 档位内容：页面取 `window.searchKey`（secured key 约 2h 时效，现取
  现用）→ Algolia `fan` 索引查 `display_type:bundle`（34 条 = 档位游戏包 +
  游戏 pick&mix；书/软件类是 book-bundle 等自动排除）。命中自带
  `bundle_tiers[].products[]`（每档**完整**清单且带 steamId——实测与详情
  API 逐条一致；pick 包的全池挂在首档）。
- pick 数量档价格：/api/all/en 的 pickandmix 数组（35 条全类型，按 slug 取）。
- tiered 档位价格：/api/products/{slug}/en 的 `bundles[]`（每档 price map；
  该数组即档位，**非累进**，档间独立集合）。

steamId 语义：int 且 steamIdType=app → appid 直取；缺失/非 app = 非 Steam
发行（drm.steam=false），attempts 直置满标「未找到」——站点数据是权威，
不做名称检索兜底（宁缺勿猜：F 站没卖 Steam 键的条目解出来也是错关联）。

价格语义：API 各价均为**主单位浮点**（USD 199 = $1.99；JPY 34500 亦是
×100 形态），统一 round(×100) 存「分」；多币种全存不折算，展示由前端切换。

安全约束：出网 URL 由常量域拼接（https + host 白名单 + 路径校验）；封面
取 Algolia cover 文件名拼 cdn-ext 直链，绝不让页面内容决定请求 host。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
from sqlalchemy import delete, func, select, update

from app.core.database import WritePriority, get_session_factory, write_gate
from app.domains.fanatical.models import FanaticalBundle, FanaticalBundleGame

logger = logging.getLogger(__name__)

_FANATICAL_BASE = "https://www.fanatical.com"
_PAGE_PATH = "/en/bundle/games"
_ALL_PATH = "/api/all/en"
_PRODUCT_API = "https://www.fanatical.com/api/products/{slug}/en"
_PICK_API = "https://www.fanatical.com/api/pick-and-mix/{slug}/en"
# 出网白名单：scheme + host 双校验（Algolia secured key 从页面提取，查询
# 端点固定为该 App ID 对应的域）
_ALLOWED_HOSTS = frozenset({
    "www.fanatical.com",
    "w2m9492ddv-dsn.algolia.net",
})
_COVER_URL = "https://cdn-ext.fanatical.com/production/product/1280x720/{file}"
_COVER_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_ALGOLIA_URL = "https://w2m9492ddv-dsn.algolia.net/1/indexes/fan/query"
_ALGOLIA_APP_ID = "W2M9492DDV"
_SEARCH_KEY_RE = re.compile(r"searchKey\s*=\s*'([^']+)'")
_ALGOLIA_FILTER = "display_type:bundle"
_ALGOLIA_HITS = 100

# 非 Steam 直标阈值：与 humble 域 attempts 账本同语义（≥3 → 前端 not_found）
RESOLVE_MAX_ATTEMPTS = 3

# 详情 API 的 WAF 校验：UA 之外必须像 XHR（缺 Accept/Referer/Origin → 403）
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _page_headers() -> dict:
    return {"Accept": "text/html,application/json;q=0.9,*/*;q=0.8", "User-Agent": _UA}


def _api_headers(referer: str) -> dict:
    return {
        "Accept": "application/json",
        "User-Agent": _UA,
        "Referer": referer,
        "Origin": _FANATICAL_BASE,
    }


async def _strategy_proxy() -> str | None:
    try:
        from app.domains.proxies import service as proxies_service

        return await proxies_service.resolve_proxy_url()
    except Exception:  # noqa: BLE001
        return None


# ── 解析层（纯函数，测试直接打点）─────────────────────────────

def extract_search_key(page_html: str) -> str | None:
    """任意 Fanatical 页面 → Algolia secured searchKey（约 2h 时效现取现用）。"""
    m = _SEARCH_KEY_RE.search(page_html or "")
    return m.group(1) if m else None


def _to_minor(v: object) -> int | None:
    """主单位浮点 → 分（Algolia 价是主单位浮点：USD 1 = $1、JPY 345 = ¥345）。"""
    try:
        n = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return int(round(n * 100)) if n >= 0 else None


def _minor_map(raw: object) -> dict[str, int] | None:
    """详情 API 价已是**分**（USD 5999 = $59.99、JPY 629000 = ¥6290，0.4.0
    实测与 Algolia 主单位量纲不同）——原样取整入库，不再 ×100。"""
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, int] = {}
    for ccy, v in raw.items():
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        if n >= 0 and isinstance(ccy, str) and len(ccy) == 3:
            out[ccy.upper()] = int(round(n))
    return out or None


def _price_map(raw: object) -> dict[str, int] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, int] = {}
    for ccy, v in raw.items():
        minor = _to_minor(v)
        if minor is not None and isinstance(ccy, str) and len(ccy) == 3:
            out[ccy.upper()] = minor
    return out or None


def _min_price_map(maps: list[dict[str, int] | None]) -> dict[str, int] | None:
    """多档价格 map → 最低档 map（按 USD 比价保证「FROM」基准一致）；
    无 USD 档可跑比价时退回首个非空 map（宁缺勿错，不做跨币种折算）。"""
    usable = [m for m in maps if m and isinstance(m.get("USD"), int)]
    if usable:
        return min(usable, key=lambda m: m["USD"])
    return next((m for m in maps if m), None)


def _cover_url(file: object) -> str:
    f = str(file or "")
    return _COVER_URL.format(file=f) if f and _COVER_FILE_RE.fullmatch(f) else ""


def parse_hits(hits: list) -> list[dict]:
    """Algolia 命中 → 捆绑包行（只收游戏类：display_type=bundle 且非 mystery）。

    每行带 kind/tiers（档内容）/price_map（最低档）。tiered 的档内容取
    bundle_tiers[].products（**该档全部，非累进**）；pick 的池子挂首档。"""
    out: list[dict] = []
    seen: set[str] = set()
    for h in hits:
        if not isinstance(h, dict):
            continue
        if h.get("display_type") != "bundle" or h.get("mystery"):
            continue
        slug = str(h.get("slug") or "").strip("/")
        if not slug or "/" in slug or slug in seen:
            continue
        kind = "pick" if h.get("type") == "pick-and-mix" else "tiered"
        tiers_raw = h.get("bundle_tiers")
        tiers: list[dict] = []
        steam_products: list[dict] = []
        for i, t in enumerate(tiers_raw if isinstance(tiers_raw, list) else []):
            if not isinstance(t, dict):
                continue
            products = [p for p in (t.get("products") or []) if isinstance(p, dict)]
            steam_products.extend(products)
            titles = [
                str(p.get("name") or "").strip()
                for p in products
                if str(p.get("name") or "").strip()
            ]
            if kind == "pick" and not titles:
                continue  # pick 池挂首档，空档跳过
            tiers.append({"id": f"t{i}", "quantity": t.get("displayTotal"), "titles": titles})
        if not tiers:
            continue
        seen.add(slug)
        out.append({
            "slug": slug,
            "kind": kind,
            "name": str(h.get("name") or slug),
            "image": _cover_url(h.get("cover")),
            "price_map": _price_map(h.get("price")),
            "start_at": _unix_to_dt(h.get("available_valid_from")),
            "end_at": _unix_to_dt(h.get("available_valid_until")),
            "game_count": int(h.get("game_total") or 0) or sum(len(t["titles"]) for t in tiers),
            "tiers": tiers,
            "steam_products": steam_products,
        })
    return out


def _unix_to_dt(v: object) -> datetime | None:
    try:
        n = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    # 站点用「公元 3001 年」表示永久在售（实测 available_valid_until =
    # 32503680000），超过 2100 年的一律视为无到期（0.5.0 显示修复根因）
    if n > 4102444800:  # 2100-01-01
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc).replace(tzinfo=None)


def parse_pick_prices(all_data: object) -> dict[str, list[dict]]:
    """/api/all/en → {slug: [{quantity, price{}}]}（选择包数量档价格，分）。"""
    out: dict[str, list[dict]] = {}
    if not isinstance(all_data, dict):
        return out
    for p in all_data.get("pickandmix") or []:
        if not isinstance(p, dict):
            continue
        slug = str(p.get("slug") or "")
        tiers = []
        for t in p.get("tiers") or []:
            if not isinstance(t, dict):
                continue
            pm = _minor_map(t.get("price"))
            if pm and isinstance(t.get("quantity"), int):
                tiers.append({"quantity": int(t["quantity"]), "price": pm})
        if slug and tiers:
            out[slug] = sorted(tiers, key=lambda t: t["quantity"])
    return out


def parse_product_tiers(data: object) -> list[dict]:
    """tiered 详情 /api/products/{slug}/en → [{price{}, full_price{}}]（按 USD 升序）。

    `bundles[]` 每元素即一档（非累进独立集合）；本函数只取价格与档序，
    档内容以 Algolia bundle_tiers 为准（同源，少一次大 JSON 切割）。
    价格量纲：详情 API 是分（与 Algolia 主单位不同，见 _minor_map）。"""
    if not isinstance(data, dict):
        return []
    tiers: list[dict] = []
    for b in data.get("bundles") or []:
        if not isinstance(b, dict):
            continue
        pm = _minor_map(b.get("price"))
        if pm:
            tiers.append({"price": pm, "full_price": _minor_map(b.get("fullPrice"))})
    tiers.sort(key=lambda t: t["price"].get("USD") or t["price"][next(iter(t["price"]))])
    return tiers


def parse_pick_detail(data: object) -> list[dict]:
    """pick 详情 /api/pick-and-mix/{slug}/en → [{quantity, price{}}]（升序，分）。"""
    if not isinstance(data, dict):
        return []
    tiers: list[dict] = []
    for t in data.get("tiers") or []:
        if not isinstance(t, dict):
            continue
        pm = _minor_map(t.get("price"))
        if pm and isinstance(t.get("quantity"), int):
            tiers.append({"quantity": int(t["quantity"]), "price": pm})
    tiers.sort(key=lambda t: t["quantity"])
    return tiers


def merge_tiers(row: dict, prices: list[dict]) -> list[dict] | None:
    """档内容（Algolia）× 档价格（详情）→ tiers_json 结构。

    tiered：两源档数一致时逐档配价；不一致（页面切换期）价格留空——
    宁缺勿错，下一轮自愈。pick：数量档价格 × 全池（池子在 games 表）。"""
    if row["kind"] == "pick":
        return [
            {"id": f"q{p['quantity']}", "quantity": p["quantity"], "price": p["price"],
             "header": "", "titles": []}
            for p in prices
        ] or None
    tiers = row["tiers"]
    out: list[dict] = []
    if len(prices) == len(tiers):
        for t, p in zip(tiers, prices):
            out.append({"id": t["id"], "quantity": t.get("quantity"), "price": p["price"],
                        "header": "", "titles": t["titles"]})
    else:
        out = [{"id": t["id"], "quantity": t.get("quantity"), "price": None,
                "header": "", "titles": t["titles"]} for t in tiers]
    return out or None


# ── 抓取层 ────────────────────────────────────────────────────────

async def _get_json(session, proxy, url, headers) -> object | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None
    try:
        async with session.get(url, headers=headers, proxy=proxy,
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)
    except Exception:  # noqa: BLE001
        return None


async def _get_text(session, proxy, url, headers) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None
    try:
        async with session.get(url, headers=headers, proxy=proxy,
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                return None
            return await resp.text(errors="replace")
    except Exception:  # noqa: BLE001
        return None


async def _algolia_query(session, proxy, key: str) -> list:
    body = {"query": "", "filters": _ALGOLIA_FILTER, "hitsPerPage": _ALGOLIA_HITS}
    parsed = urlparse(_ALGOLIA_URL)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return []
    try:
        async with session.post(
            _ALGOLIA_URL, json=body,
            headers={
                "Content-Type": "application/json",
                "x-algolia-application-id": _ALGOLIA_APP_ID,
                "x-algolia-api-key": key,
            },
            proxy=proxy, timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
        hits = (data or {}).get("hits")
        return hits if isinstance(hits, list) else []
    except Exception:  # noqa: BLE001
        return []


async def _tier_prices_for(session, proxy, row: dict,
                           pick_prices: dict[str, list[dict]]) -> list[dict]:
    """单包档位价格：pick 走 /api/all/en 缓存（缺了补详情）；tiered 走详情。"""
    if row["kind"] == "pick":
        prices = [
            {"quantity": t["quantity"], "price": t["price"]}
            for t in pick_prices.get(row["slug"], [])
        ]
        if prices:
            return prices
        data = await _get_json(
            session, proxy, _PICK_API.format(slug=row["slug"]),
            _api_headers(f"{_FANATICAL_BASE}/en/pick-and-mix/{row['slug']}"),
        )
        return parse_pick_detail(data)
    data = await _get_json(
        session, proxy, _PRODUCT_API.format(slug=row["slug"]),
        _api_headers(f"{_FANATICAL_BASE}/en/bundle/{row['slug']}"),
    )
    return [
        {"price": t["price"]} for t in parse_product_tiers(data)
    ]


# ── 入库层（与 humble 域同构：包行只增改不删，下架保留历史）────────

async def _upsert_bundles(rows: list[dict]) -> int:
    from app.crawler.utils import get_beijing_time_obj

    now = get_beijing_time_obj().replace(tzinfo=None)
    slugs = [r["slug"] for r in rows]
    async with write_gate(WritePriority.BACKGROUND, label="fanatical_sync"), \
            get_session_factory()() as session:
        for r in rows:
            existing = await session.get(FanaticalBundle, r["slug"])
            if existing is None:
                session.add(FanaticalBundle(
                    slug=r["slug"], kind=r["kind"], name=r["name"], image=r["image"],
                    url=f"{_FANATICAL_BASE}/en/"
                        f"{'pick-and-mix' if r['kind'] == 'pick' else 'bundle'}/{r['slug']}",
                    price_map_json=(json.dumps(r["price_map"]) if r["price_map"] else None),
                    start_at=r["start_at"], end_at=r["end_at"],
                    on_sale=True, first_seen_at=now, last_seen_at=now,
                ))
            else:
                existing.name = r["name"] or existing.name
                existing.image = r["image"] or existing.image
                existing.kind = r["kind"]  # 站点类型切换（罕见）随源修正
                if r["price_map"]:
                    existing.price_map_json = json.dumps(r["price_map"])
                if r["end_at"]:
                    existing.end_at = r["end_at"]
                if r["start_at"]:
                    existing.start_at = r["start_at"]
                existing.last_seen_at = now
                existing.on_sale = True
        if slugs:
            await session.execute(
                update(FanaticalBundle)
                .where(FanaticalBundle.on_sale.is_(True), FanaticalBundle.slug.notin_(slugs))
                .values(on_sale=False)
            )
        await session.commit()
    return len(slugs)


# 站点自造合辑 SKU（不是单个 Steam app，检索也查不到单一条目）：
# Double/Triple Pack、"A + B" 组合、以 Pack/Bundle/Collection 收尾。
# 命中即直标 not_found，不烧检索额度（真实游戏极少以这些词收尾）。
_COMBO_SKU_RE = re.compile(
    r"double[- ]pack|triple[- ]pack|\s\+\s|\s&\s|\s(?:pack|bundle|collection)$",
    re.IGNORECASE,
)


def is_combo_sku(title: str) -> bool:
    return bool(_COMBO_SKU_RE.search(title or ""))


async def _sync_bundle_games(
    session: aiohttp.ClientSession, proxy: str | None, bundle_slug: str, row: dict,
) -> int:
    """包内条目入库（0.4.1 判据修正）：appid 直取优先；steamId 缺失时以
    **drm 字段**为权威——drm 含 steam = Steam 键商品（Algolia 索引常缺
    steamId，实测 Saint Kotar/Dreamscaper 等全是 drm:["steam"] + 空串 id），
    走统一检索引擎兜底（attempts 记账，与 HB/GMG 同账本）；drm 不含 steam
    = 确知非 Steam，直置满；合辑 SKU（Double/Triple Pack、A + B、Pack/
    Bundle/Collection 收尾）无单 app 对应，不烧 app 检索，走四档联动链
    （0.5.1）：① 本地 bundles 语料 exact/clean → ② 子包详情站点权威
    steam.id 直取 → ③ 子包详情 bundles[].games 成员清单逐个解析（站点明示
    包内有什么，实测 30/30 合辑 SKU 两类之一）→ ④ 全落空维持直标 not_found。
    检索兜底走 `resolve_target`（app 落空时 bundle 兜底），命中
    bundle → 复用 bundles 域入库 + 条目落 bundle_id（抽屉按捆绑包卡展示）；
    成员解析结果落 member_appids_json（抽屉按站点合辑卡展示）。
    返回条目数。"""
    from app.domains.metadata import service as metadata_service

    titles = list(dict.fromkeys(
        t for tier in row["tiers"] for t in tier["titles"]
    ))
    # 已有结论的条目（appid/bundle/成员清单）本轮不再重解（省检索额度；
    # 0.4.1 起兜底链在，此为幂等护栏——注释语义与实现对齐）
    async with get_session_factory()() as s:
        known_rows = (
            await s.execute(
                select(
                    FanaticalBundleGame.title, FanaticalBundleGame.appid,
                    FanaticalBundleGame.bundle_id,
                    FanaticalBundleGame.member_appids_json,
                    FanaticalBundleGame.resolve_attempts,
                ).where(FanaticalBundleGame.slug == bundle_slug)
            )
        ).all()
    appid_known = {t for t, a, _b, _m, _n in known_rows if a is not None}
    bundle_known = {t for t, _a, b, _m, _n in known_rows if b is not None}
    packed_known = {t for t, _a, _b, m, _n in known_rows if m}
    exhausted = {
        t for t, _a, _b, _m, n in known_rows
        if int(n or 0) >= RESOLVE_MAX_ATTEMPTS
    }
    # 名称 → (直取 appid | 是否 Steam 发行)；drm 是列表（如 ["steam"]）
    steam_map: dict[str, int | None] = {name: None for name in titles}
    on_steam: dict[str, bool] = {name: False for name in titles}
    for p in row.get("steam_products") or []:
        name = str(p.get("name") or "").strip()
        if name not in steam_map:
            continue
        sid = p.get("steamId")
        sid_type = str(p.get("steamIdType") or "app")
        if isinstance(sid, int) and sid > 0 and sid_type == "app":
            steam_map[name] = sid
        drm = p.get("drm") or []
        on_steam[name] = isinstance(drm, list) and "steam" in [str(d).lower() for d in drm]

    # 需要检索兜底的标题：无直取 appid、站点表明是 Steam 商品、非合辑 SKU、
    # 尚无结论且未达尝试上限
    to_resolve = [
        t for t in titles
        if steam_map.get(t) is None and on_steam.get(t) and not is_combo_sku(t)
        and t not in appid_known and t not in bundle_known and t not in exhausted
    ]
    resolved: dict[str, int] = {}
    resolved_bundle: dict[str, int] = {}
    resolved_pack: dict[str, list[dict]] = {}
    for title in to_resolve:
        appid, bundleid = await metadata_service._resolve_target(session, title, proxy)
        if appid is not None:
            resolved[title] = appid
        elif bundleid is not None:
            resolved_bundle[title] = bundleid
        await asyncio.sleep(0.3)

    # 合辑 SKU 四档联动链（不烧 app 检索；已 bundling/已展开/已直取的跳过）
    combo_titles = [
        t for t in titles
        if steam_map.get(t) is None and is_combo_sku(t)
        and t not in appid_known and t not in bundle_known and t not in packed_known
    ]
    slug_by_title = {
        str(p.get("name") or "").strip(): str(p.get("slug") or "").strip()
        for p in row.get("steam_products") or [] if p.get("name") and p.get("slug")
    }
    for title in combo_titles:
        # ① 本地 bundles 语料 exact/clean（合辑名走 app 五级必错对首成员）
        bid = await metadata_service._resolve_bundle_only(title)
        if bid is not None:
            resolved_bundle[title] = bid
            await asyncio.sleep(0.2)
            continue
        # ②③ 子包详情探查（站点自身写明的内容；1 req/s 限速）
        sub_slug = slug_by_title.get(title)
        if not sub_slug:
            continue
        detail = await _get_json(
            session, proxy, _PRODUCT_API.format(slug=sub_slug),
            _api_headers(_FANATICAL_BASE + "/en/bundle/games"),
        )
        await asyncio.sleep(0.5)
        if not isinstance(detail, dict):
            continue  # 详情拿不到 → 维持直标 not_found
        sid = (detail.get("steam") or {}).get("id")
        if detail.get("type") == "game" and isinstance(sid, int) and sid > 0:
            resolved[title] = sid  # ② 站点权威 appid 直取
            continue
        members: list[str] = []
        for b in detail.get("bundles") or []:
            for g in (b or {}).get("games") or []:
                name = str((g or {}).get("name") or "").strip()
                if name and name not in members:
                    members.append(name)
        if detail.get("type") != "bundle" or not members:
            continue
        # ③ 成员清单逐个解析（成员 = 真实游戏/DLC，标题可检索；未解析留 None）
        packed: list[dict] = []
        for m in members:
            m_appid, _m_bundle = await metadata_service._resolve_target(session, m, proxy)
            packed.append({"title": m, "appid": m_appid})
            await asyncio.sleep(0.3)
        resolved_pack[title] = packed

    # bundle 命中入库（0.6.0 根修 database is locked）：ensure_bundle_stored
    # 自带写闸并**另开一条连接**写 bundles 主档——必须放在外层写事务之前。
    # 嵌在闸内时同任务重入放行，但外层 session 未提交的 WAL 写锁与这条新连接
    # 互等（外层又在 await 它），busy_timeout 60s 到点即抛锁错（proxypool
    # health 的「网络与落库分离」同型范式）；引用落库仍留在下方闸内单事务。
    for bid in dict.fromkeys(resolved_bundle.values()):
        try:
            from app.domains.bundles import refresh as bundles_refresh

            await bundles_refresh.ensure_bundle_stored(bid)
        except Exception:  # noqa: BLE001
            logger.exception("[fanatical-bundles] bundle %d 联动入库异常", bid)

    async with write_gate(WritePriority.BACKGROUND, label="fanatical_games"), \
            get_session_factory()() as s:
        existing = {
            t: (a, m) for t, a, m in await s.execute(
                select(
                    FanaticalBundleGame.title, FanaticalBundleGame.appid,
                    FanaticalBundleGame.member_appids_json,
                )
                .where(FanaticalBundleGame.slug == bundle_slug)
            )
        }
        for title in titles:
            direct = steam_map.get(title)
            appid = direct or resolved.get(title) or None
            # 直标 not_found 仅限：确知非 Steam / 合辑 SKU 且四档联动全落空
            # （检索兜底失败的走 attempts 账本自然到顶，不在此置满）
            known_missing = steam_map.get(title) is None and (
                not on_steam.get(title)
                or (is_combo_sku(title) and title not in resolved_bundle
                    and title not in resolved_pack and title not in resolved)
            )
            if title in existing:
                old_appid, old_members = existing[title]
                if old_appid is None and appid is not None:
                    await s.execute(
                        update(FanaticalBundleGame)
                        .where(FanaticalBundleGame.slug == bundle_slug,
                               FanaticalBundleGame.title == title)
                        .values(appid=appid)
                    )
                elif appid is None and known_missing:
                    # 确知非 Steam / 合辑：attempts 幂等置满（不覆盖已有 appid）
                    await s.execute(
                        update(FanaticalBundleGame)
                        .where(FanaticalBundleGame.slug == bundle_slug,
                               FanaticalBundleGame.title == title,
                               FanaticalBundleGame.appid.is_(None))
                        .values(resolve_attempts=RESOLVE_MAX_ATTEMPTS)
                    )
            else:
                s.add(FanaticalBundleGame(
                    slug=bundle_slug, appid=appid, title=title,
                    member_appids_json=(
                        json.dumps(resolved_pack[title], ensure_ascii=False)
                        if title in resolved_pack else None
                    ),
                    resolve_attempts=(
                        0 if appid is not None
                        else RESOLVE_MAX_ATTEMPTS if known_missing
                        else 0 if title in resolved_bundle  # bundle 命中不算失败
                        else 0 if title in resolved_pack  # 成员清单落库同样有结论
                        else 1 if title in to_resolve
                        else 0
                    ),
                ))
        # 检索兜底失败的既有行：attempts+1（与 HB/GMG 账本同语义；达上限后
        # to_resolve 不再拾取——下一轮起 not_found）；bundle 命中的不算失败
        failed = [t for t in to_resolve if t not in resolved and t not in resolved_bundle]
        for title in failed:
            if title in existing:
                await s.execute(
                    update(FanaticalBundleGame)
                    .where(FanaticalBundleGame.slug == bundle_slug,
                           FanaticalBundleGame.title == title,
                           FanaticalBundleGame.appid.is_(None))
                    .values(resolve_attempts=FanaticalBundleGame.resolve_attempts + 1)
                )
        # bundle 命中：落引用（主档入库已在闸外 ensure 完成；ensure 失败
        # 也照落——bundle_known 不再重解，刷新尾 heal_bundle_refs 补抓主档）
        for title, bid in resolved_bundle.items():
            await s.execute(
                update(FanaticalBundleGame)
                .where(FanaticalBundleGame.slug == bundle_slug,
                       FanaticalBundleGame.title == title,
                       FanaticalBundleGame.appid.is_(None))
                .values(bundle_id=bid)
            )
        # 成员清单落库（0.5.1）：既有行补 member_appids_json（不覆盖已有 appid）
        for title, packed in resolved_pack.items():
            await s.execute(
                update(FanaticalBundleGame)
                .where(FanaticalBundleGame.slug == bundle_slug,
                       FanaticalBundleGame.title == title,
                       FanaticalBundleGame.appid.is_(None))
                .values(member_appids_json=json.dumps(packed, ensure_ascii=False))
            )
        count_sq = (
            select(func.count())
            .select_from(FanaticalBundleGame)
            .where(FanaticalBundleGame.slug == bundle_slug)
            .scalar_subquery()
        )
        await s.execute(
            update(FanaticalBundle).where(FanaticalBundle.slug == bundle_slug)
            .values(game_count=count_sq)
        )
        await s.commit()
    return len(titles)


# ── 刷新链（全量 ≈12 请求/轮：页 + Algolia + all + 每包价格详情）────

_REFRESH_LOCK = asyncio.Lock()
_refresh_task: asyncio.Task | None = None


async def _heal_bundle_refs() -> None:
    """联动悬空引用自愈（0.6.0）：条目带 bundle_id 但包主档缺失（ensure
    偶发失败=瞬时写锁/网络抖动）→ 补抓。bundle_known 使这类条目不再进 sync，
    不补就永远缺主档（捆绑包卡无价无图）。"""
    from app.domains.bundles.refresh import heal_bundle_refs

    async with get_session_factory()() as s:
        refs = (
            await s.execute(
                select(FanaticalBundleGame.bundle_id)
                .where(FanaticalBundleGame.bundle_id.is_not(None))
            )
        ).scalars().all()
    healed = await heal_bundle_refs([int(r) for r in refs])
    if healed:
        logger.info("[fanatical-bundles] 联动悬空引用补抓 %d 个", healed)


async def refresh_fanatical_bundles() -> dict:
    """列表 + 档位 + 包内条目全链路（每日调度与手动触发同入口，幂等）。

    与 humble 的 24h 阈值矩阵不同：本链全量一轮只有 ≈12 个请求（档内容
    全在 Algolia 命中里，逐包只补价格），无重抓矩阵的必要；条目 appid 由
    站点数据直取（无检索欠账），attempts 账本只承载「确知非 Steam」直标。"""
    from app.crawler.utils import get_beijing_time_obj

    if _REFRESH_LOCK.locked():
        return {"ok": False, "error": "已有 Fanatical 捆绑包刷新在进行"}
    async with _REFRESH_LOCK:
        proxy = await _strategy_proxy()
        connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=60)
        async with aiohttp.ClientSession(connector=connector) as session:
            page = await _get_text(
                session, proxy, _FANATICAL_BASE + _PAGE_PATH, _page_headers()
            )
            key = extract_search_key(page or "")
            if not key:
                return {"ok": False, "error": "searchKey 提取失败（页面结构变动？）"}
            hits = await _algolia_query(session, proxy, key)
            rows = parse_hits(hits)
            if not rows:
                return {"ok": False, "error": "Algolia 无游戏类捆绑包命中"}
            await _upsert_bundles(rows)

            pick_prices = parse_pick_prices(
                await _get_json(session, proxy, _FANATICAL_BASE + _ALL_PATH, _page_headers())
                or {}
            )
            now = get_beijing_time_obj().replace(tzinfo=None)
            synced = 0
            for row in rows:
                prices = await _tier_prices_for(session, proxy, row, pick_prices)
                tiers = merge_tiers(row, prices)
                if tiers:
                    async with write_gate(WritePriority.BACKGROUND, label="fanatical_tiers"), \
                            get_session_factory()() as s:
                        await s.execute(
                            update(FanaticalBundle).where(FanaticalBundle.slug == row["slug"])
                            .values(
                                tiers_json=json.dumps(tiers, ensure_ascii=False),
                                updated_at=now,
                            )
                        )
                        await s.commit()
                synced += await _sync_bundle_games(session, proxy, row["slug"], row)
                await asyncio.sleep(0.3)
        # 刷新尾跑一轮收录扫描（与 humble 同构）：本轮解析出的未入目录 appid
        # 批量登记首爬；收录链独立于抓取，调度器另有每日一拍
        ingest = await scan_missing_ingest()
        await _heal_bundle_refs()
        logger.info("[fanatical-bundles] 刷新完成：列表 %d 包（条目 %d，收录 %d）",
                    len(rows), synced, ingest.get("queued", 0))
        return {"ok": True, "listed": len(rows), "items": synced, "ingest": ingest}


async def start_refresh() -> dict:
    """后台启动一轮刷新（HTTP 立即返回；进度轮询 bundles 接口的 running）。"""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return {"ok": True, "started": False, "running": True}
    _refresh_task = asyncio.create_task(refresh_fanatical_bundles())
    return {"ok": True, "started": True, "running": True}


_reset_task: asyncio.Task | None = None


async def reset_bundle(slug: str) -> dict:
    """单包删数据 + 重抓（0.5.1）：条目行/档位清零 → 后台单包重抓。

    包主档保留（slug/名称/链接/封面 = 列表级元数据）；acked_at 保留（重抓
    不复活 NEW 徽章）。重抓串行于 _REFRESH_LOCK；站点已下架/Algolia 无命中
    时数据保持清空（如实反馈，等下一轮列表刷新裁定去留）。"""
    global _reset_task
    if _REFRESH_LOCK.locked():
        return {"ok": False, "error": "已有 Fanatical 捆绑包刷新在进行"}
    if _reset_task is not None and not _reset_task.done():
        return {"ok": False, "error": "已有 Fanatical 单包重抓在进行"}
    async with write_gate(WritePriority.BACKGROUND, label="fanatical_reset"), \
            get_session_factory()() as s:
        bundle = await s.get(FanaticalBundle, slug)
        if bundle is None:
            return {"ok": False, "error": "包不存在"}
        await s.execute(
            delete(FanaticalBundleGame).where(FanaticalBundleGame.slug == slug)
        )
        await s.execute(
            update(FanaticalBundle).where(FanaticalBundle.slug == slug)
            .values(tiers_json=None, game_count=0)
        )
        await s.commit()
    # 强引用登记（防 GC 静默回收，同 HB 0.6.0 修）
    _reset_task = asyncio.create_task(_reset_refetch(slug))
    return {"ok": True, "started": True}


async def _reset_refetch(slug: str) -> None:
    """单包重抓后台任务：Algolia 单包命中 → 价格 + 档位 + 条目重建
    （与全量刷新同链同语义，成员清单联动含在内）；尾跑一轮收录扫描。"""
    if _REFRESH_LOCK.locked():
        logger.info("[fanatical-bundles] 单包重抓跳过（全量刷新进行中，会顺带重建）: %s", slug)
        return
    async with _REFRESH_LOCK:
        proxy = await _strategy_proxy()
        connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=60)
        async with aiohttp.ClientSession(connector=connector) as session:
            page = await _get_text(session, proxy, _FANATICAL_BASE + _PAGE_PATH, _page_headers())
            key = extract_search_key(page or "")
            if not key:
                logger.warning("[fanatical-bundles] 单包重抓失败（searchKey 提取）: %s", slug)
                return
            rows = parse_hits(await _algolia_query(session, proxy, key))
            row = next((r for r in rows if r["slug"] == slug), None)
            if row is None:
                logger.info("[fanatical-bundles] 单包重抓：站点已不在售，保持清空: %s", slug)
                return
            pick_prices = parse_pick_prices(
                await _get_json(session, proxy, _FANATICAL_BASE + _ALL_PATH, _page_headers())
                or {}
            )
            prices = await _tier_prices_for(session, proxy, row, pick_prices)
            tiers = merge_tiers(row, prices)
            now = datetime.utcnow()
            if tiers:
                async with write_gate(WritePriority.BACKGROUND, label="fanatical_reset_tiers"), \
                        get_session_factory()() as s:
                    await s.execute(
                        update(FanaticalBundle).where(FanaticalBundle.slug == slug)
                        .values(tiers_json=json.dumps(tiers, ensure_ascii=False), updated_at=now)
                    )
                    await s.commit()
            await _sync_bundle_games(session, proxy, slug, row)
        ingest = await scan_missing_ingest()
        await _heal_bundle_refs()
        logger.info("[fanatical-bundles] 单包重抓完成: %s（收录 %d）", slug, ingest.get("queued", 0))


# ── 收录链（与 humble 域同型：appid 已解析、目录缺行 → 批量首爬）──

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
            [{"scope": "appids", "appids": ids, "kind": "fanatical_backfill"}]
        )
    except Exception:  # noqa: BLE001
        logger.info("[fanatical-ingest] %s 首爬登记跳过（%d 款留待下拍）", source, len(ids))
        return 0
    if not results:
        logger.info("[fanatical-ingest] %s 爬虫占用跳过本批（%d 款留待下拍）", source, len(ids))
        return 0
    logger.info("[fanatical-ingest] %s 登记首爬 %d 款", source, len(ids))
    return len(ids)


async def _ingest_candidates() -> list[int]:
    from app.domains.games.models import Game
    async with get_session_factory()() as s:
        rows = (
            await s.execute(
                select(FanaticalBundleGame.appid, FanaticalBundle.first_seen_at)
                .join(FanaticalBundle, FanaticalBundle.slug == FanaticalBundleGame.slug)
                .where(
                    FanaticalBundle.on_sale.is_(True),
                    FanaticalBundleGame.appid.is_not(None),
                )
            )
        ).all()
        freshness: dict[int, object] = {}
        for a, seen_at in rows:
            a = int(a)
            cur = freshness.get(a)
            if seen_at is not None and (cur is None or seen_at > cur):
                freshness[a] = seen_at
        # 0.6.0：站点合辑展开出的成员 appid 同样入候选（成员有真身条目，
        # 不进目录就永远没卡片——抽屉合辑行的价格/封面全依赖这个）
        pack_rows = (
            await s.execute(
                select(FanaticalBundleGame.member_appids_json, FanaticalBundle.first_seen_at)
                .join(FanaticalBundle, FanaticalBundle.slug == FanaticalBundleGame.slug)
                .where(
                    FanaticalBundle.on_sale.is_(True),
                    FanaticalBundleGame.member_appids_json.is_not(None),
                )
            )
        ).all()
        for raw, seen_at in pack_rows:
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                continue
            for m in parsed if isinstance(parsed, list) else []:
                a = m.get("appid") if isinstance(m, dict) else None
                if isinstance(a, int) and a > 0:
                    cur = freshness.get(a)
                    if cur is None or (seen_at is not None and seen_at > cur):
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
    missing.sort()
    missing.sort(
        key=lambda a: freshness[a] if freshness[a] is not None else datetime.min,  # type: ignore[arg-type,return-value]
        reverse=True,
    )
    return missing


async def scan_missing_ingest() -> dict:
    """目录收录扫描（与 humble 同语义，kind=fanatical_backfill）。"""
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
    except Exception:  # noqa: BLE001 —— 汇总数读失败不阻断列表
        ingest_pending = 0
    async with get_session_factory()() as s:
        rows = (
            await s.execute(
                select(FanaticalBundle).order_by(
                    FanaticalBundle.end_at.asc().nulls_last(), FanaticalBundle.slug
                )
            )
        ).scalars().all()
    now = get_beijing_time_obj().replace(tzinfo=None)
    items = [
        {
            "slug": r.slug, "kind": r.kind, "name": r.name, "image": r.image, "url": r.url,
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
    """点开包 = 已读：清 NEW 徽章（幂等）。"""
    from app.crawler.utils import get_beijing_time_obj

    now = get_beijing_time_obj().replace(tzinfo=None)
    async with write_gate(WritePriority.BACKGROUND, label="fanatical_seen"), \
            get_session_factory()() as s:
        bundle = await s.get(FanaticalBundle, slug)
        if bundle is None:
            return False
        if bundle.acked_at is None:
            bundle.acked_at = now
            await s.commit()
    return True


async def bundle_detail(slug: str) -> dict | None:
    """单包详情：包信息 + 包内游戏（pending 五态）+ 档位。

    档位语义随 kind：
    - tiered：档间**独立集合**（非 HB 式累进）——games = 该档全部条目，
      unlockPriceMap = 该档价；
    - pick：数量档 × 全池——每档 games = 池子全部，unlockPriceMap = 最低档价。"""
    from app.domains.games import service as games_service
    from app.domains.games.models import Game

    async with get_session_factory()() as s:
        bundle = await s.get(FanaticalBundle, slug)
        if bundle is None:
            return None
        rows = (
            await s.execute(
                select(
                    FanaticalBundleGame.appid, FanaticalBundleGame.title,
                    FanaticalBundleGame.resolve_attempts, FanaticalBundleGame.bundle_id,
                    FanaticalBundleGame.member_appids_json,
                ).where(FanaticalBundleGame.slug == slug)
            )
        ).all()
        appids = [int(a) for a, _t, _n, _b, _m in rows if a is not None]
        have: set[int] = set()
        if appids:
            have = {
                r[0] for r in await s.execute(
                    select(Game.appid).where(Game.appid.in_(appids))
                )
            }
    # 站点合辑成员清单（0.5.1）：JSON 失败/为空一律回 None（宁缺勿猜）

    def _members(raw: str | None) -> list[dict] | None:
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(parsed, list) or not parsed:
            return None
        return [
            {"title": str(m.get("title") or ""), "appid": m.get("appid")}
            for m in parsed if isinstance(m, dict) and m.get("title")
        ] or None

    pending = [
        {
            "appid": int(a) if a is not None else None, "title": title,
            "bundleId": int(b) if b is not None else None,
            "memberAppids": _members(m),
            # 五态（0.5.1）：bundle > pack（站点合辑成员展开）> not_found >
            # ingesting > resolving
            "status": (
                "bundle"
                if a is None and b is not None
                else "pack"
                if a is None and b is None and _members(m) is not None
                else "not_found"
                if a is None and int(n or 0) >= RESOLVE_MAX_ATTEMPTS
                else "ingesting" if a is not None and int(a) not in have
                else "resolving"
            ),
        }
        for a, title, n, b, m in rows
        if a is None or int(a) not in have
    ]
    title_appid: dict[str, int | None] = {}
    title_bundle: dict[str, int | None] = {}
    title_members: dict[str, list[dict] | None] = {}
    for a, title, _n, b, m in rows:
        title_appid.setdefault(title, int(a) if a is not None else None)
        title_bundle.setdefault(title, int(b) if b is not None else None)
        title_members.setdefault(title, _members(m))

    tiers_out: list[dict] = []
    if bundle.tiers_json:
        try:
            raw_tiers = json.loads(bundle.tiers_json)
        except (ValueError, TypeError):
            raw_tiers = []
        pool_min_price: dict[str, int] | None = None
        if bundle.kind == "pick":
            prices = [
                t.get("price") for t in raw_tiers if isinstance(t, dict)
                and isinstance(t.get("price"), dict)
            ]
            pool_min_price = _min_price_map(prices)
        # pick 的池子 = 全部条目（games 表行）；tiered 的档内容 = tiers_json titles
        pool_games = [
            {"title": x, "appid": a, "bundleId": title_bundle.get(x),
             "memberAppids": title_members.get(x),
             "isNew": False, "unlockPriceMap": pool_min_price}
            for x, a in title_appid.items()
        ]
        for idx, t in enumerate(raw_tiers if isinstance(raw_tiers, list) else []):
            if not isinstance(t, dict):
                continue
            price = t.get("price") if isinstance(t.get("price"), dict) else None
            if bundle.kind == "pick":
                games = [dict(g) for g in pool_games]
                count = int(t.get("quantity") or 0)
            else:
                titles = [x for x in (t.get("titles") or []) if isinstance(x, str)]
                games = [
                    {"title": x, "appid": title_appid.get(x),
                     "bundleId": title_bundle.get(x),
                     "memberAppids": title_members.get(x), "isNew": False,
                     "unlockPriceMap": price}
                    for x in titles
                ]
                count = len(games)
            tiers_out.append({
                "id": str(t.get("id") or f"t{idx}"),
                "quantity": t.get("quantity"),
                "priceMap": price,
                "header": str(t.get("header") or ""),
                "count": count,
                "games": games,
                "newCount": 0,
            })

    payload: dict = {
        "slug": bundle.slug, "kind": bundle.kind, "name": bundle.name,
        "image": bundle.image, "url": bundle.url,
        "priceMap": _price_map_out(bundle.price_map_json),
        "startAt": bundle.start_at.isoformat() if bundle.start_at else None,
        "endAt": bundle.end_at.isoformat() if bundle.end_at else None,
        "gameCount": len(rows),
        "items": [], "total": 0, "pending": pending, "tiers": tiers_out,
    }
    # 0.6.0：合辑成员卡片集（成员 appid 非顶层条目、不在 items 里；
    # 抽屉合辑行的封面/国区价按 memberAppids.appid 从这里查）
    member_ids = sorted({
        int(m["appid"])
        for p in pending
        for m in (p.get("memberAppids") or [])
        if isinstance(m.get("appid"), int) and m["appid"] > 0
    })
    if member_ids:
        try:
            member_cards = await games_service.list_items_by_appids(member_ids)
            payload["memberItems"] = member_cards["items"]
        except Exception:  # noqa: BLE001 —— 成员卡查失败不阻断详情
            logger.exception("[fanatical-bundles] 成员卡片查询失败")
            payload["memberItems"] = []
    if appids:
        cards = await games_service.list_items_by_appids(appids)
        payload["items"] = cards["items"]
        payload["total"] = cards["total"]
    return payload
