"""family 域：/family/library 无显式目标时的默认组账号解析（plus.3）。

gamelib 家庭/游玩页签不带 target 调 /family/library：此前固定主账号，
主账号不在家庭组里时成员计数恒为 0（family 页自己选了已加入组所以
显示正常，两边口径劈叉）。修复后优先取最近同步的已加入组
（member_count>0，与 family 页「已加入组」同一判定），无已加入组
才回落主账号。三个场景各自独立库，互不共享状态。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domains.family import service as family_service  # noqa: E402
from app.domains.family.models import FamilyGroup  # noqa: E402

PRIMARY = "76561198000000001"
OTHER = "76561198000000002"


def _make_db(tmp_path, monkeypatch):
    """独立临时库（async 引擎 + create_all），factory 打桩进 family 服务。"""
    from app.core.database import Base

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'sid.db').as_posix()}", echo=False
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(family_service, "get_session_factory", lambda: factory)
    return factory, engine, Base


@pytest.fixture
def db(tmp_path, monkeypatch):
    return _make_db(tmp_path, monkeypatch)


@pytest_asyncio.fixture(autouse=True)
async def _patch_primary(monkeypatch):
    """主账号打桩：固定 PRIMARY，验证默认组解析不再无条件依赖它。"""
    async def fake_primary():
        return PRIMARY

    monkeypatch.setattr(family_service, "get_primary_steamid", fake_primary)
    yield


@pytest_asyncio.fixture(autouse=True)
async def _init_db(db):
    _factory, engine, base = db
    async with engine.begin() as conn:
        await conn.run_sync(base.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_no_group_rows_falls_back_primary(db):
    """无任何组行：回落主账号（db fixture 负责打桩 factory + 建表）。"""
    assert await family_service._default_library_sid() == PRIMARY


@pytest.mark.asyncio
async def test_unjoined_group_still_falls_back_primary(db):
    """组行存在但未加入（member_count=0）：仍回落主账号。"""
    factory, _engine, _base = db
    async with factory() as session:
        session.add(FamilyGroup(
            steamid=PRIMARY, family_groupid=None, member_count=0,
            updated_at=datetime(2026, 10, 1),
        ))
        await session.commit()
    assert await family_service._default_library_sid() == PRIMARY


@pytest.mark.asyncio
async def test_joined_group_is_selected(db):
    """已加入组（member_count>0）：选中它，而非固定主账号；
    多组并存时取最近同步（updated_at 新）的那一组。"""
    factory, _engine, _base = db
    async with factory() as session:
        session.add(FamilyGroup(
            steamid=PRIMARY, family_groupid="g1", family_name="Solo",
            member_count=0, updated_at=datetime(2026, 10, 1),
        ))
        session.add(FamilyGroup(
            steamid=OTHER, family_groupid="g2", family_name="Joined",
            member_count=3, updated_at=datetime(2026, 10, 2),
        ))
        await session.commit()
    assert await family_service._default_library_sid() == OTHER
