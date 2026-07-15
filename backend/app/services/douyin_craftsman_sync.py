"""
抖音职人号同步与门店详情服务。

该服务调用抖音来客 PC 后台基础绑定记录接口，只重建签约成功的个人职人号。
商家职人号沿用本地已有数据，避免个人职人号同步时误删其它抖音号分类。
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.database.models import DataSyncLog, DouyinCraftsmanBinding, DouyinPoiAccountBinding, SessionLocal
from app.services.cookie_storage import load_cookie
from app.services.douyin_poi_account_sync import is_active_poi_account_status, normalize_poi_account_status


logger = logging.getLogger(__name__)

PC_BASE_URL = "https://life.douyin.com"
CRAFTSMAN_INFO_LIST_ENDPOINT = "/life/craftsman/v1/info/list"
SYNC_TYPE = "douyin_craftsman_bindings"
MERCHANT_ACTIVE_STATUS = "运营中"
PERSONAL_ACTIVE_STATUS = "签约成功"
CRAFTSMAN_STATUS_TEXT = {
    "1": "待签约",
    "2": PERSONAL_ACTIVE_STATUS,
    "3": "签约失败",
    "4": "已解约",
}


def _json_dumps(value: Any) -> str:
    """把接口原始片段保存为中文友好的 JSON 字符串。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_dict(value: Any) -> dict[str, Any]:
    """只在值为字典时返回原值，避免接口字段为空时报错。"""
    return value if isinstance(value, dict) else {}


def _normalize_store_name(value: Any) -> str:
    """规整门店名称和就职信息，便于侧边栏按门店名精确匹配。"""
    text = str(value or "").strip().replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


