"""crawl 域单元测试：discounted scope（库内折扣中游戏，tmp sqlite）。

覆盖链路：
- resolve_scope_appids("discounted")：任一地区 discount_percent>0 的在库行入选；
- 排除门与 catalog 同口径（下架宽限期外 removed_at / 永久免费 free_kind 不入列）；
- 排序：跨地区最大折扣率降序 → appid 升序；
- 空库语义：返回空列表，上层 start_job 以「任务列表为空」拒绝启动。
"""
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.domains.crawl import service as crawl_service


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.core.database as database_module

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}", echo=False
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(crawl_service, "get_session_factory", lambda: factory)
    return factory


@pytest_asyncio.fixture(autouse=True)
async def _schema(db):
    import app.domains.games.models  # noqa: F401

    async with db.kw["bind"].begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_game(db, appid: int, *, name: str, removed: bool = False, free_kind: str | None = None):
    from app.crawler.utils import get_beijing_time_obj
    from app.domains.games.models import Game

    now = get_beijing_time_obj().replace(tzinfo=None)
    async with db() as session:
        session.add(Game(
            appid=appid, name=name, created_at=now, updated_at=now,
            removed_at=now if removed else None, free_kind=free_kind,
        ))
        await session.commit()


async def _seed_price(db, appid: int, region: str, discount: int):
    from app.crawler.utils import get_beijing_time_obj
    from app.domains.games.models import GameCurrentPrice

    now = get_beijing_time_obj().replace(tzinfo=None)
    async with db() as session:
        session.add(GameCurrentPrice(
            appid=appid, region_code=region, currency="CNY", price=1000,
            discount_percent=discount, price_status="ok", updated_at=now,
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_discounted_scope_selection_and_order(db):
    """折扣中 = 任一地区 discount_percent>0 的在库行；跨区取最大折扣排序。"""
    await _seed_game(db, 400, name="五折")
    await _seed_game(db, 300, name="三折")
    await _seed_game(db, 200, name="原价")
    await _seed_game(db, 500, name="跨区七折")
    await _seed_game(db, 900, name="已下架打折", removed=True)
    await _seed_game(db, 910, name="限时赠送打折", free_kind="promo")
    await _seed_price(db, 400, "CN", 50)
    await _seed_price(db, 300, "CN", 30)
    await _seed_price(db, 200, "CN", 0)
    await _seed_price(db, 500, "CN", 10)
    await _seed_price(db, 500, "US", 70)   # 跨区最大折扣 70，排头
    await _seed_price(db, 900, "CN", 90)   # 已下架 → 排除
    await _seed_price(db, 910, "CN", 80)   # 限时赠送（promo）→ 排除

    out = await crawl_service.resolve_scope_appids("discounted", None)
    assert [a for a, _ in out] == [500, 400, 300], (
        "期望 [500(70%) → 400(50%) → 300(30%)]；原价/下架/赠送不入列，"
        f"实际 {out}"
    )


@pytest.mark.asyncio
async def test_discounted_scope_same_discount_stable_appid(db):
    """同折扣率按 appid 升序稳定排（pairs 序 = worker 消费序，必须确定）。"""
    await _seed_game(db, 820, name="甲")
    await _seed_game(db, 810, name="乙")
    await _seed_price(db, 820, "CN", 40)
    await _seed_price(db, 810, "CN", 40)
    out = await crawl_service.resolve_scope_appids("discounted", None)
    assert [a for a, _ in out] == [810, 820]


@pytest.mark.asyncio
async def test_discounted_scope_empty(db):
    """库内无折扣行 → 空列表（上层 start_job 以「任务列表为空」拒绝启动）。"""
    assert await crawl_service.resolve_scope_appids("discounted", None) == []
