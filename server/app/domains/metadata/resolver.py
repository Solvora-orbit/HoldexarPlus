"""统一 Steam appid 检索引擎（0.4.0）：HB 捆绑包 / Fanatical / GMG / 月包共用。

两级检索源（0.4.0 实测定案，13 条 HB 欠账标题全部 exact 命中）：
1. storesearch（轻量 JSON，首选）——但对长标题/缺符号标题常返回热门杂讯
   （实测 "Rivals of Aether II" 返回 Dota 2/CS2 等兜底热榜）；
2. 店铺全文搜索页 /search/?term=（SteamDB 同款模糊搜索，兜底）——storesearch
   五级选不出才打：带拼写容错与相关度排序，缺冒号/副标题差异照样命中本体。

查询词链（不匹配继续降，0.3.2 语义）：整名剥符号 → 主标题段（按
冒号/间隔号/空格间隔符切首段，副标题差异不干扰检索）→ 前 2 词 → 首词。

匹配层五级：exact → clean → playtest 归一 → 前缀副标题 → 相似度 ≥0.8，
前缀/相似两级拦截原声/画集/季票等本体附属条目（storesearch 的 type 一律
app 不可用作判据，只能按名称词拦）。低于阈值宁缺勿猜——错关联比留欠账毒得多。

归一化 normalize_title：NFKD 折叠音标（VR 包常见重音标题）→ lower → 剥
™®© → 弯引号/破折号归一 → 平台/版本词剥离 → 剥全部非 \\w\\s 标点 → 空白归一。
缺冒号免疫：`A B` 与 `A: B` 清洗后同形，colon 只影响检索层不影响匹配层。
"""
from __future__ import annotations

import asyncio
import difflib
import html
import logging
import re
import unicodedata

import aiohttp

logger = logging.getLogger(__name__)

_STORESEARCH_URL = "https://store.steampowered.com/api/storesearch"
_SEARCH_URL = "https://store.steampowered.com/search/"

