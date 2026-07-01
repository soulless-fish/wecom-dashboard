"""
抖音电脑清灰团购商品同步服务。

该服务使用抖音开放平台商品线上数据列表接口，判断每个门店是否关联指定的
电脑清灰团购商品，并把结果写入独立状态表供企业微信侧边栏读取。
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import (
    DataSyncLog,
    DouyinComputerCleaningStatus,
    SessionLocal,
    StorePerformance,
)
from app.services.douyin_client import DouyinClient
from app.services.douyin_token_manager import DouyinTokenManager


logger = logging.getLogger(__name__)

TARGET_KEYWORD = "笔记本"
TARGET_PRODUCT_IDS = {
    "1835072917422080": "【更换硅脂】笔记本深度清理",
    "1835073554323456": "【更换液金】笔记本深度清理",
    "1839132336458763": "【系统重装】笔记本/台式机系统重装（2选1）",
}
PRODUCT_ONLINE_QUERY_ENDPOINT = "/goodlife/v1/goods/product/online/query/"
SYNC_QUERY_MODE = "fresh_openapi_query_each_run"
SYNC_QUERY_MODE_TEXT = "每次同步都实时调用抖音来客商品线上数据列表接口，不复用历史商品查询结果"


def _json_dumps(value: Any) -> str:
    """把列表或字典保存为中文友好的 JSON 字符串。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads_list(value: Any) -> list[Any]:
    """从数据库 JSON 字符串读取列表，失败时返回空列表。"""
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _safe_int(value: Any, default: int = 0) -> int:
    """安全转换接口中的数字字段。"""
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_value(value: Any, keys: tuple[str, ...]) -> Any:
    """递归读取第一个命中的字段值。"""
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


def _extract_product_id(product_online: dict[str, Any]) -> str:
    """从线上商品对象中提取商品 ID。"""
    product = product_online.get("product") if isinstance(product_online.get("product"), dict) else {}
    sku = product_online.get("sku") if isinstance(product_online.get("sku"), dict) else {}
    return str(
        product.get("product_id")
        or sku.get("sku_id")
        or product_online.get("product_id")
        or _first_value(product_online, ("product_id", "sku_id"))
        or ""
    )


def _extract_product_name(product_online: dict[str, Any]) -> str:
    """从线上商品对象中提取商品名称。"""
    product = product_online.get("product") if isinstance(product_online.get("product"), dict) else {}
    sku = product_online.get("sku") if isinstance(product_online.get("sku"), dict) else {}
    return str(
        product.get("product_name")
        or sku.get("sku_name")
        or product_online.get("product_name")
        or _first_value(product_online, ("product_name", "sku_name", "goods_name", "name", "title"))
        or ""
    )


def _extract_online_status(product_online: dict[str, Any]) -> int:
    """从线上商品对象中提取在线状态。"""
    product = product_online.get("product") if isinstance(product_online.get("product"), dict) else {}
    sku = product_online.get("sku") if isinstance(product_online.get("sku"), dict) else {}
    return _safe_int(
        product_online.get("online_status")
        or product.get("online_status")
        or sku.get("status")
        or _first_value(product_online, ("online_status", "status")),
        default=0,
    )


def _extract_poi_ids(product_online: dict[str, Any]) -> list[str]:
    """从商品关联门店列表中提取所有 poi_id。"""
    product = product_online.get("product") if isinstance(product_online.get("product"), dict) else {}
    poi_ids: list[str] = []

    pois = product.get("pois")
    if isinstance(pois, list):
        for poi in pois:
            if not isinstance(poi, dict):
                continue
            poi_id = str(poi.get("poi_id") or poi.get("supplier_ext_id") or "").strip()
            if poi_id and poi_id not in poi_ids:
                poi_ids.append(poi_id)

    fallback_poi_id = str(_first_value(product_online, ("poi_id", "poi_id_str", "intention_poi_id")) or "").strip()
    if fallback_poi_id and fallback_poi_id not in poi_ids:
        poi_ids.append(fallback_poi_id)

    return poi_ids


def _extract_online_products(body: dict[str, Any]) -> list[dict[str, Any]]:
    """从商品接口响应中提取线上商品列表。"""
    data = body.get("data") if isinstance(body, dict) else {}
    if not isinstance(data, dict):
        return []
    products = data.get("products") or data.get("product_onlines") or data.get("product_list") or []
    return [item for item in products if isinstance(item, dict)] if isinstance(products, list) else []


