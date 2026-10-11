"""GMG 捆绑包域路由：在售包列表 / 单包详情 / 手动刷新（与 humble 同构）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.domains.gmg import service

router = APIRouter(prefix="/gmg", tags=["gmg"])


@router.get("/bundles")
async def bundles() -> dict:
    """在售 GMG 捆绑包列表（纯本地读；running 供前端轮询刷新进度）。"""
    return await service.list_bundles()


@router.get("/bundles/{slug}")
async def bundle_detail(slug: str) -> dict:
    """单包详情 + 包内游戏条目（pending 三态 + 累进档位展开）。"""
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
    """后台启动一轮刷新（立即返回；每日调度同入口，幂等）。"""
    return await service.start_refresh()


@router.post("/bundles/{slug}/reset")
async def reset_bundle(slug: str) -> dict:
    """单包删数据 + 重抓（0.5.1）：清条目/档位后后台单包重建，立即返回。
    未知 slug 404；刷新进行中 409。"""
    res = await service.reset_bundle(slug)
    if not res.get("ok"):
        err = str(res.get("error"))
        code = 409 if ("刷新" in err or "重抓" in err) else 404
        raise HTTPException(status_code=code, detail=err)
    return res


@router.post("/bundles/ingest-now")
async def ingest_now() -> dict:
    """立即跑一轮目录收录扫描（后台分批首爬，立即返回）。"""
    return await service.start_ingest_now()