def _to_int(value: Any) -> int:
    """把接口返回的分页数字安全转换为整数。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _response_data(body: dict[str, Any]) -> dict[str, Any]:
    """兼容列表字段在顶层或 data 下两种响应结构。"""
    data = _safe_dict(body.get("data"))
    if "craftsman_bind_record_list" in data or "pagination" in data:
        return data
    return body


def _extract_craftsman_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """从基础绑定记录接口响应中提取职人号列表。"""
    rows = _response_data(body).get("craftsman_bind_record_list") or []
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _extract_pagination(body: dict[str, Any]) -> dict[str, int]:
    """读取基础绑定记录接口的分页信息。"""
    pagination = _safe_dict(_response_data(body).get("pagination"))
    return {
        "page_count": _to_int(pagination.get("page_count")),
        "page_size": _to_int(pagination.get("page_size")),
        "total_count": _to_int(pagination.get("total_count")),
    }


def _format_craftsman_status(value: Any) -> str:
    """把来客 PC 职人号状态码转换为侧边栏展示文案。"""
    text = str(value or "").strip()
    return CRAFTSMAN_STATUS_TEXT.get(text, text)


def _extract_life_craftsman_binding(item: dict[str, Any], root_account_id: str) -> dict[str, Any]:
    """从来客 PC 基础绑定记录对象中提取本地表字段。"""
    account_info = _safe_dict(item.get("account_info"))
    relation_info = _safe_dict(item.get("relation_info"))
    craftsman_info = _safe_dict(item.get("craftsman_info"))
    account_name = str(account_info.get("account_name") or "").strip()
    aweme_short_id = str(relation_info.get("aweme_short_id") or "").strip()
    aweme_user_id = str(relation_info.get("aweme_user_id") or "").strip()
    bind_record_id = str(item.get("bind_record_id") or "").strip()
    craftsman_name = str(craftsman_info.get("craftsman_name") or "").strip()
    position_parts = [
        str(craftsman_info.get(key) or "").strip()
        for key in ("position", "role", "title")
        if str(craftsman_info.get(key) or "").strip()
    ]

    return {
        "account_id": root_account_id,
        "source_account_id": str(account_info.get("life_account_id") or "").strip(),
        "poi_id": str(account_info.get("life_account_id") or "").strip(),
        "poi_name": account_name,
        "craftsman_type": "personal",
        "craftsman_uid": aweme_user_id or aweme_short_id or bind_record_id,
        "aweme_id": aweme_short_id or aweme_user_id,
        "aweme_name": craftsman_name,
        "operator_name": craftsman_name,
        "employee_info": account_name,
        "position_title": " / ".join(dict.fromkeys(position_parts)),
        "is_violation": "",
        "valid_fans_count": 0,
        "bring_goods_permission": "",
        "status": _format_craftsman_status(item.get("status")),
    }


def _build_craftsman_headers(cookie: str, csrf_token: str) -> dict[str, str]:
    """构造来客 PC 基础绑定记录接口请求头。"""
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "content-type": "application/json",
        "cookie": cookie,
        "origin": PC_BASE_URL,
        "referer": f"{PC_BASE_URL}/p/life/craftsman",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
        "agw-js-conv": "str",
    }
    if csrf_token:
        headers["x-secsdk-csrf-token"] = csrf_token
    return headers


async def _fetch_craftsman_info_page(
    client: httpx.AsyncClient,
    *,
    root_life_account_id: str,
    page_index: int,
) -> dict[str, Any]:
    """拉取来客 PC 基础绑定记录单页，固定筛选签约成功状态。"""
    response = await client.get(
        f"{PC_BASE_URL}{CRAFTSMAN_INFO_LIST_ENDPOINT}",
        params={
            "root_life_account_id": root_life_account_id,
            "scene": 2,
            "data_access": "{}",
            "page_index": page_index,
            "life_account_ids": "",
            "status": [2],
            "cooperation_mode": [1],
        },
    )
    response.raise_for_status()
    body = response.json()
    status_code = _safe_dict(body).get("status_code")
    if status_code not in (0, "0", None):
        raise RuntimeError(f"来客PC职人基础绑定记录接口返回错误: {_safe_dict(body).get('status_msg') or status_code}")
    return body


async def fetch_douyin_craftsman_pages(
    *,
    cookie: str,
    csrf_token: str,
    root_life_account_id: str,
    max_pages: int = 500,
) -> list[dict[str, Any]]:
    """分页拉取来客 PC 后台签约成功的个人职人号基础绑定记录。"""
    headers = _build_craftsman_headers(cookie, csrf_token)
    all_items: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True, headers=headers) as client:
        first_body = await _fetch_craftsman_info_page(
            client,
            root_life_account_id=root_life_account_id,
            page_index=1,
        )
        all_items.extend(_extract_craftsman_rows(first_body))
        pagination = _extract_pagination(first_body)
        page_count = pagination["page_count"] or 1

        for page_index in range(2, min(page_count, max_pages) + 1):
            body = await _fetch_craftsman_info_page(
                client,
                root_life_account_id=root_life_account_id,
                page_index=page_index,
            )
            rows = _extract_craftsman_rows(body)
            if not rows:
                break
            all_items.extend(rows)

    return all_items


def _upsert_craftsman_row(db: Session, *, root_account_id: str, item: dict[str, Any], sync_time: datetime) -> bool:
    """新增或更新一条签约成功个人职人号记录。"""
    binding = _extract_life_craftsman_binding(item, root_account_id)
    if binding["status"] != PERSONAL_ACTIVE_STATUS:
        return False
    if not binding["employee_info"] or not (binding["aweme_id"] or binding["craftsman_uid"]):
        return False

    row = (
        db.query(DouyinCraftsmanBinding)
        .filter(
            DouyinCraftsmanBinding.account_id == root_account_id,
            DouyinCraftsmanBinding.craftsman_type == binding["craftsman_type"],
            DouyinCraftsmanBinding.craftsman_uid == binding["craftsman_uid"],
            DouyinCraftsmanBinding.poi_id == binding["poi_id"],
        )
        .first()
    )
    if row is None:
        row = DouyinCraftsmanBinding(
            account_id=root_account_id,
            craftsman_type=binding["craftsman_type"],
            craftsman_uid=binding["craftsman_uid"],
            poi_id=binding["poi_id"],
        )
        db.add(row)

    row.source_account_id = binding["source_account_id"]
    row.poi_name = binding["poi_name"]
    row.aweme_id = binding["aweme_id"]
    row.aweme_name = binding["aweme_name"]
    row.operator_name = binding["operator_name"]
    row.employee_info = binding["employee_info"]
    row.position_title = binding["position_title"]
    row.is_violation = binding["is_violation"]
    row.valid_fans_count = binding["valid_fans_count"]
    row.bring_goods_permission = binding["bring_goods_permission"]
    row.status = binding["status"]
    row.raw_json = _json_dumps(item)
    row.last_sync_at = sync_time
    return True


async def sync_douyin_craftsman_bindings(db: Session | None = None) -> dict[str, Any]:
    """同步来客 PC 签约成功个人职人号到本地数据库。"""
    own_session = db is None
    session = db or SessionLocal()
    sync_log_id: int | None = None

    try:
        cookie_config = load_cookie()
        root_life_account_id = str(cookie_config.get("account_id") or "").strip()
        pc_cookie = str(cookie_config.get("cookie") or "").strip()
        csrf_token = str(cookie_config.get("csrf_token") or "").strip()
        if not pc_cookie or not root_life_account_id:
            raise RuntimeError("来客 PC 后台 Cookie 或 root_life_account_id 未配置，无法同步个人职人号")

        sync_log = DataSyncLog(
            sync_type=SYNC_TYPE,
            data_month=datetime.now().strftime("%Y-%m"),
            status="running",
            started_at=datetime.now(),
        )
        session.add(sync_log)
        session.commit()
        session.refresh(sync_log)
        sync_log_id = sync_log.id

        query_started_at = datetime.now()
        logger.info("抖音个人职人号同步: 开始调用 %s", CRAFTSMAN_INFO_LIST_ENDPOINT)
        items = await fetch_douyin_craftsman_pages(
            cookie=pc_cookie,
            csrf_token=csrf_token,
            root_life_account_id=root_life_account_id,
        )
        query_finished_at = datetime.now()
        sync_time = datetime.now()

        # 基础绑定记录接口只覆盖个人职人号，同步时只删除个人职人号，保留商家职人号。
        deleted_count = session.query(DouyinCraftsmanBinding).filter(
            DouyinCraftsmanBinding.account_id == root_life_account_id,
            DouyinCraftsmanBinding.craftsman_type == "personal",
        ).delete(synchronize_session=False)
        session.commit()
        session.expire_all()

        saved_count = 0
        skipped_count = 0
        seen_keys: set[tuple[str, str, str, str]] = set()
        for item in items:
            binding = _extract_life_craftsman_binding(item, root_life_account_id)
            unique_key = (
                binding["account_id"],
                binding["craftsman_type"],
                binding["craftsman_uid"],
                binding["poi_id"],
            )
            if unique_key in seen_keys:
                skipped_count += 1
                continue
            seen_keys.add(unique_key)
            if _upsert_craftsman_row(session, root_account_id=root_life_account_id, item=item, sync_time=sync_time):
                saved_count += 1
            else:
                skipped_count += 1

        session.flush()
        total_count = session.query(DouyinCraftsmanBinding).filter(
            DouyinCraftsmanBinding.account_id == root_life_account_id
        ).count()
        sync_log = session.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
        if sync_log:
            sync_log.status = "success"
            sync_log.total_records = total_count
            sync_log.finished_at = datetime.now()
        session.commit()

        result = {
            "account_id": root_life_account_id,
            "record_count": total_count,
            "candidate_count": len(items),
            "saved_signed_success_count": saved_count,
            "skipped_count": skipped_count,
            "deleted_personal_record_count": deleted_count,
            "api_endpoint": CRAFTSMAN_INFO_LIST_ENDPOINT,
            "filter_status": PERSONAL_ACTIVE_STATUS,
            "match_field": "employee_info",
            "source": "life_douyin_pc_craftsman_info_list",
            "query_started_at": query_started_at.isoformat(timespec="seconds"),
            "query_finished_at": query_finished_at.isoformat(timespec="seconds"),
            "last_sync_at": sync_time.isoformat(timespec="seconds"),
        }
        logger.info("抖音个人职人号同步完成: %s", result)
        return result
    except Exception as exc:
        session.rollback()
        if sync_log_id is not None:
            log_row = session.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
            if log_row:
                log_row.status = "failed"
                log_row.error_message = str(exc)
                log_row.finished_at = datetime.now()
                session.commit()
        logger.exception("抖音个人职人号同步失败")
        raise
    finally:
        if own_session:
            session.close()


def _format_poi_account(row: DouyinPoiAccountBinding) -> dict[str, Any]:
    """把子机构经营号记录转换为详情页字段。"""
    return {
        "account_category": "sub_org",
        "account_category_name": "子机构经营号",
        "account_id": row.poi_account_id or "",
        "account_name": row.poi_account_name or "",
        "status": normalize_poi_account_status(row.source_status),
        "poi_id": row.poi_id or "",
        "poi_name": row.poi_name or "",
        "employee_info": row.parent_account_name or "",
        "position_title": "",
        "is_violation": "",
        "valid_fans_count": 0,
        "bring_goods_permission": "",
        "last_sync_at": row.last_sync_at.isoformat(timespec="seconds") if row.last_sync_at else "",
    }


def _format_craftsman(row: DouyinCraftsmanBinding) -> dict[str, Any]:
    """把商家职人号或个人职人号记录转换为详情页字段。"""
    return {
        "account_category": row.craftsman_type or "",
        "account_category_name": "商家职人号" if row.craftsman_type == "merchant" else "个人职人号",
        "account_id": row.aweme_id or row.craftsman_uid or "",
        "account_name": row.aweme_name or row.aweme_id or row.craftsman_uid or "",
        "status": row.status or "",
        "poi_id": row.poi_id or "",
        "poi_name": row.poi_name or "",
        "operator_name": row.operator_name or "",
        "employee_info": row.employee_info or row.poi_name or "",
        "position_title": row.position_title or "",
        "is_violation": row.is_violation or "",
        "valid_fans_count": row.valid_fans_count or 0,
        "bring_goods_permission": row.bring_goods_permission or "",
        "last_sync_at": row.last_sync_at.isoformat(timespec="seconds") if row.last_sync_at else "",
    }


def build_store_douyin_account_detail(db: Session, poi_id: Any = "", poi_name: Any = "") -> dict[str, Any]:
    """按门店ID和门店名称构造抖音号详情页数据。"""
    poi_id_text = str(poi_id or "").strip()
    poi_name_text = str(poi_name or "").strip()
    poi_name_key = _normalize_store_name(poi_name_text)

    sub_org_rows = (
        db.query(DouyinPoiAccountBinding)
        .filter(DouyinPoiAccountBinding.poi_id == poi_id_text)
        .order_by(DouyinPoiAccountBinding.last_sync_at.desc(), DouyinPoiAccountBinding.id.desc())
        .all()
        if poi_id_text
        else []
    )
    sub_org_accounts = [_format_poi_account(row) for row in sub_org_rows if is_active_poi_account_status(row.source_status)]

    craftsman_rows = db.query(DouyinCraftsmanBinding).order_by(
        DouyinCraftsmanBinding.last_sync_at.desc(),
        DouyinCraftsmanBinding.id.desc(),
    ).all()
    matched_craftsman_rows = []
    for row in craftsman_rows:
        row_poi_id = str(row.poi_id or "").strip()
        row_name_key = _normalize_store_name(row.employee_info or row.poi_name)
        if poi_name_key and row_name_key and row_name_key == poi_name_key:
            matched_craftsman_rows.append(row)
        elif not poi_name_key and poi_id_text and row_poi_id and row_poi_id == poi_id_text:
            matched_craftsman_rows.append(row)

    merchant_accounts = [
        _format_craftsman(row)
        for row in matched_craftsman_rows
        if row.craftsman_type == "merchant" and row.status == MERCHANT_ACTIVE_STATUS
    ]
    personal_accounts = [
        _format_craftsman(row)
        for row in matched_craftsman_rows
        if row.craftsman_type == "personal" and row.status == PERSONAL_ACTIVE_STATUS
    ]

    all_accounts = sub_org_accounts + merchant_accounts + personal_accounts
    latest_sync_times = [item["last_sync_at"] for item in all_accounts if item.get("last_sync_at")]
    return {
        "poi_id": poi_id_text,
        "poi_name": poi_name_text,
        "account_count": len(all_accounts),
        "sub_org_accounts": sub_org_accounts,
        "merchant_craftsman_accounts": merchant_accounts,
        "personal_craftsman_accounts": personal_accounts,
        "accounts": all_accounts,
        "last_sync_at": max(latest_sync_times) if latest_sync_times else "",
    }


def get_douyin_craftsman_binding_status(db: Session) -> dict[str, Any]:
    """读取个人职人号同步状态，不返回密钥或 token。"""
    cookie_config = load_cookie()
    root_life_account_id = str(cookie_config.get("account_id") or "").strip()
    last_log = (
        db.query(DataSyncLog)
        .filter(DataSyncLog.sync_type == SYNC_TYPE)
        .order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc())
        .first()
    )
    total_count = db.query(DouyinCraftsmanBinding).count()
    merchant_count = db.query(DouyinCraftsmanBinding).filter(DouyinCraftsmanBinding.craftsman_type == "merchant").count()
    personal_count = (
        db.query(DouyinCraftsmanBinding)
        .filter(
            DouyinCraftsmanBinding.craftsman_type == "personal",
            DouyinCraftsmanBinding.status == PERSONAL_ACTIVE_STATUS,
        )
        .count()
    )
    return {
        "configured": bool(cookie_config.get("cookie") and root_life_account_id),
        "account_id": root_life_account_id,
        "api_endpoint": CRAFTSMAN_INFO_LIST_ENDPOINT,
        "source": "life_douyin_pc_craftsman_info_list",
        "filter_status": PERSONAL_ACTIVE_STATUS,
        "match_field": "employee_info",
        "total_count": total_count,
        "merchant_count": merchant_count,
        "personal_count": personal_count,
        "last_sync": {
            "status": last_log.status if last_log else "",
            "total_records": last_log.total_records if last_log else 0,
            "error_message": last_log.error_message if last_log else "",
            "started_at": last_log.started_at.isoformat(timespec="seconds") if last_log and last_log.started_at else "",
            "finished_at": last_log.finished_at.isoformat(timespec="seconds") if last_log and last_log.finished_at else "",
        },
    }