async def fetch_computer_cleaning_online_products(client: DouyinClient, max_pages: int = 20) -> list[dict[str, Any]]:
    """分页实时拉取名称包含“笔记本”的在线团购商品。"""
    products: list[dict[str, Any]] = []
    cursor = ""

    for _page in range(max(max_pages, 1)):
        params: dict[str, Any] = {
            "account_id": client.token_manager.get_account_id(),
            "count": 20,
            "product_name": TARGET_KEYWORD,
            # 必须查询商品全量关联门店，否则 product.pois 只会返回部分门店，导致已开通门店数偏小。
            "query_all_poi": True,
            "status": 1,
        }
        if cursor:
            params["cursor"] = cursor

        body = await client.call_api(
            "GET",
            PRODUCT_ONLINE_QUERY_ENDPOINT,
            params=params,
            use_account_header=True,
        )
        data = body.get("data") if isinstance(body, dict) else {}
        if not isinstance(data, dict):
            break

        products.extend(_extract_online_products(body))

        if not data.get("has_more"):
            break
        next_cursor = str(data.get("next_cursor") or "").strip()
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    return products


def _build_open_products_by_poi(products: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """把目标商品展开为 poi_id -> 商品摘要列表。"""
    opened_by_poi: dict[str, list[dict[str, str]]] = {}

    for product_online in products:
        product_id = _extract_product_id(product_online)
        if product_id not in TARGET_PRODUCT_IDS:
            continue
        if _extract_online_status(product_online) != 1:
            continue

        product_name = _extract_product_name(product_online) or TARGET_PRODUCT_IDS[product_id]
        product_summary = {
            "product_id": product_id,
            "product_name": product_name,
        }
        for poi_id in _extract_poi_ids(product_online):
            opened_by_poi.setdefault(poi_id, [])
            if product_summary not in opened_by_poi[poi_id]:
                opened_by_poi[poi_id].append(product_summary)

    return opened_by_poi


def _latest_store_poi_ids(db: Session) -> tuple[str, set[str]]:
    """读取最新月份门店业绩表中的门店 ID 集合。"""
    latest_month = db.query(func.max(StorePerformance.data_month)).scalar() or datetime.now().strftime("%Y-%m")
    rows = (
        db.query(StorePerformance.poi_id)
        .filter(StorePerformance.data_month == latest_month)
        .filter(StorePerformance.poi_id.isnot(None))
        .filter(StorePerformance.poi_id != "")
        .all()
    )
    return latest_month, {str(row[0]) for row in rows if row[0]}


def _existing_status_poi_ids(db: Session, account_id: str) -> set[str]:
    """读取已有状态表中的门店 ID，避免商品下架后残留旧开通状态。"""
    rows = (
        db.query(DouyinComputerCleaningStatus.poi_id)
        .filter(DouyinComputerCleaningStatus.account_id == account_id)
        .all()
    )
    return {str(row[0]) for row in rows if row[0]}


def _upsert_status_row(
    db: Session,
    *,
    account_id: str,
    poi_id: str,
    matched_products: list[dict[str, str]],
    sync_time: datetime,
) -> None:
    """新增或更新单个门店的电脑清灰开通状态。"""
    product_ids = [item["product_id"] for item in matched_products]
    product_names = [item["product_name"] for item in matched_products]
    row = (
        db.query(DouyinComputerCleaningStatus)
        .filter(
            DouyinComputerCleaningStatus.account_id == account_id,
            DouyinComputerCleaningStatus.poi_id == poi_id,
        )
        .first()
    )
    if row is None:
        row = DouyinComputerCleaningStatus(account_id=account_id, poi_id=poi_id)
        db.add(row)

    row.is_opened = 1 if matched_products else 0
    row.matched_product_ids = _json_dumps(product_ids)
    row.matched_product_names = _json_dumps(product_names)
    row.matched_product_count = len(matched_products)
    row.source_status = "success"
    row.last_sync_at = sync_time


async def sync_douyin_computer_cleaning_status(db: Session | None = None) -> dict[str, Any]:
    """实时查询抖音接口，并同步电脑清灰团购开通状态到本地数据库。"""
    own_session = db is None
    session = db or SessionLocal()
    sync_log_id: int | None = None
    data_month = datetime.now().strftime("%Y-%m")

    try:
        sync_log = DataSyncLog(
            sync_type="douyin_computer_cleaning_status",
            data_month=data_month,
            status="running",
            started_at=datetime.now(),
        )
        session.add(sync_log)
        session.commit()
        session.refresh(sync_log)
        sync_log_id = sync_log.id

        settings = get_settings()
        if not settings.douyin_client_key or not settings.douyin_client_secret or not settings.douyin_account_id:
            raise RuntimeError("抖音开放平台配置不完整，无法同步电脑清灰团购商品状态")

        client = DouyinClient(DouyinTokenManager(settings))
        api_query_started_at = datetime.now()
        logger.info(
            "电脑清灰团购状态同步: 开始实时调用抖音接口 %s，query_all_poi=true，本次不复用历史商品查询结果",
            PRODUCT_ONLINE_QUERY_ENDPOINT,
        )
        online_products = await fetch_computer_cleaning_online_products(client)
        api_query_finished_at = datetime.now()
        opened_by_poi = _build_open_products_by_poi(online_products)

        latest_month, store_poi_ids = _latest_store_poi_ids(session)
        all_poi_ids = store_poi_ids | set(opened_by_poi.keys()) | _existing_status_poi_ids(session, settings.douyin_account_id)
        sync_time = datetime.now()

        for poi_id in sorted(all_poi_ids):
            _upsert_status_row(
                session,
                account_id=settings.douyin_account_id,
                poi_id=poi_id,
                matched_products=opened_by_poi.get(poi_id, []),
                sync_time=sync_time,
            )

        sync_log = session.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
        if sync_log:
            sync_log.status = "success"
            sync_log.total_records = len(all_poi_ids)
            sync_log.finished_at = datetime.now()
        session.commit()

        result = {
            "account_id": settings.douyin_account_id,
            "data_month": latest_month,
            "target_product_ids": sorted(TARGET_PRODUCT_IDS.keys()),
            "online_product_count": len(online_products),
            "target_product_count": len({item["product_id"] for products in opened_by_poi.values() for item in products}),
            "opened_poi_count": len(opened_by_poi),
            "store_poi_count": len(store_poi_ids),
            "updated_status_count": len(all_poi_ids),
            "sync_query_mode": SYNC_QUERY_MODE,
            "sync_query_mode_text": SYNC_QUERY_MODE_TEXT,
            "api_endpoint": PRODUCT_ONLINE_QUERY_ENDPOINT,
            "query_all_poi": True,
            "api_query_started_at": api_query_started_at.isoformat(timespec="seconds"),
            "api_query_finished_at": api_query_finished_at.isoformat(timespec="seconds"),
            "last_sync_at": sync_time.isoformat(timespec="seconds"),
        }
        logger.info("电脑清灰团购状态同步完成: %s", result)
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
        logger.exception("电脑清灰团购状态同步失败")
        raise
    finally:
        if own_session:
            session.close()


def build_store_computer_cleaning_summary(db: Session, poi_id: Any) -> dict[str, Any]:
    """构造侧边栏使用的电脑清灰开通状态摘要。"""
    poi_id_text = str(poi_id or "").strip()
    if not poi_id_text:
        return {
            "computer_cleaning_opened": False,
            "computer_cleaning_status": "未开通",
            "computer_cleaning_product_ids": [],
            "computer_cleaning_product_names": [],
            "computer_cleaning_last_sync_time": "",
        }

    row = (
        db.query(DouyinComputerCleaningStatus)
        .filter(DouyinComputerCleaningStatus.poi_id == poi_id_text)
        .order_by(DouyinComputerCleaningStatus.last_sync_at.desc(), DouyinComputerCleaningStatus.id.desc())
        .first()
    )
    if row is None:
        return {
            "computer_cleaning_opened": False,
            "computer_cleaning_status": "未开通",
            "computer_cleaning_product_ids": [],
            "computer_cleaning_product_names": [],
            "computer_cleaning_last_sync_time": "",
        }

    is_opened = bool(row.is_opened)
    return {
        "computer_cleaning_opened": is_opened,
        "computer_cleaning_status": "已开通" if is_opened else "未开通",
        "computer_cleaning_product_ids": _json_loads_list(row.matched_product_ids),
        "computer_cleaning_product_names": _json_loads_list(row.matched_product_names),
        "computer_cleaning_last_sync_time": row.last_sync_at.isoformat(timespec="seconds") if row.last_sync_at else "",
    }


def get_douyin_computer_cleaning_status(db: Session) -> dict[str, Any]:
    """读取电脑清灰团购同步状态，不返回任何密钥信息。"""
    last_log = (
        db.query(DataSyncLog)
        .filter(DataSyncLog.sync_type == "douyin_computer_cleaning_status")
        .order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc())
        .first()
    )
    status_count = db.query(DouyinComputerCleaningStatus).count()
    opened_count = (
        db.query(DouyinComputerCleaningStatus)
        .filter(DouyinComputerCleaningStatus.is_opened == 1)
        .count()
    )
    return {
        "configured": bool(
            get_settings().douyin_client_key
            and get_settings().douyin_client_secret
            and get_settings().douyin_account_id
        ),
        "target_product_ids": sorted(TARGET_PRODUCT_IDS.keys()),
        "sync_query_mode": SYNC_QUERY_MODE,
        "sync_query_mode_text": SYNC_QUERY_MODE_TEXT,
        "api_endpoint": PRODUCT_ONLINE_QUERY_ENDPOINT,
        "query_all_poi": True,
        "status_count": status_count,
        "opened_count": opened_count,
        "last_sync": {
            "status": last_log.status if last_log else "",
            "total_records": last_log.total_records if last_log else 0,
            "error_message": last_log.error_message if last_log else "",
            "started_at": last_log.started_at.isoformat(timespec="seconds") if last_log and last_log.started_at else "",
            "finished_at": last_log.finished_at.isoformat(timespec="seconds") if last_log and last_log.finished_at else "",
        },
    }
