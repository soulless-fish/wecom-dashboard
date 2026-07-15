"""
抖音门店营业状态同步服务。

先按需求验证官方 shop.query 接口；当前生产返回中没有营业状态字段，
因此实际营业状态使用来客 PC 门店关系接口的 poi_open_status 字段落库。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import logging
import math
import re
from typing import Any

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import DataSyncLog, DouyinShopBusinessStatus, SessionLocal
from app.services.cookie_storage import load_cookie


logger = logging.getLogger(__name__)

PC_BASE_URL = "https://life.douyin.com"
PC_POI_RELATION_SEARCH_ENDPOINT = "/life/account/v3/poi/relation/search"
SYNC_TYPE = "douyin_shop_business_status"
LITE_APP_ID = "100013"
DEFAULT_PAGE_SIZE = 200
DEFAULT_MAX_PAGES = 200

POI_SELECTOR_PERMISSION_KEYS = [
    "hermes.goods.product_view_all",
    "hermes.goods.product_create",
    "hermes.goods.trade_order_view",
    "hermes.data.shengyijing_data_view",
]

BUSINESS_STATUS_TEXT = {
    0: "暂停营业",
    1: "正常营业",
    2: "即将开业",
    3: "暂停营业",
}

BUSINESS_STATUS_CLASS = {
    "正常营业": "normal",
    "暂停营业": "paused",
    "即将开业": "upcoming",
}


def _json_dumps(value: Any) -> str:
    """把接口返回片段保存为中文友好的 JSON 字符串。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_dict(value: Any) -> dict[str, Any]:
    """只在值为字典时返回原值，避免接口字段为空导致取字段报错。"""
    return value if isinstance(value, dict) else {}


