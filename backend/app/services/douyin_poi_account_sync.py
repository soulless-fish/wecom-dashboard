"""
抖音门店绑定经营号同步服务。

该服务优先调用抖音来客 PC 后台抖音号管理接口，保存每个门店绑定的子机构经营抖音号
和审核状态，供企业微信侧边栏按 poi_id 快速展示。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import DataSyncLog, DouyinPoiAccountBinding, SessionLocal, StorePerformance
from app.services.cookie_storage import load_cookie


logger = logging.getLogger(__name__)

PC_ACCOUNT_LIST_ENDPOINT = "/life/gate/v1/account/get_account_list_by_root_v2/"
PC_INTEGRATION_BIND_LIST_ENDPOINT = "/life/merchant/v1/integration-user/bind/list"
SYNC_TYPE = "douyin_poi_account_bindings"
PC_BASE_URL = "https://life.douyin.com"
INTEGRATION_STATUS_TEXT = {
    "1": "已激活",
    "2": "审核中",
    "3": "待完善",
    "4": "待提交",
    "5": "审核中",
    "6": "审核失败",
    "7": "已解绑",
    "8": "已失效",
    "9": "待确认",
    "10": "待处理",
}

ACTIVE_POI_ACCOUNT_STATUS_TEXT = "已激活"
LEGACY_ACTIVE_POI_ACCOUNT_STATUS = {"已激活", "已绑定"}


def _json_dumps(value: Any) -> str:
    """把官方接口返回片段保存为中文友好的 JSON 字符串。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_dict(value: Any) -> dict[str, Any]:
    """只在值为字典时返回原值，避免接口字段为空导致取字段报错。"""
    return value if isinstance(value, dict) else {}


def _normalize_store_name(value: Any) -> str:
    """规整门店名称，用于把经营号列表里的名称匹配到侧边栏门店。"""
    text = str(value or "").strip()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


def _extract_pc_account_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """从来客 PC 子账号列表响应中提取账号列表。"""
    data = body.get("data") if isinstance(body, dict) else {}
    rows = _safe_dict(data).get("list") or []
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _extract_pc_total(body: dict[str, Any]) -> int:
    """从来客 PC 子账号列表响应中提取总数。"""
    data = body.get("data") if isinstance(body, dict) else {}
    pagination = _safe_dict(_safe_dict(data).get("pagination"))
    try:
        return int(pagination.get("total_count") or 0)
    except (TypeError, ValueError):
        return 0


def _extract_bind_list_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """从来客 PC 抖音号管理响应中提取经营号列表。"""
    rows = body.get("integration_info") if isinstance(body, dict) else []
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _extract_bind_list_total(body: dict[str, Any]) -> int:
    """从来客 PC 抖音号管理响应中读取总数。"""
    try:
        return int(_safe_dict(body).get("Total") or 0)
    except (TypeError, ValueError):
        return 0


def _has_more_bind_list(body: dict[str, Any]) -> bool:
    """判断来客 PC 抖音号管理接口是否还有下一页。"""
    return bool(_safe_dict(body).get("HasMore"))


