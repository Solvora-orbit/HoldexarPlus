"""system 域单元测试：wipe-data（删除本地全部数据，tmp sqlite）。

覆盖链路：
- confirm 逐字校验：非 "DELETE" 一律 400 拒绝（防脚本/误触双保险的后端半边）；
- 正常清空：drop_all+create_all 后业务表为空，且重置后的 schema 可继续写入。
"""
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domains.system import router as system_router
from app.domains.system.router import WipeData, wipe_data


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.core.database as database_module

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}", echo=False
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(system_router, "get_session_factory", lambda: factory)
    # wipe 的 drop_all/create_all 与 VACUUM 都走 engine.begin()/engine.url：
    # 打桩到测试引擎，绝不触碰真实库
    monkeypatch.setattr(system_router, "get_engine", lambda: engine)
    monkeypatch.setattr("app.crawler.occupancy.crawler_busy", lambda: False)
    return factory


@pytest_asyncio.fixture(autouse=True)
async def _schema(db):
    import app.domains.games.models  # noqa: F401

    from app.core.database import Base

    async with db.kw["bind"].begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_game(db):
    from app.crawler.utils import get_beijing_time_obj
    from app.domains.games.models import Game

    now = get_beijing_time_obj().replace(tzinfo=None)
    async with db() as session:
        session.add(Game(appid=620, name="Portal 2", created_at=now, updated_at=now))
        await session.commit()


@pytest.mark.asyncio
async def test_wipe_rejects_wrong_confirm():
    with pytest.raises(HTTPException) as ei:
        await wipe_data(WipeData(confirm="delete"))
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_wipe_clears_all_and_schema_stays_usable(db):
    from app.domains.games.models import Game

    await _seed_game(db)
    res = await wipe_data(WipeData(confirm="DELETE"))
    assert res == {"wiped": True}
    async with db() as session:
        rows = (await session.scalars(select(Game))).all()
    assert rows == [], "清空后业务表必须为空"
    # 重置后的 schema 必须可继续写入（create_all 重建），应用无需重启即不崩
    await _seed_game(db)
    async with db() as session:
        again = (await session.scalars(select(Game))).all()
    assert len(again) == 1