_STORE_HEADERS = {
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


# ── 名称归一 + 五级匹配 ──────────────────────────────────────────

_TRADEMARK_RE = re.compile(r"\s*\-\s*pc edition|\s*\(pc\)|\s*\(windows\)|\s*windows edition|\s*xbox one|\s*xbox series x\|s")
# 版本词表保持 0.3.2 基线（0.4.2 撤回 0.4.1 扩充）：带版本后缀的条目可能是
# **独立的 Steam 捆绑包/DLC SKU**（如某些 Complete Edition 是合辑商品），
# 剥掉后缀会把它错对到本体——宁可留欠账也不猜。
_EDITION_RE = re.compile(
    r"\s*standard edition|\s*game of the year edition|\s*anniversary edition|\s*complete edition"
    r"|\s*deluxe edition|\s*definitive edition|\s*remastered|\s*director's cut|\s*ultimate edition",
    re.IGNORECASE,
)

# 本体附属条目名称词（0.3.2）：模糊关联两级的前置拦截——storesearch/搜索页对
# "Disco Elysium" 会同时回 The Final Cut 本体与 Soundtrack/Artbooklet 附属，
# 模糊匹配若不加拦会按返回序把原声当本体
_NON_CORE_RE = re.compile(
    r"soundtrack|\bost\b|original score|artbooklet|art book|digital comic"
    r"|season pass|preload|\bdemo\b|\bbeta\b|\bdlc\b",
    re.IGNORECASE,
)


def normalize_title(n: str) -> str:
    """标题 → 匹配形态：NFKD 折叠音标、lower、剥商标/平台/版本词、剥标点、
    空白归一（0.4.0 起为全引擎统一基准；_clean_name 为兼容别名）。"""
    if not n:
        return ""
    n = unicodedata.normalize("NFKD", n)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = html.unescape(n)
    n = n.lower()
    n = n.replace("™", "").replace("®", "").replace("©", "")
    n = n.replace("’", "'").replace("–", "-").replace("—", "-")
    n = _TRADEMARK_RE.sub("", n)
    n = _EDITION_RE.sub("", n)
    n = n.replace("ea sports fc", "fc").replace("ea sports ", "")
    n = re.sub(r"[^\w\s]", "", n)
    return re.sub(r"\s+", " ", n).strip()


def _clean_name(n: str) -> str:
    """兼容别名（0.3.2 及此前链路、XGP 名称匹配沿用旧名）。"""
    return normalize_title(n)


def _pick_appid(title: str, hits: list[dict]) -> int | None:
    """检索命中列表 → appid：exact → 清洗名 → playtest 归一 → 前缀副标题 →
    相似度，五级匹配（前三级与 XGP 链同口径）。

    两轮扫描而非逐条短路：副标题变体（如 Deluxe Edition 被清洗层剥掉）
    不能抢在 exact 命中之前。playtest 层：捆绑包偶发给测试键（页内标题
    带 Playtest），商店侧无该条目——剥掉 playtest 词后与商店本体同形即认。
    前缀层（0.3.2）：捆绑包惯用简称而商店是全称——`Disco Elysium` 的 Steam
    本体是 `Disco Elysium - The Final Cut`；清洗名互为前缀（后接空格，
    防 disco 撞 discover）即认。模糊两级排除原声/画集/季票等外围条目。
    相似层：SequenceMatcher ≥ 0.8 收口轻微拼写差异，取分最高者；
    低于阈值宁缺勿猜——错关联比留欠账毒得多。
    """
    want = title.lower().strip()
    want_clean = normalize_title(title)
    want_pt = " ".join(t for t in want_clean.split() if t != "playtest")
    exact = clean = playtest = prefix = similar = None
    similar_score = 0.0
    for hit in hits:
        name = str(hit.get("name") or "")
        hit_id = str(hit.get("id") or "")
        if not name or not hit_id.isdigit():
            continue
        name_clean = normalize_title(name)
        if exact is None and name.lower().strip() == want:
            exact = int(hit_id)
        if clean is None and want_clean and name_clean == want_clean:
            clean = int(hit_id)
        if playtest is None and want_pt and want_pt != want_clean \
                and name_clean == want_pt:
            playtest = int(hit_id)
        # 前缀/相似两级排除外围条目：storesearch 的 type 一律 app 不可用
        # （0.3.2 实测），按名称词拦原声/画集/季票等本体附属
        if _NON_CORE_RE.search(name):
            continue
        if want_clean and prefix is None and (
            name_clean.startswith(want_clean + " ")
            or want_clean.startswith(name_clean + " ")
        ):
            prefix = int(hit_id)
        elif prefix is None:
            score = difflib.SequenceMatcher(None, want_clean, name_clean).ratio()
            if score >= 0.8 and score > similar_score:
                similar_score = score
                similar = int(hit_id)
    for pick in (exact, clean, playtest, prefix, similar):
        if pick is not None:
            return pick
    return None


# ── 检索源 ────────────────────────────────────────────────────────

# 查询词链：剥符号后的整名 → 主标题段 → 前 2 词 → 首词（非空且不重复才收）。
# 0.4.2 撤回 0.4.1 的「剥版本词整名」档：版本后缀可能是独立捆绑包 SKU，
# 剥词查询会把合辑条目错对到本体（宁缺勿猜）。主标题段 = 原题按冒号/
# 间隔号/空格间隔符切首段：副标题差异不拖累检索；"Rivals of Aether II"
# 这类尾巴带续作号的长名靠搜索页兜底而非砍词（砍尾词会撞前作）。
_QUERY_SPLIT_RE = re.compile(r"\s+[-–—]\s+|[:：|~·]")


def _query_terms(title: str) -> list[str]:
    base = re.sub(r"[^\w\s]", " ", title)
    tokens = base.split()
    out: list[str] = []

    def _add(parts: list[str] | str) -> None:
        t = " ".join(parts).strip() if not isinstance(parts, str) else parts.strip()
        if t and t not in out:
            out.append(t)

    _add(tokens)
    seg = _QUERY_SPLIT_RE.split(title or "", 1)[0]
    if seg.strip() and seg.strip() != (title or "").strip():
        _add(re.sub(r"[^\w\s]", " ", seg).split())
    if len(tokens) > 2:
        _add(tokens[:2])
    if len(tokens) > 1:
        _add(tokens[0])
    return out


async def _fetch_storesearch(
    session: aiohttp.ClientSession, term: str, proxy: str | None
) -> list[dict]:
    try:
        async with session.get(
            _STORESEARCH_URL,
            params={"term": term, "cc": "us", "l": "en"},
            headers=_STORE_HEADERS,
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
        return (data or {}).get("items") or []
    except Exception:  # noqa: BLE001
        return []


# 搜索页结果行：锚点 <a href="…/app/{id}/…" 与其后的 <span class="title">
# 按文档序配对（窗口配对在密集行会串行，序配对天然无窗歧义）
_SEARCH_ANCHOR_RE = re.compile(
    r'<a[^>]*?href="(?:https?://store\.steampowered\.com)?/app/(\d+)[^"]*"[^>]*?>'
)
# 捆绑包行（0.5.0）：同一结果页里 data-ds-bundleid 锚 + 同款 title 配对——
# 三站查不到 app 的条目名（Complete Edition 合辑等）常能按 bundle 命中
_SEARCH_BUNDLE_ANCHOR_RE = re.compile(r'data-ds-bundleid="(\d+)"')
_SEARCH_TITLE_RE = re.compile(r'<span class="title">([^<]+)</span>')


def _pair_anchors_with_titles(anchors: list[tuple[int, int]], titles: list[tuple[int, str]]) -> list[dict]:
    rows: list[dict] = []
    ti = 0
    for pos, ident in anchors:
        while ti < len(titles) and titles[ti][0] < pos:
            ti += 1
        if ti < len(titles) and titles[ti][1]:
            rows.append({"id": ident, "name": titles[ti][1]})
    return rows


def parse_search_rows(html_text: str) -> list[dict]:
    """搜索页 HTML → [{id, name}]（结果行序 = 相关度序）。"""
    anchors = [(m.start(), int(m.group(1))) for m in _SEARCH_ANCHOR_RE.finditer(html_text)]
    titles = [
        (m.start(), html.unescape(m.group(1)).strip())
        for m in _SEARCH_TITLE_RE.finditer(html_text)
    ]
    return _pair_anchors_with_titles(anchors, titles)


def parse_search_bundle_rows(html_text: str) -> list[dict]:
    """搜索页 HTML → 捆绑包行 [{id: bundleid, name}]（相关度序）。"""
    anchors = [(m.start(), int(m.group(1))) for m in _SEARCH_BUNDLE_ANCHOR_RE.finditer(html_text)]
    titles = [
        (m.start(), html.unescape(m.group(1)).strip())
        for m in _SEARCH_TITLE_RE.finditer(html_text)
    ]
    return _pair_anchors_with_titles(anchors, titles)


async def _fetch_search_page_raw(
    session: aiohttp.ClientSession, term: str, proxy: str | None
) -> str:
    try:
        async with session.get(
            _SEARCH_URL,
            params={"term": term, "cc": "us", "l": "english"},
            headers=_STORE_HEADERS,
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=25),
        ) as resp:
            if resp.status != 200:
                return ""
            return await resp.text(errors="replace")
    except Exception:  # noqa: BLE001
        return ""


async def _fetch_search_page(
    session: aiohttp.ClientSession, term: str, proxy: str | None
) -> list[dict]:
    return parse_search_rows(await _fetch_search_page_raw(session, term, proxy))


def _pick_bundleid(title: str, rows: list[dict]) -> int | None:
    """捆绑包行 → bundleid：exact → 清洗名相等，取首个。

    只做两级（bundle 行名称规范且条目少，prefix/相似会把 "X Collection"
    错对到 "X" 合辑——宁缺勿猜）。"""
    want = title.lower().strip()
    want_clean = normalize_title(title)
    for row in rows:
        name = str(row.get("name") or "")
        rid = str(row.get("id") or "")
        if not name or not rid.isdigit():
            continue
        if name.lower().strip() == want:
            return int(rid)
        if want_clean and normalize_title(name) == want_clean:
            return int(rid)
    return None


async def resolve_target(
    session: aiohttp.ClientSession, title: str, proxy: str | None
) -> tuple[int | None, int | None]:
    """条目标题 → (Steam appid, Steam bundleid)（0.5.0）。

    storesearch 阶段只产 appid（API 无 bundle）；搜索页阶段同页同时解析
    app 行与 bundle 行——查 app 顺手拿 bundle，零额外请求。**app 命中优先
    于任何更早的 bundle 命中**（bundle 只在 app 全链落空时兜底返回），
    全链失败 (None, None) 由调用方记账。"""
    for term in _query_terms(title):
        hits = await _fetch_storesearch(session, term, proxy)
        if hits:
            pick = _pick_appid(title, hits)
            if pick is not None:
                return pick, None
    bundle_pick: int | None = None
    for i, term in enumerate(_query_terms(title)):
        if i:
            await asyncio.sleep(0.5)
        page = await _fetch_search_page_raw(session, term, proxy)
        if not page:
            continue
        pick = _pick_appid(title, parse_search_rows(page))
        if pick is not None:
            return pick, bundle_pick
        if bundle_pick is None:
            bundle_pick = _pick_bundleid(title, parse_search_bundle_rows(page))
    return None, bundle_pick


async def resolve_appid(
    session: aiohttp.ClientSession, title: str, proxy: str | None
) -> int | None:
    """条目标题 → Steam appid（两级检索源；全链失败返回 None 由调用方记账）。

    第一源 storesearch 按词链逐档打；五级选不出才进第二源搜索页（HTTP 层
    失败视作空命中继续降档/换源，不做单点短路）。0.5.0 起为 resolve_target
    的 appid 侧委托。"""
    appid, _bundle = await resolve_target(session, title, proxy)
    return appid