def _first_value(value: Any, keys: tuple[str, ...]) -> Any:
    """递归读取第一个命中的字段值，兼容官方字段层级调整。"""
    if isinstance(value, dict):
        for key in keys:
            if value.get(key) not in (None, ""):
                return value.get(key)
        for item in value.values():
            found = _first_value(item, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def _valid_id(value: str) -> bool:
    """判断接口返回的 ID 是否为有效业务 ID。"""
    return bool(value and value != "0")


def _extract_pc_account_binding(item: dict[str, Any]) -> dict[str, str]:
    """从来客 PC 账号对象中提取门店和经营抖音号字段。"""
    account_info = _safe_dict(item.get("account_info"))
    account_base = _safe_dict(account_info.get("AccountBase"))
    account_attr = _safe_dict(account_info.get("AccountAttribute"))

    return {
        "life_account_id": str(account_info.get("LifeAccountID") or "").strip(),
        "account_type": str(account_base.get("AccountType") or "").strip(),
        "poi_id": str(account_base.get("PoiId") or "").strip(),
        "poi_name": str(account_base.get("AccountName") or "").strip(),
        "aweme_id": str(account_base.get("AwemeUserId") or "").strip(),
        "aweme_name": str(account_base.get("AccountName") or "").strip(),
        "company_name": str(account_attr.get("CompanyName") or "").strip(),
        "license_id": str(account_attr.get("LicenseID") or "").strip(),
    }


def _extract_bind_list_binding(item: dict[str, Any]) -> dict[str, str]:
    """从来客 PC 抖音号管理对象中提取经营号字段。"""
    content = _safe_dict(item.get("integration_content"))
    user_info = _safe_dict(content.get("user_info"))
    bind_form = _safe_dict(content.get("bind_form"))
    subject_info = _safe_dict(content.get("subject_info"))
    status = str(item.get("status") or user_info.get("integration_status") or "").strip()
    aweme_id = str(user_info.get("aweme_id") or "").strip()
    user_id = str(user_info.get("user_id") or "").strip()
    nickname = str(user_info.get("nickname") or bind_form.get("nick_name") or user_info.get("account_name") or "").strip()
    account_name = str(user_info.get("account_name") or nickname).strip()
    fail_reason = str(bind_form.get("fail_reason") or "").strip()

    return {
        "life_account_id": str(item.get("account_id") or "").strip(),
        "account_type": str(item.get("account_type") or "").strip(),
        "key_account_id": str(item.get("key_account_id") or "").strip(),
        "poi_id": str(item.get("poi_id") or "").strip(),
        "poi_name": account_name,
        "aweme_id": aweme_id or user_id,
        "aweme_user_id": user_id,
        "aweme_name": nickname,
        "company_name": str(subject_info.get("company_name") or "").strip(),
        "license_id": str(subject_info.get("qual_serial") or "").strip(),
        "status": status,
        "status_text": _format_integration_status(status, fail_reason),
    }


def _format_integration_status(status: str, fail_reason: str = "") -> str:
    """把来客经营号状态码转换为侧边栏可读文案。"""
    return INTEGRATION_STATUS_TEXT.get(str(status or "").strip(), f"状态{status}" if status else "未知状态")


def normalize_poi_account_status(value: Any) -> str:
    """把历史状态文案统一成当前侧边栏展示口径。"""
    text = str(value or "").strip()
    if text in LEGACY_ACTIVE_POI_ACCOUNT_STATUS:
        return ACTIVE_POI_ACCOUNT_STATUS_TEXT
    return text


def is_active_poi_account_status(value: Any) -> bool:
    """判断经营抖音号是否属于允许展示的已激活状态。"""
    return normalize_poi_account_status(value) == ACTIVE_POI_ACCOUNT_STATUS_TEXT


def _build_latest_store_name_index(db: Session) -> dict[str, list[dict[str, str]]]:
    """按最新数据月份建立门店名称索引，用于经营号接口缺少 poi_id 时兜底匹配。"""
    latest_month = db.query(func.max(StorePerformance.data_month)).scalar()
    if not latest_month:
        return {}

    rows = (
        db.query(StorePerformance)
        .filter(StorePerformance.data_month == latest_month)
        .all()
    )
    index: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = _normalize_store_name(row.poi_name)
        if key:
            index.setdefault(key, []).append({
                "poi_id": str(row.poi_id or ""),
                "poi_name": str(row.poi_name or ""),
            })
    return index


def _resolve_bind_list_poi_id(
    binding: dict[str, str],
    store_name_index: dict[str, list[dict[str, str]]],
) -> tuple[str, str]:
    """解析经营号记录对应的侧边栏 poi_id，优先接口字段，其次门店名称唯一匹配。"""
    if _valid_id(binding.get("poi_id", "")):
        return binding["poi_id"], binding["poi_name"]

    matches = store_name_index.get(_normalize_store_name(binding.get("poi_name", ""))) or []
    unique_matches = []
    seen_poi_ids: set[str] = set()
    for match in matches:
        poi_id = str(match.get("poi_id") or "")
        if poi_id and poi_id not in seen_poi_ids:
            unique_matches.append(match)
            seen_poi_ids.add(poi_id)

    if len(unique_matches) == 1:
        match = unique_matches[0]
        return str(match.get("poi_id") or ""), str(match.get("poi_name") or binding.get("poi_name", ""))
    return "", binding.get("poi_name", "")


def _upsert_binding_row(
    db: Session,
    *,
    account_id: str,
    item: dict[str, Any],
    sync_time: datetime,
) -> None:
    """新增或更新单个门店绑定经营抖音号记录。"""
    binding = _extract_pc_account_binding(item)

    poi_id = binding["poi_id"]
    if not _valid_id(poi_id) or binding["account_type"] != "20" or not _valid_id(binding["aweme_id"]):
        return

    row = (
        db.query(DouyinPoiAccountBinding)
        .filter(
            DouyinPoiAccountBinding.account_id == account_id,
            DouyinPoiAccountBinding.poi_id == poi_id,
        )
        .first()
    )
    if row is None:
        row = DouyinPoiAccountBinding(account_id=account_id, poi_id=poi_id)
        db.add(row)

    row.poi_name = binding["poi_name"]
    row.poi_account_id = binding["aweme_id"]
    row.poi_account_name = binding["aweme_name"]
    row.poi_account_type = "SUB_ORG"
    row.parent_account_id = ""
    row.parent_account_name = ""
    row.parent_account_type = ""
    row.root_account_id = account_id
    row.root_account_name = ""
    row.root_account_type = "ROOT"
    row.raw_json = _json_dumps(item)
    row.source_status = "success"
    row.last_sync_at = sync_time


def _upsert_bind_list_row(
    db: Session,
    *,
    account_id: str,
    item: dict[str, Any],
    store_name_index: dict[str, list[dict[str, str]]],
    sync_time: datetime,
    seen_poi_ids: set[str] | None = None,
) -> bool:
    """新增或更新抖音号管理列表中的单个经营号记录。"""
    binding = _extract_bind_list_binding(item)
    if binding["account_type"] != "20":
        return False

    poi_id, poi_name = _resolve_bind_list_poi_id(binding, store_name_index)
    if not _valid_id(poi_id):
        return False
    if seen_poi_ids is not None and poi_id in seen_poi_ids:
        return False

    row = (
        db.query(DouyinPoiAccountBinding)
        .filter(
            DouyinPoiAccountBinding.account_id == account_id,
            DouyinPoiAccountBinding.poi_id == poi_id,
        )
        .first()
    )
    if row is None:
        row = DouyinPoiAccountBinding(account_id=account_id, poi_id=poi_id)
        db.add(row)

    row.poi_name = poi_name or binding["poi_name"]
    row.poi_account_id = binding["aweme_id"]
    row.poi_account_name = binding["aweme_name"] or binding["poi_name"]
    row.poi_account_type = "SUB_ORG"
    row.parent_account_id = binding["aweme_user_id"]
    row.parent_account_name = binding["company_name"]
    row.parent_account_type = "AWEME_USER_ID"
    row.root_account_id = account_id
    row.root_account_name = ""
    row.root_account_type = "ROOT"
    row.raw_json = _json_dumps(item)
    row.source_status = binding["status_text"]
    row.last_sync_at = sync_time
    if seen_poi_ids is not None:
        seen_poi_ids.add(poi_id)
    return True


def _build_pc_headers(cookie: str, csrf_token: str) -> dict[str, str]:
    """构造来客 PC 后台账号列表接口请求头。"""
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "content-type": "application/json",
        "cookie": cookie,
        "origin": PC_BASE_URL,
        "referer": f"{PC_BASE_URL}/p/liteapp/bc_manage?subTab=3",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
        "ac-tag": "ka_50h",
        "agw-js-conv": "str",
        "rpc-persist-life-merchant-role": "473489608",
        "rpc-persist-life-merchant-switch-role": "1",
    }
    if csrf_token:
        headers["x-secsdk-csrf-token"] = csrf_token
    return headers


