"""HB 捆绑包（Humble Bundle 商店包）领域模型。

与 HB 月包（membership 月包，games.is_hb/hb_data 标记）分开：商店捆绑包是
**独立在售商品**，有封面、定价、档期与包内游戏清单，单立两张贴合
「包 → 包内游戏」的形态。包内游戏 appid 解析走 metadata 的 storesearch
链路；未解析条目 appid 留空，下轮补跑重试。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HumbleBundle(Base):
    """商店捆绑包（每包一行；slug = product_url 末段）。"""

    __tablename__ = "humble_bundles"

    slug: Mapped[str] = mapped_column(String(160), primary_key=True)
    machine_name: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(300))
    image: Mapped[str] = mapped_column(Text, default="")  # 封面（imgix 直链）
    url: Mapped[str] = mapped_column(String(400), default="")  # 详情页绝对 URL（白名单域拼接）
    price_cny_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 包价（最低档，分）
    discount_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # 页面折扣（%）
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    on_sale: Mapped[bool] = mapped_column(Boolean, default=True)  # 档期内（end_at 未到）
    game_count: Mapped[int] = mapped_column(Integer, default=0)  # 包内条目数（全量，含未解析）
    # 价格档位（0.2.2）：JSON [{id, price_cny_fen, header, titles, is_initial}]，
    # titles 为**本档新增**（累进售卖，累计展开由 API 层算）；NULL = 旧数据未解析过档位
    tiers_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # 用户已读时刻：NULL = 本轮扫出的新包未读（前端 NEW 徽章，点击标记已读）。
    # 存量库经列保障 ALTER 带 DEFAULT CURRENT_TIMESTAMP = 视为已读（升级不涌 NEW）；
    # 新行写侧显式置 None（未读）。
    acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 最近一次详情抓取


class HumbleBundleGame(Base):
    """包内游戏条目（一行一款；title = HB 侧名称）。"""

    __tablename__ = "humble_bundle_games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(
        String(160), ForeignKey("humble_bundles.slug", ondelete="CASCADE"), index=True
    )
    appid: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    # 下架包保留行（历史可查），on_sale 只挂在包行上
    # storesearch 关联尝试计数（0.3.2）：≥3 轮仍解析不出 → 前端标「未找到」
    # （大概率非 Steam 发行/GOG 独占/商店下架），停止空转重试；
    # 关联成功的行不再触碰（欠账账本语义与 appid 列同源）
    resolve_attempts: Mapped[int] = mapped_column(Integer, default=0)
