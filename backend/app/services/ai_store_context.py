from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.database.models import StorePerformance
from app.services.store_group_mapping import get_mapping_candidates


MONTHLY_VIDEO_TARGET = 15
MONTHLY_UPTURN_TARGET = 2000.0
SOURCE_SYSTEMS = ["life.douyin.com", "www.life-data.cn"]


@dataclass
class StoreMatch:
    store: StorePerformance
    match_method: str
    match_confidence: float
    match_reason: str


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def decimal_to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_money_value(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float, Decimal)):
        return float(value)

    text = str(value).strip()
    if not text:
        return 0.0

    text = text.replace("¥", "").replace(",", "").replace("元", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _remove_rating_prefix(keyword: str) -> str:
    return re.sub(r"^[A-Za-z][+\-]?", "", keyword or "").strip()


def _extract_store_keywords(group_name: str) -> list[str]:
    all_brackets = re.findall(r"[（(]([^)）]+)[)）]", group_name or "")
    keywords: list[str] = []
    for bracket_content in all_brackets:
        parts = re.split(r"[、,，\s]+", bracket_content)
        keywords.extend([part.strip() for part in parts if part.strip()])
    return keywords


def _poi_name_matches_keyword(poi_name: str, keyword: str) -> bool:
    if not poi_name or not keyword:
        return False
    if keyword in poi_name:
        return True
    clean_keyword = _remove_rating_prefix(keyword)
    return bool(clean_keyword and clean_keyword != keyword and clean_keyword in poi_name)


def _store_identity(store: StorePerformance) -> str:
    return store.poi_id or f"row:{store.id}"


def get_store_by_poi_id(
    db: Session,
    poi_id: str,
    data_month: Optional[str] = None,
) -> Optional[StorePerformance]:
    if not poi_id:
        return None

    query = db.query(StorePerformance).filter(StorePerformance.poi_id == poi_id)
    if data_month:
        query = query.filter(StorePerformance.data_month == data_month)

    return query.order_by(StorePerformance.data_month.desc(), StorePerformance.updated_at.desc()).first()


def _query_matches_by_keyword(
    db: Session,
    keyword: str,
    used_poi_ids: set[str],
    data_month: Optional[str],
) -> list[StorePerformance]:
    query = db.query(StorePerformance).filter(StorePerformance.poi_name.contains(keyword))
    if data_month:
        query = query.filter(StorePerformance.data_month == data_month)
    if used_poi_ids:
        query = query.filter(~StorePerformance.poi_id.in_(list(used_poi_ids)))
    return query.order_by(StorePerformance.data_month.desc(), StorePerformance.updated_at.desc()).all()


def _pick_best_match(matches: list[StorePerformance], keyword: str) -> Optional[StorePerformance]:
    unique: list[StorePerformance] = []
    seen: set[str] = set()

    for store in matches:
        identity = _store_identity(store)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(store)

    if not unique:
        return None
    if len(unique) == 1:
        return unique[0]

    def score(store: StorePerformance) -> tuple[int, int]:
        name = store.poi_name or ""
        bracket = re.search(r"[（(]([^)）]+)[)）]", name)
        bracket_content = bracket.group(1) if bracket else ""
        exact = bracket_content == keyword
        return (0 if exact else 1, len(name))

    unique.sort(key=score)
    return unique[0]


def resolve_store_matches(
    db: Session,
    group_name: str,
    data_month: Optional[str] = None,
) -> tuple[list[StoreMatch], list[str]]:
    """
    Resolve a WeCom external group name into one or more current StorePerformance records.

    The matching order intentionally follows the existing sidebar route:
    mapping Excel first, then bracket keyword matching.
    """
    group_name = (group_name or "").strip()
    store_keywords = _extract_store_keywords(group_name)
    mapping_candidates = get_mapping_candidates(group_name)

    if not store_keywords and not mapping_candidates:
        return [], ["群名中未找到括号内容，且映射表没有命中该群名"]

    matched: list[StoreMatch] = []
    used_poi_ids: set[str] = set()
    warnings: list[str] = []

    def add_store(store: StorePerformance, method: str, confidence: float, reason: str) -> bool:
        identity = _store_identity(store)
        if identity in used_poi_ids:
            return False
        matched.append(
            StoreMatch(
                store=store,
                match_method=method,
                match_confidence=confidence,
                match_reason=reason,
            )
        )
        used_poi_ids.add(identity)
        return True

    def pick_mapping_candidate(keyword: str):
        remaining = [
            candidate for candidate in mapping_candidates
            if candidate.poi_id and candidate.poi_id not in used_poi_ids
        ]
        if not remaining:
            return None

        keyword_matches = [
            candidate for candidate in remaining
            if _poi_name_matches_keyword(candidate.poi_name or "", keyword)
        ]
        if not keyword_matches:
            return None
        if len(keyword_matches) == 1:
            return keyword_matches[0]

        keyword_matches.sort(key=lambda candidate: len(candidate.poi_name or ""))
        return keyword_matches[0]

    for keyword in store_keywords:
        mapping_candidate = pick_mapping_candidate(keyword)
        if mapping_candidate is not None:
            store = get_store_by_poi_id(db, mapping_candidate.poi_id, data_month=data_month)
            if store:
                add_store(
                    store,
                    "group_name_exact_mapping",
                    0.98,
                    f"群名命中映射表，关键词 `{keyword}` 对应 poi_id `{mapping_candidate.poi_id}`",
                )
                continue
            warnings.append(f"映射表 poi_id `{mapping_candidate.poi_id}` 在数据库中没有找到数据")

        matches = _query_matches_by_keyword(db, keyword, used_poi_ids, data_month)
        search_keyword = keyword
        method = "group_name_keyword"
        confidence = 0.82

        if not matches:
            clean_keyword = _remove_rating_prefix(keyword)
            if clean_keyword and clean_keyword != keyword:
                matches = _query_matches_by_keyword(db, clean_keyword, used_poi_ids, data_month)
                search_keyword = clean_keyword
                method = "group_name_keyword_rating_normalized"
                confidence = 0.78

        store = _pick_best_match(matches, search_keyword)
        if store:
            add_store(
                store,
                method,
                confidence,
                f"从群名括号关键词 `{keyword}` 匹配到门店 `{store.poi_name}`",
            )
        else:
            warnings.append(f"关键词 `{keyword}` 未匹配到门店")

    for candidate in mapping_candidates:
        if not candidate.poi_id or candidate.poi_id in used_poi_ids:
            continue
        store = get_store_by_poi_id(db, candidate.poi_id, data_month=data_month)
        if store:
            add_store(
                store,
                "group_name_exact_mapping",
                0.98,
                f"群名命中映射表，补充 poi_id `{candidate.poi_id}`",
            )
        else:
            warnings.append(f"映射表 poi_id `{candidate.poi_id}` 在数据库中没有找到数据")

    return matched, warnings


def _period_payload(store: StorePerformance) -> dict[str, Any]:
    data_month = store.data_month
    start_day = store.data_start_day
    end_day = store.data_end_day

    def make_date(day: Optional[int]) -> Optional[str]:
        if not data_month or not day:
            return None
        return f"{data_month}-{int(day):02d}"

    return {
        "data_month": data_month,
        "data_start_day": make_date(start_day),
        "data_end_day": make_date(end_day),
        "data_start_day_num": start_day,
        "data_end_day_num": end_day,
    }


def _metrics_payload(store: StorePerformance) -> dict[str, Any]:
    return {
        "upturn_revenue_yuan": round(decimal_to_float(store.gmv_yuan), 2),
        "gmv_yuan": round(decimal_to_float(store.gmv_yuan), 2),
        "live_duration_seconds": int(store.live_duration_seconds or 0),
        "live_duration_display": store.live_duration_formatted or "0天0小时0分钟",
        "video_count": int(store.video_count or 0),
        "video_cnt_1d": int(store.video_cnt_1d or 0),
        "verify_amount_realtime": round(parse_money_value(store.verify_amount_realtime), 2),
        "verify_amount_realtime_display": store.verify_amount_realtime or "¥0.00",
        "verify_cert_cnt_realtime": int(store.verify_cert_cnt_realtime or 0),
        "verify_amount": round(parse_money_value(store.verify_amount), 2),
        "verify_amount_display": store.verify_amount or "¥0.00",
        "verify_cert_cnt": int(store.verify_cert_cnt or 0),
    }


def _targets_payload(metrics: dict[str, Any]) -> dict[str, Any]:
    video_done = int(metrics["video_cnt_1d"] or 0)
    upturn_done = float(metrics["upturn_revenue_yuan"] or 0.0)

    return {
        "monthly_video_target": MONTHLY_VIDEO_TARGET,
        "monthly_upturn_target": MONTHLY_UPTURN_TARGET,
        "video_done": video_done,
        "video_remaining": max(MONTHLY_VIDEO_TARGET - video_done, 0),
        "video_met": video_done >= MONTHLY_VIDEO_TARGET,
        "upturn_done": round(upturn_done, 2),
        "upturn_remaining": round(max(MONTHLY_UPTURN_TARGET - upturn_done, 0.0), 2),
        "upturn_met": upturn_done >= MONTHLY_UPTURN_TARGET,
        "video_metric": "video_cnt_1d",
        "upturn_metric": "gmv_yuan",
    }


def build_single_store_context(
    match: StoreMatch,
    chat_id: Optional[str],
    group_name: Optional[str],
) -> dict[str, Any]:
    store = match.store
    metrics = _metrics_payload(store)
    targets = _targets_payload(metrics)

    return {
        "store": {
            "chat_id": chat_id,
            "group_name": group_name,
            "store_id": store.poi_id,
            "poi_id": store.poi_id,
            "store_name": store.poi_name,
            "poi_name": store.poi_name,
            "store_score": float(store.poi_score) if store.poi_score is not None else None,
            "match_method": match.match_method,
            "match_confidence": match.match_confidence,
            "match_reason": match.match_reason,
        },
        "period": _period_payload(store),
        "metrics": metrics,
        "targets": targets,
        "source": {
            "source_systems": SOURCE_SYSTEMS,
            "fetched_at": _now_iso(),
            "updated_at": store.updated_at.isoformat() if store.updated_at else None,
            "data_version": f"{store.data_month}:{store.data_start_day}-{store.data_end_day}:{store.poi_id or store.id}",
            "statistics_rule": "video_done 使用侧边栏当前达标口径 video_cnt_1d；upturn_done 使用 gmv_yuan；verify_amount_realtime/verify_cert_cnt_realtime 为当月实时核销数据，verify_amount/verify_cert_cnt 为近30天核销数据。",
        },
    }


def build_summary_text(contexts: list[dict[str, Any]]) -> str:
    if not contexts:
        return ""

    parts = []
    for context in contexts:
        store = context["store"]
        targets = context["targets"]
        name = store.get("store_name") or "未知门店"
        video_text = (
            f"本月视频 {targets['video_done']} 条，已达到 {targets['monthly_video_target']} 条要求"
            if targets["video_met"]
            else f"本月视频 {targets['video_done']} 条，距离 {targets['monthly_video_target']} 条还差 {targets['video_remaining']} 条"
        )
        upturn_text = (
            f"上翻 {targets['upturn_done']} 元，已达到 {targets['monthly_upturn_target']} 元要求"
            if targets["upturn_met"]
            else f"上翻 {targets['upturn_done']} 元，距离 {targets['monthly_upturn_target']} 元还差 {targets['upturn_remaining']} 元"
        )
        parts.append(f"{name}: {video_text}；{upturn_text}")

    return "；".join(parts) + "。"


def build_context_response(
    matches: list[StoreMatch],
    chat_id: Optional[str],
    group_name: Optional[str],
    warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    contexts = [
        build_single_store_context(match, chat_id=chat_id, group_name=group_name)
        for match in matches
    ]
    first = contexts[0] if contexts else None

    if not first:
        return {
            "found": False,
            "reason": "未匹配到门店",
            "warnings": warnings or [],
        }

    return {
        "found": True,
        "store": first["store"],
        "stores": [context["store"] for context in contexts],
        "period": first["period"],
        "metrics": first["metrics"],
        "targets": first["targets"],
        "store_contexts": contexts,
        "summary_text": build_summary_text(contexts),
        "source": first["source"],
        "warnings": warnings or [],
    }
