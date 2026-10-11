"""Fanatical 捆绑包领域模型。

与 humble 域同构（包 → 包内游戏两张表），差异点：
- kind：`tiered`（档位包，档间是**独立集合**，与 HB 累进语义不同——
  0.4.0 实测 tier 间零交集）与 `pick`（选择包，全池任选 N 款按数量定价）。
- 价格是**站点原生多币种**：price_map_json / 档位 price = {币种: 分}
  （Fanatical 无 CNY，7 币种全存，前端切换展示、零折算）。
- appid 由站点数据直取（steamId），无关联检索链：steamId 缺失 = 非 Steam
  发行（drm.steam=false），resolve_attempts 直置满标「未找到」。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FanaticalBundle(Base):
    """Fanatical 捆绑包（每包一行；slug = 站点路由末段）。"""

    __tablename__ = "fanatical_bundles"

    slug: Mapped[str] = mapped_column(String(200), primary_key=True)
    # tiered = 档位包（档间独立集合）/ pick = 选择包（数量档 + 全池）
    kind: Mapped[str] = mapped_column(String(20), default="tiered")
    name: Mapped[str] = mapped_column(String(300))
    image: Mapped[str] = mapped_column(Text, default="")  # 封面直链（cdn-ext 16:9）
    url: Mapped[str] = mapped_column(String(400), default="")
    # 最低档价格 map（JSON {币种: 分}）；NULL = 尚未抓到价格
    price_map_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    on_sale: Mapped[bool] = mapped_column(Boolean, default=True)
    game_count: Mapped[int] = mapped_column(Integer, default=0)
    # 档位数据（JSON）：tiered = [{id, price{}, header, titles}]（titles=该档
    # 全部，**非累进新增**）；pick = [{id, quantity, price{}}]（池子在 games 表）
    # NULL = 旧数据未解析过档位
    tiers_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # 用户已读时刻：NULL = 本轮扫出的新包未读（NEW 徽章）
    acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FanaticalBundleGame(Base):
    """包内游戏条目（一行一款；title = Fanatical 侧名称）。"""

    __tablename__ = "fanatical_bundle_games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(
        String(200), ForeignKey("fanatical_bundles.slug", ondelete="CASCADE"), index=True
    )
    appid: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Steam 捆绑包引用（0.5.0）：检索兜底未命中 app 却命中 bundle 时落引用
    bundle_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 站点合辑成员清单（0.5.1）：合辑 SKU 详情 API bundles[].games 的成员解析
    # 结果（JSON [{"title","appid"}]，appid 可为 None=成员未解析）；NULL = 未探查
    member_appids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    # 站点数据直取链的「尝试」只用于直标：steamId 缺失/非 app = 确知非 Steam，
    # 入库即置满（≥RESOLVE_MAX_ATTEMPTS → 前端 not_found），不再有检索重试
    resolve_attempts: Mapped[int] = mapped_column(Integer, default=0)
