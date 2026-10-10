"""Steampy（steampy.com，游戏 CDK 市场）行情代理（0.5.0 实验性）。

调研定案（2026-10-11，见 docs/HANDOFF.md「Steampy 调研」）：
- 纯前端渲染 Vue SPA，无 SSR/内嵌 JSON，数据全走同源网关
  `https://steampy.com/xboot/<Module>/<action>`（xboot 框架，响应统一
  {"success", "message", "code", "result"} 包裹）。
- **无账号可稳定抓**：`GET /xboot/steamCommon/listHot?pageNumber=&pageSize=`
  （全量游戏 CDK 行情：gameName/appId/oriPrice/gamePrice/hisPrice/discount/
  keyPrice 最低 CDK 价/keySales 在售数/keyAveAmt 成交均价/gameAva 封面，
  人民币计价）。需登录的端点（卖家明细/目录搜索/下单）本轮不接。
- 反爬：无 Cloudflare/UA 校验；网关限速温和（实测 50+ 连发瞬断一次），
  代理层限 1 req/s + 失败退避；负载均衡粘性 cookie SERVERID 用 CookieJar
  保持；**SPA fallback 会把 404 吞成 200+HTML 壳**——必须校验响应
  Content-Type 为 JSON（壳是 text/html），非法即按失败计。

实验边界：数据是站点公开行情的透传缓存，不落库不监控；端点为社区逆向
所得，随前端改版可能失效（前端 bundle hash 见调研记录）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

_STEAMPY_BASE = "https://steampy.com"
_LIST_HOT_PATH = "/xboot/steamCommon/listHot"
_ALLOWED_HOSTS = frozenset({"steampy.com"})
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MIN_INTERVAL = 1.0  # 实测 50+ 连发会触发 TCP 瞬断，1 req/s 礼貌限速

_LAST_REQUEST_TS = 0.0
_CACHE: dict[int, tuple[float, list[dict]]] = {}  # page -> (ts, items)
_CACHE_TTL = 60.0

_session: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=2, ttl_dns_cache=300),
            cookie_jar=aiohttp.CookieJar(),  # SERVERID 粘性
        )
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


async def is_enabled() -> bool:
    from app.domains.settings import service as settings_service

    return bool(await settings_service.get_value("experimental.enabled", False))


async def _throttle() -> None:
    global _LAST_REQUEST_TS
    wait = _MIN_INTERVAL - (time.monotonic() - _LAST_REQUEST_TS)
    if wait > 0:
        await asyncio.sleep(wait)
    _LAST_REQUEST_TS = time.monotonic()


def _normalize_item(raw: dict) -> dict | None:
    """listHot 条目 → 前端行情行；关键字段缺失丢弃。"""
    name = str(raw.get("gameName") or "").strip()
    app_id = str(raw.get("appId") or "").strip()
    if not name or not app_id.isdigit():
        return None
    try:
        return {
            "gameName": name,
            "appId": int(app_id),
            "oriPrice": round(float(raw.get("oriPrice") or 0), 2),
            "gamePrice": round(float(raw.get("gamePrice") or 0), 2),
            "hisPrice": round(float(raw.get("hisPrice") or 0), 2),
            "discount": round(float(raw.get("discount") or 0), 4),
            "keyPrice": round(float(raw.get("keyPrice") or 0), 2),
            "keySales": int(raw.get("keySales") or 0),
            "keyAveAmt": round(float(raw.get("keyAveAmt") or 0), 2),
            "gameAva": str(raw.get("gameAva") or ""),
        }
    except (TypeError, ValueError):
        return None


async def fetch_hot_page(page: int = 1, page_size: int = 20) -> dict | None:
    """listHot 单页代理：`{items, page, pageSize, total}`；失败/未启用 None。"""
    page = max(1, min(int(page), 500))
    page_size = max(1, min(int(page_size), 50))
    cached = _CACHE.get(page)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL:
        return {"items": cached[1], "page": page, "pageSize": page_size}

    parsed = urlparse(_STEAMPY_BASE + _LIST_HOT_PATH)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None
    session = await _get_session()
    payload = None
    for attempt in range(2):  # TCP 瞬断重试一次
        await _throttle()
        try:
            async with session.get(
                _STEAMPY_BASE + _LIST_HOT_PATH,
                params={"pageNumber": page, "pageSize": page_size},
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    continue
                # SPA fallback 防：壳响应是 text/html，行情必须是 JSON
                if "application/json" not in (resp.headers.get("Content-Type") or ""):
                    logger.warning("[steampy] listHot 返回非 JSON（SPA 壳/改版）")
                    return None
                body = await resp.json(content_type=None)
            if isinstance(body, dict) and body.get("success"):
                payload = body.get("result")
                break
        except Exception:  # noqa: BLE001
            if attempt == 0:
                await asyncio.sleep(3)
    if not isinstance(payload, dict):
        return None
    items = [
        item for item in (_normalize_item(r) for r in payload.get("list") or [])
        if item is not None
    ]
    _CACHE[page] = (now, items)
    total = int(payload.get("total") or 0)
    return {"items": items, "page": page, "pageSize": page_size, "total": total}
