"""一次性修复工具：从 game_price_history 重建 game_current_prices。

背景（2026-10 dev 库事故）：爬虫 missing 清理链把带旧价的 missing 行整片
删除，game_current_prices 清零而 game_price_history（289 万行）完好。
本脚本按「每 (appid, 区) 最近一次有效观察」从 history 回填 current：

- 选择规则复刻 db_writer._plan_price_rows 的口径：有价行优先、标准版优先
  （非 gold 非 bundle、无版本后缀或显式 Standard Edition 归一）、同刻
  多 sub 取 min(sub_id)；该区从无有效价则落最近一次状态行（locked 等）。
- 观察章按快照派生（ok/locked → success，其余 failed），
  updated_at / last_success_at 用 snapshot_at 而非当下——诚实标注数据年龄，
  且不给 pp_flag 的 snapshot_at < updated_at 判据制造「全史自比」死角。
- 只补缺不覆盖：ON CONFLICT 仅在现行为空价（price IS NULL）时写入，
  已有真实观察的行绝不被历史快照倒灌；幂等可重跑。

用法（关闭应用后执行；默认 dev 库，指向正式库需 --allow-prod）：
    server\\.venv\\Scripts\\python.exe scripts\\backfill_current_prices.py --dry-run
    server\\.venv\\Scripts\\python.exe scripts\\backfill_current_prices.py

回填后重启应用即可：启动三连（史低/永降/排序缓存）自动重算派生列，
或手动跑 scripts\\refresh_sort_cache.py。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

# 显式命名的标准版=本体官方命名（与 db_writer._PLAIN_STANDARD_SUFFIX_RE 同口径）
_PLAIN_STANDARD_RE = re.compile(r"standard( digital)? edition$", re.IGNORECASE)

_H_COLS = (
    "h.appid, h.region_code, h.snapshot_at, h.currency, h.price, h.original_price, "
    "h.discount_percent, h.sub_id, h.is_gold, h.version_suffix, h.is_bundle, "
    "h.price_status, h.cny_fen, h.discount_end_ts"
)


class _Cand(NamedTuple):
    appid: int
    region_code: str
    snapshot_at: str
    currency: str | None
    price: int | None
    original_price: int | None
    discount_percent: int | None
    sub_id: int | None
    is_gold: int
    version_suffix: str | None
    is_bundle: int
    price_status: str | None
    cny_fen: int | None
    discount_end_ts: int | None


def _resolve_db(allow_prod: bool) -> Path:
    from app.core.config import get_settings

    settings = get_settings()
    db = Path(settings.data_dir) / settings.db_filename
    if "dev" not in str(db).lower() and not allow_prod:
        raise SystemExit(
            f"目标库不是 dev 数据目录（{db}）：这是数据修复工具，指向正式库请显式 --allow-prod"
        )
    if not db.exists():
        raise SystemExit(f"数据库不存在: {db}")
    return db


def _is_plain_standard(suffix: str | None) -> bool:
    return bool(suffix) and bool(_PLAIN_STANDARD_RE.match(suffix.strip()))


def _pick(cands: list[_Cand]) -> _Cand:
    """同一快照时刻多 sub 并列时：标准版优先，再 min(sub_id)。"""
    std = [
        r for r in cands
        if not r.is_gold and not r.is_bundle
        and (not r.version_suffix or _is_plain_standard(r.version_suffix))
    ]
    pool = std or cands
    return min(pool, key=lambda r: r.sub_id if r.sub_id is not None else 0)


def _group_latest(rows: list[sqlite3.Row]) -> dict[tuple[int, str], list[_Cand]]:
    """按 (appid, 区) 归组「最近一次快照」候选（同刻并列多行保留）。"""
    grouped: dict[tuple[int, str], list[_Cand]] = {}
    for row in rows:
        cand = _Cand(*row)
        key = (cand.appid, cand.region_code)
        bucket = grouped.setdefault(key, [])
        if not bucket or cand.snapshot_at == bucket[0].snapshot_at:
            bucket.append(cand)
    return grouped


def build_rows(con: sqlite3.Connection) -> list[tuple]:
    """有价观察优先；从无有效价的区落最近状态行；仅限 games 在册 appid。"""
    live = {r[0] for r in con.execute("SELECT appid FROM games")}
    rows: list[tuple] = []

    # 每区「最近一次有效价」：MAX(snapshot_at) 顶趟的全部候选行
    priced_rows = list(con.execute(
        f"SELECT {_H_COLS} FROM game_price_history h "
        "JOIN (SELECT appid, region_code, MAX(snapshot_at) AS mx FROM game_price_history "
        "WHERE price IS NOT NULL AND COALESCE(is_bundle, 0) = 0 AND region_code <> '' "
        "GROUP BY appid, region_code) t "
        "ON h.appid = t.appid AND h.region_code = t.region_code AND h.snapshot_at = t.mx "
        "WHERE h.price IS NOT NULL AND COALESCE(h.is_bundle, 0) = 0"
    ))
    done: set[tuple[int, str]] = set()
    for key, cands in _group_latest(priced_rows).items():
        appid, _region = key
        if appid not in live:
            continue
        r = _pick(cands)
        done.add(key)
        success = (r.price_status or "ok") == "ok"
        rows.append((
            appid, r.region_code, r.currency or "", r.price,
            r.original_price, r.discount_percent or 0,
            r.sub_id or 0, r.price_status or "ok", r.cny_fen,
            r.discount_end_ts, r.snapshot_at,
            "success" if success else "failed",
            "free" if r.price == 0 else "ok" if success else None,
            r.snapshot_at if success else None,
        ))

    # 从无有效价的区：最近一次快照的状态行（locked/missing 等）
    status_rows = list(con.execute(
        f"SELECT {_H_COLS} FROM game_price_history h "
        "JOIN (SELECT appid, region_code, MAX(snapshot_at) AS mx FROM game_price_history "
        "WHERE region_code <> '' GROUP BY appid, region_code) t "
        "ON h.appid = t.appid AND h.region_code = t.region_code AND h.snapshot_at = t.mx"
    ))
    for key, cands in _group_latest(status_rows).items():
        appid, _region = key
        if appid not in live or key in done:
            continue
        r = _pick(cands)
        status = r.price_status or "missing"
        success = status == "locked"
        rows.append((
            appid, r.region_code, r.currency or "", None, None, 0,
            r.sub_id or 0, status, None, None, r.snapshot_at,
            "success" if success else "failed",
            "locked" if success else None,
            r.snapshot_at if success else None,
        ))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument("--allow-prod", action="store_true", help="允许指向非 dev 数据目录")
    args = ap.parse_args()

    db = _resolve_db(args.allow_prod)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = build_rows(con)
        with_price = sum(1 for r in rows if r[3] is not None)
        print(f"候选回填行 {len(rows)}（带价 {with_price} / 状态行 {len(rows) - with_price}），目标库 {db}")
        if args.dry_run:
            print("dry-run：未写库")
            return 0
        # 只补缺不覆盖：冲突时仅当现行为空价才写入（幂等可重跑）
        con.executemany(
            "INSERT INTO game_current_prices ("
            "appid, region_code, currency, price, original_price, discount_percent, sub_id, "
            "price_status, fail_count, cny_fen, discount_end_ts, updated_at, "
            "attempt_outcome, steam_answer, last_success_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(appid, region_code) DO UPDATE SET "
            "currency = excluded.currency, price = excluded.price, "
            "original_price = excluded.original_price, "
            "discount_percent = excluded.discount_percent, sub_id = excluded.sub_id, "
            "price_status = excluded.price_status, cny_fen = excluded.cny_fen, "
            "discount_end_ts = excluded.discount_end_ts, updated_at = excluded.updated_at, "
            "attempt_outcome = excluded.attempt_outcome, steam_answer = excluded.steam_answer, "
            "last_success_at = excluded.last_success_at "
            "WHERE game_current_prices.price IS NULL",
            rows,
        )
        con.commit()
        cur = con.execute(
            "SELECT COUNT(*) FROM game_current_prices WHERE price IS NOT NULL"
        ).fetchone()[0]
        print(f"回填完成：现价行（price 非空）= {cur}。重启应用自动重算派生列（史低/永降/排序缓存）。")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
