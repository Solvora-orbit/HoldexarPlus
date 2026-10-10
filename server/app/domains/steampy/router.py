"""Steampy 行情域路由（0.5.0 实验性）：实验开关关闭时不服务数据。"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.domains.steampy import service

router = APIRouter(prefix="/steampy", tags=["steampy"])


@router.get("/hot")
async def hot(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=50),
) -> dict:
    """游戏 CDK 行情单页（实验性）：experimental.enabled 关闭时返回 enabled=false
    语义载荷（前端隐藏分区，不给数据）。"""
    if not await service.is_enabled():
        return {"enabled": False, "items": [], "page": page, "pageSize": pageSize}
    payload = await service.fetch_hot_page(page=page, page_size=pageSize)
    if payload is None:
        return {"enabled": True, "ok": False, "items": [],
                "page": page, "pageSize": pageSize}
    return {"enabled": True, "ok": True, **payload}