async def _fetch_pc_account_page(
    client: httpx.AsyncClient,
    *,
    root_life_account_id: str,
    page: int,
) -> dict[str, Any]:
    """拉取来客 PC 后台账号列表单页。"""
    response = await client.get(
        f"{PC_BASE_URL}{PC_ACCOUNT_LIST_ENDPOINT}",
        params={
            "root_life_account_id": root_life_account_id,
            "page": page,
            "auth": 1,
            "extend": 1,
            "only_need_root": 0,
            "account_type": 20,
        },
    )
    response.raise_for_status()
    body = response.json()
    if _safe_dict(body).get("status_code") not in (0, "0"):
        raise RuntimeError(f"来客PC账号列表接口返回错误: {_safe_dict(body).get('status_msg') or _safe_dict(body).get('status_code')}")
    return body


async def _fetch_bind_list_page(
    client: httpx.AsyncClient,
    *,
    root_life_account_id: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """拉取来客 PC 抖音号管理列表单页。"""
    response = await client.get(
        f"{PC_BASE_URL}{PC_INTEGRATION_BIND_LIST_ENDPOINT}",
        params={
            "root_life_account_id": root_life_account_id,
            "page": page,
            "size": page_size,
            "sub_tab": 3,
            "aweme_type": 1,
        },
    )
    response.raise_for_status()
    body = response.json()
    if _safe_dict(body).get("status_code") not in (0, "0"):
        raise RuntimeError(f"来客PC抖音号管理接口返回错误: {_safe_dict(body).get('status_msg') or _safe_dict(body).get('status_code')}")
    return body


async def fetch_douyin_poi_account_pages(
    *,
    cookie: str,
    csrf_token: str,
    root_life_account_id: str,
    max_pages: int = 1000,
) -> list[dict[str, Any]]:
    """分页拉取来客 PC 后台门店经营抖音号列表。"""
    all_items: list[dict[str, Any]] = []
    seen_poi_ids: set[str] = set()
    page = 1
    total = 0

    headers = _build_pc_headers(cookie, csrf_token)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True, headers=headers) as client:
        while page <= max_pages:
            body = await _fetch_pc_account_page(
                client,
                root_life_account_id=root_life_account_id,
                page=page,
            )
            items = _extract_pc_account_rows(body)
            if page == 1:
                total = _extract_pc_total(body)
            if not items:
                break

            for item in items:
                binding = _extract_pc_account_binding(item)
                poi_id = binding["poi_id"]
                if _valid_id(poi_id) and poi_id in seen_poi_ids:
                    continue
                if _valid_id(poi_id):
                    seen_poi_ids.add(poi_id)
                all_items.append(item)

            if total and page * len(items) >= total:
                break
            page += 1

    return all_items


