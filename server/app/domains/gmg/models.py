"""GreenManGaming 捆绑包领域模型。

与 humble 域同构（包 → 包内游戏两张表 + 关联尝试账本）：GMG 详情 HTML
**不带 steam appid**，包内游戏全走统一检索引擎（metadata.resolver）按名
关联，attempts/not_found 三态语义与 HB 完全一致。

价格是站点原生多币种（CN 出口 CN¥，代理出口可能 $）——price_map_json /
档位 price = {币种: 分}，多币种全存不折算，展示由前端切换。
档位是 **PWY 累进**（买高档拿全部低档内容，与 HB 累进同语义）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GmgBundle(Base):
    """GMG 捆绑包（每包一行；slug = 子站路由末段）。"""

    __tablename__ = "gmg_bundles"

    slug: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    image: Mapped[str] = mapped_column(Text, default="")  # 封面直链（主站媒体 CDN）
    url: Mapped[str] = mapped_column(String(400), default="")
    # 最低档价格 map（JSON {币种: 分}）；NULL = 尚未抓到价格
    price_map_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    on_sale: Mapped[bool] = mapped_column(Boolean, default=True)
    game_count: Mapped[int] = mapped_column(Integer, default=0)
    # 档位数据（JSON）：[{id, header, price{}, titles, is_initial}]（titles=本档新增）
    # NULL = 尚未解析过档位
    tiers_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GmgBundleGame(Base):
    """包内游戏条目（一行一款；title = GMG 侧名称，appid 走统一检索引擎）。"""

    __tablename__ = "gmg_bundle_games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(
        String(200), ForeignKey("gmg_bundles.slug", ondelete="CASCADE"), index=True
    )
    appid: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    # 统一检索引擎关联尝试计数：≥RESOLVE_MAX_ATTEMPTS → 前端标「未找到」
    resolve_attempts: Mapped[int] = mapped_column(Integer, default=0)
