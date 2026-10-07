"""HB 捆绑包域路由：在售包列表 / 单包详情（包内游戏比价）/ 手动刷新。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.domains.humble import service

router = APIRouter(prefix="/humble", tags=["humble"])


@router.get("/bundles")
async def bundles() -> dict:
    """在售 HB 捆绑包列表（纯本地读，零外网；running 供前端轮询刷新进度）。"""
    return await service.list_bundles()


@router.get("/bundles/{slug}")
async def bundle_detail(slug: str) -> dict:
    """单包详情 + 包内游戏条目（/games 同款卡片载荷；pending = 收录中条目）。
    未知 slug 404。"""
    payload = await service.bundle_detail(slug)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"未知捆绑包: {slug}")
    return payload


@router.post("/bundles/{slug}/seen")
async def mark_seen(slug: str) -> dict:
    """点开包 = 已读（清 NEW 徽章，幂等）。未知 slug 404。"""
    if not await service.mark_seen(slug):
        raise HTTPException(status_code=404, detail=f"未知捆绑包: {slug}")
    return {"ok": True}


@router.post("/refresh")
async def refresh() -> dict:
    """后台启动一轮列表+详情刷新（立即返回；每日调度同入口，幂等）。"""
    return await service.start_refresh()


@router.post("/bundles/ingest-now")
async def ingest_now() -> dict:
    """立即跑一轮目录收录扫描（后台分批首爬，立即返回；bundles 端点
    ingestRunning/ingestPending 轮询收尾）。"""
    return await service.start_ingest_now()