async def fetch_douyin_integration_bind_pages(
    *,
    cookie: str,
    csrf_token: str,
    root_life_account_id: str,
    page_size: int = 100,
    max_pages: int = 200,
    concurrency: int = 5,
) -> list[dict[str, Any]]:
    """分页拉取来客 PC 抖音号管理全量经营号列表。"""
    all_items: list[dict[str, Any]] = []
    seen_account_ids: set[str] = set()
    headers = _build_pc_headers(cookie, csrf_token)

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True, headers=headers) as client:
        first_body = await _fetch_bind_list_page(
            client,
            root_life_account_id=root_life_account_id,
            page=1,
            page_size=page_size,
        )
        first_items = _extract_bind_list_rows(first_body)
        total = _extract_bind_list_total(first_body)
        actual_page_size = max(len(first_items), 1)
        page_count = min(max_pages, (total + actual_page_size - 1) // actual_page_size if total else 1)

        bodies: list[dict[str, Any]] = [first_body]
        if _has_more_bind_list(first_body) and page_count > 1:
            semaphore = asyncio.Semaphore(max(concurrency, 1))

            async def fetch_page_with_limit(page: int) -> dict[str, Any]:
                """限制并发拉取分页，避免来客后台接口被瞬时打满。"""
                async with semaphore:
                    return await _fetch_bind_list_page(
                        client,
                        root_life_account_id=root_life_account_id,
                        page=page,
                        page_size=page_size,
                    )

            bodies.extend(await asyncio.gather(*(fetch_page_with_limit(page) for page in range(2, page_count + 1))))

        for body in bodies:
            for item in _extract_bind_list_rows(body):
                account_id = str(item.get("account_id") or "")
                if account_id and account_id in seen_account_ids:
                    continue
                if account_id:
                    seen_account_ids.add(account_id)
                all_items.append(item)

    return all_items


async def sync_douyin_poi_account_bindings(db: Session | None = None) -> dict[str, Any]:
    """同步门店绑定经营抖音号到本地数据库。"""
    own_session = db is None
    session = db or SessionLocal()
    sync_log_id: int | None = None

    try:
        cookie_config = load_cookie()
        root_life_account_id = str(cookie_config.get("account_id") or "").strip()
        pc_cookie = str(cookie_config.get("cookie") or "").strip()
        csrf_token = str(cookie_config.get("csrf_token") or "").strip()
        if not pc_cookie or not root_life_account_id:
            raise RuntimeError("来客 PC 后台 Cookie 或 root_life_account_id 未配置，无法同步门店绑定经营抖音号")

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
        logger.info("门店绑定经营抖音号同步: 开始调用 %s", PC_INTEGRATION_BIND_LIST_ENDPOINT)
        store_name_index = _build_latest_store_name_index(session)
        bind_items = await fetch_douyin_integration_bind_pages(
            cookie=pc_cookie,
            csrf_token=csrf_token,
            root_life_account_id=root_life_account_id,
        )
        exact_items = await fetch_douyin_poi_account_pages(
            cookie=pc_cookie,
            csrf_token=csrf_token,
            root_life_account_id=root_life_account_id,
        )
        query_finished_at = datetime.now()
        sync_time = datetime.now()

        # 旧版本误把 poi_account 当作经营抖音号。新口径同步前先清空，避免继续展示错误数据。
        deleted_count = session.query(DouyinPoiAccountBinding).filter(
            DouyinPoiAccountBinding.account_id == root_life_account_id
        ).delete(synchronize_session=False)
        session.commit()
        session.expire_all()

        saved_count = 0
        skipped_count = 0
        for item in exact_items:
            _upsert_binding_row(
                session,
                account_id=root_life_account_id,
                item=item,
                sync_time=sync_time,
            )
            binding = _extract_pc_account_binding(item)
            if binding["account_type"] == "20" and _valid_id(binding["poi_id"]) and _valid_id(binding["aweme_id"]):
                saved_count += 1
            else:
                skipped_count += 1

        exact_saved_count = saved_count
        session.commit()
        session.expire_all()

        bind_saved_count = 0
        bind_unmatched_count = 0
        bind_skipped_count = 0
        seen_bind_poi_ids: set[str] = set()
        for item in bind_items:
            if _upsert_bind_list_row(
                session,
                account_id=root_life_account_id,
                item=item,
                store_name_index=store_name_index,
                sync_time=sync_time,
                seen_poi_ids=seen_bind_poi_ids,
            ):
                bind_saved_count += 1
            else:
                binding = _extract_bind_list_binding(item)
                if binding["account_type"] == "20":
                    bind_unmatched_count += 1
                else:
                    bind_skipped_count += 1

        session.flush()
        saved_count = session.query(DouyinPoiAccountBinding).filter(
            DouyinPoiAccountBinding.account_id == root_life_account_id
        ).count()

        sync_log = session.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
        if sync_log:
            sync_log.status = "success"
            sync_log.total_records = saved_count
            sync_log.finished_at = datetime.now()
        session.commit()

        result = {
            "account_id": root_life_account_id,
            "record_count": saved_count,
            "candidate_count": len(bind_items) + len(exact_items),
            "bind_list_candidate_count": len(bind_items),
            "exact_candidate_count": len(exact_items),
            "bind_list_saved_count": bind_saved_count,
            "exact_saved_count": exact_saved_count,
            "bind_list_unmatched_count": bind_unmatched_count,
            "bind_list_skipped_count": bind_skipped_count,
            "deleted_wrong_record_count": deleted_count,
            "skipped_non_sub_org_count": skipped_count,
            "api_endpoint": PC_INTEGRATION_BIND_LIST_ENDPOINT,
            "fallback_api_endpoint": PC_ACCOUNT_LIST_ENDPOINT,
            "filter_bind_type": "SUB_ORG",
            "source": "life_douyin_pc_integration_bind_list",
            "query_started_at": query_started_at.isoformat(timespec="seconds"),
            "query_finished_at": query_finished_at.isoformat(timespec="seconds"),
            "last_sync_at": sync_time.isoformat(timespec="seconds"),
        }
        logger.info("门店绑定经营抖音号同步完成: %s", result)
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
        logger.exception("门店绑定经营抖音号同步失败")
        raise
    finally:
        if own_session:
            session.close()


def build_store_poi_account_summary(db: Session, poi_id: Any) -> dict[str, Any]:
    """按门店 ID 构造侧边栏使用的绑定经营抖音号摘要。"""
    poi_id_text = str(poi_id or "").strip()
    empty = {
        "douyin_poi_account_bound": False,
        "douyin_poi_account_id": "",
        "douyin_poi_account_name": "",
        "douyin_poi_account_type": "",
        "douyin_parent_account_id": "",
        "douyin_parent_account_name": "",
        "douyin_root_account_id": "",
        "douyin_root_account_name": "",
        "douyin_poi_account_status": "",
        "douyin_poi_account_last_sync_time": "",
    }
    if not poi_id_text:
        return empty

    rows = (
        db.query(DouyinPoiAccountBinding)
        .filter(DouyinPoiAccountBinding.poi_id == poi_id_text)
        .order_by(DouyinPoiAccountBinding.last_sync_at.desc(), DouyinPoiAccountBinding.id.desc())
        .all()
    )
    row = next((item for item in rows if is_active_poi_account_status(item.source_status)), None)
    if row is None:
        return empty
    status_text = normalize_poi_account_status(row.source_status)

    return {
        "douyin_poi_account_bound": bool(row.poi_account_id or row.poi_account_name),
        "douyin_poi_account_id": row.poi_account_id or "",
        "douyin_poi_account_name": row.poi_account_name or "",
        "douyin_poi_account_type": row.poi_account_type or "",
        "douyin_parent_account_id": row.parent_account_id or "",
        "douyin_parent_account_name": row.parent_account_name or "",
        "douyin_root_account_id": row.root_account_id or "",
        "douyin_root_account_name": row.root_account_name or "",
        "douyin_poi_account_status": status_text,
        "douyin_poi_account_last_sync_time": row.last_sync_at.isoformat(timespec="seconds") if row.last_sync_at else "",
    }


def get_douyin_poi_account_binding_status(db: Session) -> dict[str, Any]:
    """读取门店绑定经营抖音号同步状态，不返回密钥或 token。"""
    last_log = (
        db.query(DataSyncLog)
        .filter(DataSyncLog.sync_type == SYNC_TYPE)
        .order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc())
        .first()
    )
    total_count = db.query(DouyinPoiAccountBinding).count()
    bound_count = (
        db.query(DouyinPoiAccountBinding)
        .filter(DouyinPoiAccountBinding.poi_account_id != "")
        .count()
    )
    return {
        "configured": bool(
            load_cookie().get("cookie")
            and load_cookie().get("account_id")
        ),
        "api_endpoint": PC_INTEGRATION_BIND_LIST_ENDPOINT,
        "fallback_api_endpoint": PC_ACCOUNT_LIST_ENDPOINT,
        "filter_bind_type": "SUB_ORG",
        "total_count": total_count,
        "bound_count": bound_count,
        "last_sync": {
            "status": last_log.status if last_log else "",
            "total_records": last_log.total_records if last_log else 0,
            "error_message": last_log.error_message if last_log else "",
            "started_at": last_log.started_at.isoformat(timespec="seconds") if last_log and last_log.started_at else "",
            "finished_at": last_log.finished_at.isoformat(timespec="seconds") if last_log and last_log.finished_at else "",
        },
    }
