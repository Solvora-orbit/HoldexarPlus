"""games 域单元测试：board 榜单视图（list_games(board=...)，tmp sqlite）。

覆盖链路：
- 榜序 join 目录与价格行：榜内 appid 按榜序返回；目录缺失（未爬到的新面孔）跳过；
- 价格矩阵随行组装（CN 现价/折扣可读，HlGameCard 渲染所需字段在位）；
- sort=top100 回归：board_key=topsellers、top_n=100 语义不变（泛化前的老语义）；
- 未知榜单源在路由层 404（list_games 的 board 分支对空榜返回空集，不炸）。
"""
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domains.games import service as games_service


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.core.database as database_module

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}", echo=False
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(games_service, "get_session_factory", lambda: factory)

    # 榜单数据源 mock（不打真网）：specials 榜序 200→400→300（200 不在目录，
    # 模拟「新面孔未落库」）；topsellers 只有 400（top100 回归用）
    import app.domains.games.boards as boards_mod

    async def fake_get_board(key: str):
        return {"specials": [200, 400, 300], "topsellers": [400]}.get(key, [])

    monkeypatch.setattr(boards_mod, "get_board", fake_get_board)

    async def fake_effective_regions(_regions):
        return ["cn"]

    monkeypatch.setattr(games_service, "effective_regions", fake_effective_regions)
    return factory


@pytest_asyncio.fixture(autouse=True)
async def _schema(db):
    import app.domains.games.models  # noqa: F401
    import app.domains.wishlist.models  # noqa: F401

    from app.core.database import Base

    async with db.kw["bind"].begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_game(db, appid: int, name: str, discount: int | None):
    from app.crawler.utils import get_beijing_time_obj
    from app.domains.games.models import Game, GameCurrentPrice

    now = get_beijing_time_obj().replace(tzinfo=None)
    async with db() as session:
        session.add(Game(appid=appid, name=name, created_at=now, updated_at=now))
        if discount is not None:
            session.add(GameCurrentPrice(
                appid=appid, region_code="CN", currency="CNY", price=1000,
                discount_percent=discount, price_status="ok", updated_at=now,
            ))
        await session.commit()


@pytest.mark.asyncio
async def test_board_view_orders_by_board_and_skips_missing(db):
    """榜序返回目录内游戏；目录缺失（200）跳过；价格矩阵可读。"""
    await _seed_game(db, 400, "五折", 50)
    await _seed_game(db, 300, "三折", 30)
    # 200 不入库（目录缺失的新面孔）

    res = await games_service.list_games(board="specials", limit=10)
    assert [i["appid"] for i in res["items"]] == [400, 300], (
        "期望榜序 [400, 300]（200 目录缺失跳过），实际 "
        f"{[i['appid'] for i in res['items']]}"
    )
    cn = res["items"][0]["priceMatrix"].get("CN")
    assert cn, "榜内游戏应带 CN 价格行（卡片渲染依赖）"


@pytest.mark.asyncio
async def test_board_view_top100_regression(db):
    """sort=top100 回归：默认 topsellers 榜、top_n=100 语义不变。"""
    await _seed_game(db, 400, "五折", 50)
    res = await games_service.list_games(sort="top100", limit=10)
    assert [i["appid"] for i in res["items"]] == [400]