def _to_int(value: Any, default: int = 0) -> int:
    """把接口返回的数字安全转换为整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _valid_id(value: Any) -> bool:
    """判断接口返回的门店 ID 是否为有效业务 ID。"""
    text = str(value or "").strip()
    return bool(text and text != "0")


def normalize_store_name(value: Any) -> str:
    """规整门店名称，用于 poi_id 不一致时兜底匹配。"""
    text = str(value or "").strip().replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


def format_business_status_text(value: Any) -> str:
    """把来客 poi_open_status 状态码转换成侧边栏展示文案。"""
    return BUSINESS_STATUS_TEXT.get(_to_int(value, default=-1), "")


def business_status_class(value: Any) -> str:
    """把营业状态文案转换成前端样式分类。"""
    return BUSINESS_STATUS_CLASS.get(str(value or "").strip(), "")


def _build_headers(cookie: str, csrf_token: str) -> dict[str, str]:
    """构造来客 PC 门店关系接口请求头。"""
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "content-type": "application/json",
        "cookie": cookie,
        "origin": PC_BASE_URL,
        "referer": f"{PC_BASE_URL}/p/liteapp/bc_manage?subTab=1",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
        "ac-tag": "ka_50h",
        "agw-js-conv": "str",
        "rpc-persist-life-merchant-role": "473489608",
        "rpc-persist-life-merchant-switch-role": "1",
        "rpc-persist-life-platform": "pc",
        "rpc-persist-lite-app-id": LITE_APP_ID,
        "rpc-persist-terminal-type": "1",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    if csrf_token:
        headers["x-secsdk-csrf-token"] = csrf_token
    return headers


def _build_payload(page_index: int, page_size: int) -> dict[str, Any]:
    """构造门店关系搜索请求体，只请求已认领门店的基础营业状态。"""
    return {
        "search_params": {
            "relation_types": [1],
            "permission_key_list": POI_SELECTOR_PERMISSION_KEYS,
            "poi_name": "",
            "poi_aggregate_name": "",
        },
        "filter_params": {},
        "aggregate_child_shop": True,
        "need_biz_label": True,
        "biz_key": "craftsman",
        "lite_app_id": LITE_APP_ID,
        "page_index": page_index,
        "page_size": page_size,
    }


async def _fetch_relation_page(
    client: httpx.AsyncClient,
    *,
    root_life_account_id: str,
    page_index: int,
    page_size: int,
) -> dict[str, Any]:
    """拉取来客 PC 门店关系接口单页。"""
    response = await client.post(
        f"{PC_BASE_URL}{PC_POI_RELATION_SEARCH_ENDPOINT}",
        params={"root_life_account_id": root_life_account_id},
        json=_build_payload(page_index, page_size),
    )
    response.raise_for_status()
    body = response.json()
    status_code = _safe_dict(body).get("status_code")
    if status_code not in (0, "0", None):
        raise RuntimeError(f"来客PC门店关系接口返回错误: {_safe_dict(body).get('status_msg') or status_code}")
    return body


def _extract_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """从门店关系接口响应中提取门店列表。"""
    rows = _safe_dict(body.get("data")).get("list") or []
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _extract_total_count(body: dict[str, Any]) -> int:
    """从门店关系接口响应中读取总记录数。"""
    pagination = _safe_dict(_safe_dict(body.get("data")).get("pagination"))
    return _to_int(pagination.get("total_count"))


def _extract_business_status_row(item: dict[str, Any]) -> dict[str, Any]:
    """把来客门店对象转换成本地表字段。"""
    status_code = _to_int(item.get("poi_open_status"), default=-1)
    status_text = format_business_status_text(status_code)
    poi_name = str(item.get("poi_name") or "").strip()
    return {
        "poi_id": str(item.get("poi_id") or "").strip(),
        "poi_name": poi_name,
        "normalized_poi_name": normalize_store_name(poi_name),
        "poi_remark_name": str(item.get("poi_remark_name") or "").strip(),
        "poi_life_account_id": str(item.get("poi_life_account_id") or "").strip(),
        "business_status_code": status_code,
        "business_status_text": status_text,
    }


async def fetch_douyin_shop_business_status_pages(
    *,
    cookie: str,
    csrf_token: str,
    root_life_account_id: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[dict[str, Any]]:
    """分页拉取来客 PC 门店营业状态。"""
    headers = _build_headers(cookie, csrf_token)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True, headers=headers) as client:
        first_body = await _fetch_relation_page(
            client,
            root_life_account_id=root_life_account_id,
            page_index=1,
            page_size=page_size,
        )
        all_rows = _extract_rows(first_body)
        total_count = _extract_total_count(first_body)
        page_count = min(max_pages, max(1, math.ceil(total_count / page_size))) if total_count else 1

        for page_index in range(2, page_count + 1):
            body = await _fetch_relation_page(
                client,
                root_life_account_id=root_life_account_id,
                page_index=page_index,
                page_size=page_size,
            )
            rows = _extract_rows(body)
            if not rows:
                break
            all_rows.extend(rows)

    return all_rows


async def sync_douyin_shop_business_status(db: Session | None = None) -> dict[str, Any]:
    """同步门店营业状态到本地数据库。"""
    own_session = db is None
    session = db or SessionLocal()
    sync_log_id: int | None = None

    try:
        cookie_config = load_cookie()
        root_life_account_id = str(cookie_config.get("account_id") or "").strip()
        pc_cookie = str(cookie_config.get("cookie") or "").strip()
        csrf_token = str(cookie_config.get("csrf_token") or "").strip()
        if not pc_cookie or not root_life_account_id:
            raise RuntimeError("来客 PC 后台 Cookie 或 root_life_account_id 未配置，无法同步门店营业状态")

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
        logger.info("抖音门店营业状态同步: 开始调用 %s", PC_POI_RELATION_SEARCH_ENDPOINT)
        items = await fetch_douyin_shop_business_status_pages(
            cookie=pc_cookie,
            csrf_token=csrf_token,
            root_life_account_id=root_life_account_id,
        )
        query_finished_at = datetime.now()
        sync_time = datetime.now()

        session.query(DouyinShopBusinessStatus).filter(
            DouyinShopBusinessStatus.account_id == root_life_account_id
        ).delete(synchronize_session=False)

        saved_count = 0
        skipped_count = 0
        status_counter: Counter[str] = Counter()
        seen_poi_ids: set[str] = set()
        for item in items:
            row_data = _extract_business_status_row(item)
            poi_id = row_data["poi_id"]
            if not _valid_id(poi_id) or poi_id in seen_poi_ids:
                skipped_count += 1
                continue
            seen_poi_ids.add(poi_id)
            row = DouyinShopBusinessStatus(
                account_id=root_life_account_id,
                poi_id=poi_id,
                poi_name=row_data["poi_name"],
                normalized_poi_name=row_data["normalized_poi_name"],
                poi_remark_name=row_data["poi_remark_name"],
                poi_life_account_id=row_data["poi_life_account_id"],
                business_status_code=row_data["business_status_code"],
                business_status_text=row_data["business_status_text"],
                source_status="success" if row_data["business_status_text"] else "unknown_status",
                raw_json=_json_dumps(item),
                last_sync_at=sync_time,
            )
            session.add(row)
            saved_count += 1
            status_counter[row.business_status_text or "未知状态"] += 1

        sync_log = session.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
        if sync_log:
            sync_log.status = "success"
            sync_log.total_records = saved_count
            sync_log.finished_at = datetime.now()
        session.commit()

        result = {
            "account_id": root_life_account_id,
            "record_count": saved_count,
            "candidate_count": len(items),
            "skipped_count": skipped_count,
            "status_count": dict(status_counter),
            "api_endpoint": PC_POI_RELATION_SEARCH_ENDPOINT,
            "source": "life_douyin_pc_poi_relation_search",
            "page_size": DEFAULT_PAGE_SIZE,
            "query_started_at": query_started_at.isoformat(timespec="seconds"),
            "query_finished_at": query_finished_at.isoformat(timespec="seconds"),
            "last_sync_at": sync_time.isoformat(timespec="seconds"),
        }
        logger.info("抖音门店营业状态同步完成: %s", result)
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
        logger.exception("抖音门店营业状态同步失败")
        raise
    finally:
        if own_session:
            session.close()


def _format_status_summary(row: DouyinShopBusinessStatus | None) -> dict[str, Any]:
    """把营业状态记录转换成侧边栏字段。"""
    if row is None or not row.business_status_text:
        return {
            "business_status_available": False,
            "business_status_code": None,
            "business_status_text": "",
            "business_status_class": "",
            "business_status_last_sync_at": "",
        }
    return {
        "business_status_available": True,
        "business_status_code": row.business_status_code,
        "business_status_text": row.business_status_text or "",
        "business_status_class": business_status_class(row.business_status_text),
        "business_status_last_sync_at": row.last_sync_at.isoformat(timespec="seconds") if row.last_sync_at else "",
    }


def build_store_business_status_summary(db: Session, poi_id: Any = "", poi_name: Any = "") -> dict[str, Any]:
    """按门店 ID 优先、门店名称兜底构造侧边栏营业状态摘要。"""
    poi_id_text = str(poi_id or "").strip()
    poi_name_key = normalize_store_name(poi_name)

    row: DouyinShopBusinessStatus | None = None
    if poi_id_text:
        row = (
            db.query(DouyinShopBusinessStatus)
            .filter(DouyinShopBusinessStatus.poi_id == poi_id_text)
            .order_by(DouyinShopBusinessStatus.last_sync_at.desc(), DouyinShopBusinessStatus.id.desc())
            .first()
        )
    if row is None and poi_name_key:
        row = (
            db.query(DouyinShopBusinessStatus)
            .filter(DouyinShopBusinessStatus.normalized_poi_name == poi_name_key)
            .order_by(DouyinShopBusinessStatus.last_sync_at.desc(), DouyinShopBusinessStatus.id.desc())
            .first()
        )
    return _format_status_summary(row)


def get_douyin_shop_business_status(db: Session) -> dict[str, Any]:
    """读取门店营业状态同步状态，不返回 Cookie 或密钥。"""
    cookie_config = load_cookie()
    last_log = (
        db.query(DataSyncLog)
        .filter(DataSyncLog.sync_type == SYNC_TYPE)
        .order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc())
        .first()
    )
    status_rows = (
        db.query(
            DouyinShopBusinessStatus.business_status_text,
            func.count(DouyinShopBusinessStatus.id),
        )
        .group_by(DouyinShopBusinessStatus.business_status_text)
        .all()
    )
    return {
        "configured": bool(cookie_config.get("cookie") and cookie_config.get("account_id")),
        "api_endpoint": PC_POI_RELATION_SEARCH_ENDPOINT,
        "source": "life_douyin_pc_poi_relation_search",
        "status_count": {str(name or "未知状态"): int(count or 0) for name, count in status_rows},
        "total_count": int(db.query(DouyinShopBusinessStatus).count() or 0),
        "last_sync": {
            "status": last_log.status if last_log else "",
            "total_records": last_log.total_records if last_log else 0,
            "error_message": last_log.error_message if last_log else "",
            "started_at": last_log.started_at.isoformat(timespec="seconds") if last_log and last_log.started_at else "",
            "finished_at": last_log.finished_at.isoformat(timespec="seconds") if last_log and last_log.finished_at else "",
        },
    }
