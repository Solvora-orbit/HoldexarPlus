"""本地 bundles 语料加载（0.5.1 bundle 兜底数据源）。

爬虫扫 app 店面页时从购买选项发现的 Steam 捆绑包/Sub 实名（bundles 表，
随爬取持续增长）。Steam 已把 bundle 行移出搜索结果（0.5.0 的搜索页
data-ds-bundleid 解析实网 6 名 0 命中，已撤），本地语料是零请求、实名
权威的名称→bundleid 唯一可得源；覆盖随爬取面增长，未覆盖的标题走
「未找到」（宁缺勿猜不变）。语料含 Sub 形态行：bundle/sub 数字空间并存，
下游 ensure_bundle_stored 的双轨抓取自会落到正确形态。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_BUNDLE_CORPUS_TTL = 3600.0
_bundle_corpus: list[dict] | None = None
_bundle_corpus_at: float = 0.0


def invalidate_bundle_corpus() -> None:
    """语料缓存失效（测试 / bundles 表批量导入后手动触发）。"""
    global _bundle_corpus, _bundle_corpus_at
    _bundle_corpus = None
    _bundle_corpus_at = 0.0


async def load_bundle_corpus() -> list[dict]:
    """bundles 表 → [{id, name}]（进程内 TTL 缓存 1h；查询失败沿用旧缓存）。"""
    global _bundle_corpus, _bundle_corpus_at
    if _bundle_corpus is not None and time.monotonic() - _bundle_corpus_at < _BUNDLE_CORPUS_TTL:
        return _bundle_corpus
    try:
        from sqlalchemy import select

        from app.core.database import get_session_factory
        from app.domains.games.models import Bundle

        async with get_session_factory()() as s:
            stmt = select(Bundle).where(Bundle.name.is_not(None))
            objs = (await s.scalars(stmt)).all()
        corpus = [{"id": b.bundle_id, "name": str(b.name)} for b in objs]
    except Exception:  # noqa: BLE001 —— 库未就绪/查询失败不阻断解析链
        logger.exception("[resolver] bundles 语料加载失败（沿用旧缓存）")
        return _bundle_corpus if _bundle_corpus is not None else []
    _bundle_corpus = corpus
    _bundle_corpus_at = time.monotonic()
    return corpus
