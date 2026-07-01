"""
抖音开放平台订单同步服务

该服务负责调用抖音官方 trade 订单列表接口，把账号级订单分页同步到本地明细表，
侧边栏再按门店 poi_id 从本地数据库聚合近30天订单数据。
"""

from __future__ import annotations

import ast
import json
import logging
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

import httpx
from sqlalchemy import func, or_
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import DataSyncLog, DouyinOfficialOrder, SessionLocal
from app.services.douyin_token_manager import DouyinTokenManager


logger = logging.getLogger(__name__)

DOUYIN_BASE_URL = "https://open.douyin.com"
TRADE_ORDER_QUERY_PATH = "/goodlife/v1/trade/order/query/"
DEFAULT_PAGE_SIZE = 200
DEFAULT_SYNC_DAYS = 30
DEFAULT_MAX_PAGES = 50
DEFAULT_WINDOW_HOURS = 24

# 当前正式订单样本中 order_status=201 且 certificate.item_status=100 对应已核销。
VERIFIED_DOUYIN_ORDER_STATUSES = (201,)
VERIFIED_CERTIFICATE_STATUSES = {"100"}
CATEGORY_APPLE_BATTERY = "apple_battery"
CATEGORY_APPLE_CELL = "apple_cell"
CATEGORY_ANDROID_BATTERY = "android_battery"
CATEGORY_ANDROID_CELL = "android_cell"
ROOT_CATEGORY_BATTERY = "battery"
ROOT_CATEGORY_CELL = "cell"
CATEGORY_NAMES = {
    CATEGORY_APPLE_BATTERY: "苹果电池",
    CATEGORY_APPLE_CELL: "苹果电芯",
    CATEGORY_ANDROID_BATTERY: "安卓电池",
    CATEGORY_ANDROID_CELL: "安卓电芯",
}
CATEGORY_ROOTS = {
    CATEGORY_APPLE_BATTERY: ROOT_CATEGORY_BATTERY,
    CATEGORY_ANDROID_BATTERY: ROOT_CATEGORY_BATTERY,
    CATEGORY_APPLE_CELL: ROOT_CATEGORY_CELL,
    CATEGORY_ANDROID_CELL: ROOT_CATEGORY_CELL,
}
SENSITIVE_RAW_KEYS = {
    "token",
    "secret",
    "phone",
    "mobile",
    "tel",
    "open_id",
    "openid",
    "uid",
    "user_id",
    "certificate",
    "encrypted",
    "encrypt",
}


def safe_int(value: Any, default: int = 0) -> int:
    """安全转换整数，接口字段缺失或格式异常时返回默认值。"""
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def fen_to_yuan(value: Any) -> Decimal:
    """把接口返回的分转换成元，保持两位小数。"""
    return (Decimal(safe_int(value, 0)) / Decimal("100")).quantize(Decimal("0.01"))


def normalize_text(value: Any) -> str:
    """规整商品文案，用于抖音订单商品分类。"""
    text = str(value or "").strip().lower()
    text = text.replace("：", ":")
    return re.sub(r"\s+", "", text)


def parse_timestamp(value: Any) -> Optional[datetime]:
    """把秒级或毫秒级时间戳转换成数据库时间。"""
    timestamp = safe_int(value, 0)
    if timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    try:
        return datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        return None


def normalize_error_code(value: Any) -> int:
    """统一处理抖音接口中 0、空字符串、None 三种成功码。"""
    if value in (None, "", 0, "0"):
        return 0
    return safe_int(value, -1)


def mask_value(value: Any, keep_start: int = 4, keep_end: int = 4) -> str:
    """脱敏敏感字段，只保留首尾少量字符用于排查。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep_start + keep_end:
        return "*" * len(text)
    return f"{text[:keep_start]}...{text[-keep_end:]}"


def sanitize_order_payload(value: Any) -> Any:
    """递归脱敏订单原始响应，避免落库手机号、券码、open_id 等敏感内容。"""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(marker in lowered for marker in SENSITIVE_RAW_KEYS):
                sanitized[key_text] = mask_value(item)
            else:
                sanitized[key_text] = sanitize_order_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_order_payload(item) for item in value]
    return value


def dumps_sanitized_order(order: dict[str, Any]) -> str:
    """生成脱敏后的订单 JSON 字符串，超过 TEXT 安全长度时截断。"""
    raw_text = json.dumps(sanitize_order_payload(order), ensure_ascii=False, separators=(",", ":"))
    max_length = 60000
    if len(raw_text) <= max_length:
        return raw_text
    return raw_text[:max_length] + "...已截断"


def parse_certificate_payload(value: Any) -> list[Any]:
    """解析券码字段，只用于统计数量和状态，不保存券码明文。"""
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
            except Exception:
                continue
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        return [text]
    return [value]


def build_certificate_summary(value: Any) -> tuple[int, str, int, Optional[datetime]]:
    """根据券码字段生成数量、状态摘要、已核销数量和最后核销时间。"""
    certificates = parse_certificate_payload(value)
    if not certificates:
        return 0, "", 0, None

    status_counter: Counter[str] = Counter()
    verified_count = 0
    verify_times: list[datetime] = []
    for certificate in certificates:
        if isinstance(certificate, dict):
            status = (
                certificate.get("item_status")
                or certificate.get("certificate_status")
                or certificate.get("status")
                or certificate.get("verify_status")
                or certificate.get("code_status")
                or "未知"
            )
            status_text = str(status)
            status_counter[f"item_status={status_text}" if certificate.get("item_status") is not None else status_text] += 1
            if status_text in VERIFIED_CERTIFICATE_STATUSES:
                verified_count += 1
                verify_time = parse_timestamp(certificate.get("item_update_time"))
                if verify_time:
                    verify_times.append(verify_time)
        else:
            status_counter["未解析"] += 1

    summary = "，".join(f"{status}:{count}" for status, count in sorted(status_counter.items()))
    latest_verify_time = max(verify_times) if verify_times else None
    return len(certificates), summary[:255], verified_count, latest_verify_time


def classify_douyin_product(product_name: Any) -> tuple[str, str, str, str]:
    """按抖音团购商品名识别电池/电芯类别。"""
    text = normalize_text(product_name)
    if not text:
        return "", "", "", ""

    excluded_keywords = ("贴膜", "钢化膜", "硅脂", "清理", "代金券", "其他维修", "屏幕", "官屏", "官方屏")
    if any(keyword in text for keyword in excluded_keywords) and not any(keyword in text for keyword in ("电池", "电芯", "官电", "官方电")):
        return "", "", "", ""

    is_android = "安卓" in text
    is_apple = any(keyword in text for keyword in ("苹果", "iphone", "ipad"))
    has_cell = "电芯" in text
    has_battery = any(keyword in text for keyword in ("电池", "官电", "官方电"))

    if has_cell and not has_battery:
        category = CATEGORY_ANDROID_CELL if is_android else CATEGORY_APPLE_CELL
        return category, CATEGORY_NAMES[category], CATEGORY_ROOTS[category], "商品名关键词:电芯"

    if has_battery:
        category = CATEGORY_ANDROID_BATTERY if is_android and not is_apple else CATEGORY_APPLE_BATTERY
        return category, CATEGORY_NAMES[category], CATEGORY_ROOTS[category], "商品名关键词:电池"

    return "", "", "", ""


def first_dict(items: Any) -> dict[str, Any]:
    """从列表里取第一条字典数据，字段缺失时返回空字典。"""
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def as_dict(value: Any) -> dict[str, Any]:
    """确保接口嵌套字段是字典，异常结构统一按空字典处理。"""
    return value if isinstance(value, dict) else {}


def extract_page_orders(body: Any) -> list[dict[str, Any]]:
    """从抖音订单接口响应中提取订单列表。"""
    if not isinstance(body, dict):
        return []
    data = body.get("data") or {}
    if not isinstance(data, dict):
        return []
    orders = data.get("orders") or data.get("order_list") or []
    return [order for order in orders if isinstance(order, dict)] if isinstance(orders, list) else []


def extract_page_total(body: Any) -> Optional[int]:
    """读取接口分页总数，无法读取时返回 None。"""
    if not isinstance(body, dict):
        return None
    data = body.get("data") or {}
    if not isinstance(data, dict):
        return None
    page = data.get("page") or {}
    if isinstance(page, dict) and page.get("total") is not None:
        return safe_int(page.get("total"), 0)
    if data.get("total") is not None:
        return safe_int(data.get("total"), 0)
    return None


def check_douyin_response(body: dict[str, Any]) -> None:
    """检查抖音接口响应码，失败时抛出包含官方描述的异常。"""
    data = body.get("data") or {}
    extra = body.get("extra") or {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(extra, dict):
        extra = {}

    data_code = normalize_error_code(data.get("error_code"))
    extra_code = normalize_error_code(extra.get("error_code"))
    if data_code != 0:
        message = data.get("description") or "抖音订单接口返回失败"
        raise RuntimeError(f"{message} (data.error_code={data_code})")
    if extra_code != 0:
        message = extra.get("description") or "抖音订单接口返回失败"
        raise RuntimeError(f"{message} (extra.error_code={extra_code})")


async def fetch_trade_order_page(
    *,
    http_client: httpx.AsyncClient,
    token_manager: DouyinTokenManager,
    account_id: str,
    start_time: int,
    end_time: int,
    page_num: int,
    page_size: int,
) -> dict[str, Any]:
    """调用抖音官方 trade 订单列表接口的一页数据。"""
    access_token = await token_manager.get_access_token()
    headers = {
        "content-type": "application/json",
        "access-token": access_token,
        "Rpc-Transit-Life-Account": account_id,
    }
    params = {
        "account_id": account_id,
        "create_order_start_time": start_time,
        "create_order_end_time": end_time,
        "page_num": page_num,
        "page_size": page_size,
    }
    response = await http_client.get(
        f"{DOUYIN_BASE_URL}{TRADE_ORDER_QUERY_PATH}",
        params=params,
        headers=headers,
    )
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"抖音订单接口返回非JSON响应: {response.text[:200]}") from exc
    if not isinstance(body, dict):
        raise RuntimeError("抖音订单接口返回结构不是对象")
    check_douyin_response(body)
    return body


def build_sync_windows(
    *,
    days: int = DEFAULT_SYNC_DAYS,
    hours: Optional[int] = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    now: Optional[datetime] = None,
) -> list[tuple[datetime, datetime]]:
    """按时间倒序生成同步窗口，便于受页数限制时优先覆盖最新订单。"""
    end_dt = now or datetime.now()
    if hours is not None:
        start_dt = end_dt - timedelta(hours=max(hours, 1))
    else:
        start_dt = end_dt - timedelta(days=max(days, 1))

    resolved_window_hours = max(1, window_hours)
    windows: list[tuple[datetime, datetime]] = []
    cursor_end = end_dt
    while cursor_end > start_dt:
        cursor_start = max(start_dt, cursor_end - timedelta(hours=resolved_window_hours))
        windows.append((cursor_start, cursor_end))
        cursor_end = cursor_start
    return windows


def build_order_row(
    order: dict[str, Any],
    *,
    account_id: str,
    sync_start_date: date,
    sync_end_date: date,
) -> Optional[dict[str, Any]]:
    """把抖音订单响应转换成数据库可写入的字段字典。"""
    order_id = str(order.get("order_id") or "").strip()
    if not order_id:
        return None

    merchant_info = as_dict(order.get("merchant_info"))
    poi = as_dict(order.get("poi"))
    amount_info = as_dict(order.get("amount_info"))
    products = order.get("products") or order.get("goods") or []
    first_product = first_dict(products)
    sub_order_info = first_dict(order.get("sub_order_amount_infos"))
    certificate_count, certificate_status_summary, verified_certificate_count, verify_time = build_certificate_summary(order.get("certificate"))
    sku_name = str(order.get("sku_name") or first_product.get("sku_name") or first_product.get("product_name") or "")
    product_name = str(first_product.get("product_name") or first_product.get("sku_name") or order.get("sku_name") or "")
    category, category_name, root_category, match_rule = classify_douyin_product(sku_name or product_name)

    resolved_account_id = str(merchant_info.get("account_id") or account_id)
    pay_amount_fen = safe_int(
        order.get("pay_amount")
        or order.get("order_pay_amount")
        or amount_info.get("pay_amount")
        or amount_info.get("order_pay_amount"),
        0,
    )
    original_amount_fen = safe_int(
        order.get("original_amount")
        or amount_info.get("origin_amount"),
        0,
    )
    receipt_amount_fen = safe_int(
        order.get("receipt_amount")
        or sub_order_info.get("receipt_amount")
        or pay_amount_fen,
        0,
    )

    now_dt = datetime.now()
    return {
        "sync_start_date": sync_start_date,
        "sync_end_date": sync_end_date,
        "account_id": resolved_account_id,
        "account_name": str(merchant_info.get("account_name") or ""),
        "order_id": order_id,
        "order_status": safe_int(order.get("order_status"), 0),
        "order_type": safe_int(order.get("order_type"), 0),
        "poi_id": str(order.get("poi_id") or poi.get("poi_id") or ""),
        "intention_poi_id": str(order.get("intention_poi_id") or poi.get("intention_poi_id") or ""),
        "poi_name": str(order.get("poi_name") or poi.get("poi_name") or ""),
        "pay_amount_fen": pay_amount_fen,
        "original_amount_fen": original_amount_fen,
        "receipt_amount_fen": receipt_amount_fen,
        "pay_amount_yuan": fen_to_yuan(pay_amount_fen),
        "receipt_amount_yuan": fen_to_yuan(receipt_amount_fen),
        "sku_id": str(order.get("sku_id") or first_product.get("sku_id") or ""),
        "sku_name": sku_name,
        "product_id": str(first_product.get("product_id") or first_product.get("sku_id") or ""),
        "product_name": product_name,
        "third_sku_id": str(order.get("third_sku_id") or first_product.get("third_sku_id") or ""),
        "sub_order_id": str(sub_order_info.get("sub_order_id") or ""),
        "product_category": category,
        "product_category_name": category_name,
        "product_root_category": root_category,
        "product_match_rule": match_rule,
        "certificate_count": certificate_count,
        "verified_certificate_count": verified_certificate_count,
        "certificate_status_summary": certificate_status_summary,
        "create_order_time": parse_timestamp(order.get("create_order_time")),
        "pay_time": parse_timestamp(order.get("pay_time")),
        "verify_time": verify_time,
        "update_order_time": parse_timestamp(order.get("update_order_time")),
        "raw_order_json": dumps_sanitized_order(order),
        "created_at": now_dt,
        "updated_at": now_dt,
    }


def upsert_order_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    """按 account_id + order_id 幂等写入抖音订单。"""
    if not rows:
        return 0

    stmt = mysql_insert(DouyinOfficialOrder).values(rows)
    update_columns = {
        column.name: stmt.inserted[column.name]
        for column in DouyinOfficialOrder.__table__.columns
        if column.name not in {"id", "created_at"}
    }
    update_columns["updated_at"] = datetime.now()
    db.execute(stmt.on_duplicate_key_update(**update_columns))
    db.commit()
    return len(rows)


async def sync_douyin_official_orders(
    *,
    days: int = DEFAULT_SYNC_DAYS,
    hours: Optional[int] = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_size: int = DEFAULT_PAGE_SIZE,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> dict[str, Any]:
    """同步抖音官方订单到本地数据库。"""
    settings = get_settings()
    if not settings.douyin_client_key or not settings.douyin_client_secret:
        raise RuntimeError("抖音开放平台凭据未配置")
    account_id = (settings.douyin_account_id or "").strip()
    if not account_id:
        raise RuntimeError("抖音 account_id 未配置")

    resolved_page_size = min(max(page_size, 1), DEFAULT_PAGE_SIZE)
    resolved_max_pages = max(max_pages, 1)
    windows = build_sync_windows(days=days, hours=hours, window_hours=window_hours)
    token_manager = DouyinTokenManager(settings)

    db = SessionLocal()
    sync_log_id: Optional[int] = None
    total_pages = 0
    total_orders = 0
    total_saved = 0
    stopped_by_max_pages = False
    window_summaries: list[dict[str, Any]] = []
    started_at = datetime.now()
    end_month = started_at.strftime("%Y-%m")

    try:
        sync_log = DataSyncLog(
            sync_type="douyin_official_orders",
            data_month=end_month,
            status="running",
            started_at=started_at,
        )
        db.add(sync_log)
        db.commit()
        db.refresh(sync_log)
        sync_log_id = sync_log.id

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as http_client:
            for window_start, window_end in windows:
                if total_pages >= resolved_max_pages:
                    stopped_by_max_pages = True
                    break

                page_num = 1
                window_orders = 0
                window_saved = 0
                page_total: Optional[int] = None
                while total_pages < resolved_max_pages:
                    body = await fetch_trade_order_page(
                        http_client=http_client,
                        token_manager=token_manager,
                        account_id=account_id,
                        start_time=int(window_start.timestamp()),
                        end_time=int(window_end.timestamp()),
                        page_num=page_num,
                        page_size=resolved_page_size,
                    )
                    orders = extract_page_orders(body)
                    page_total = extract_page_total(body)
                    rows = [
                        row
                        for order in orders
                        if (row := build_order_row(
                            order,
                            account_id=account_id,
                            sync_start_date=window_start.date(),
                            sync_end_date=window_end.date(),
                        ))
                    ]
                    saved_count = upsert_order_rows(db, rows)

                    total_pages += 1
                    total_orders += len(orders)
                    total_saved += saved_count
                    window_orders += len(orders)
                    window_saved += saved_count

                    logger.info(
                        "抖音官方订单同步: 窗口%s~%s 第%s页，订单%s条，累计页%s/%s",
                        window_start.isoformat(timespec="seconds"),
                        window_end.isoformat(timespec="seconds"),
                        page_num,
                        len(orders),
                        total_pages,
                        resolved_max_pages,
                    )

                    if len(orders) < resolved_page_size:
                        break
                    if page_total is not None and page_num * resolved_page_size >= page_total:
                        break

                    page_num += 1

                window_summaries.append(
                    {
                        "start_time": window_start.isoformat(timespec="seconds"),
                        "end_time": window_end.isoformat(timespec="seconds"),
                        "orders": window_orders,
                        "saved_rows": window_saved,
                        "page_total": page_total,
                    }
                )

        sync_log = db.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
        if sync_log:
            sync_log.status = "success"
            sync_log.total_records = total_saved
            sync_log.finished_at = datetime.now()
            db.commit()

        return {
            "success": True,
            "account_id": account_id,
            "days": days,
            "hours": hours,
            "window_hours": window_hours,
            "page_size": resolved_page_size,
            "max_pages": resolved_max_pages,
            "total_pages": total_pages,
            "total_orders": total_orders,
            "saved_rows": total_saved,
            "stopped_by_max_pages": stopped_by_max_pages or total_pages >= resolved_max_pages,
            "windows": window_summaries[:20],
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as exc:
        db.rollback()
        if sync_log_id:
            log_db = SessionLocal()
            try:
                sync_log = log_db.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
                if sync_log:
                    sync_log.status = "failed"
                    sync_log.error_message = str(exc)
                    sync_log.finished_at = datetime.now()
                    log_db.commit()
            finally:
                log_db.close()
        raise
    finally:
        db.close()


def douyin_order_status_text(order_status: Any) -> str:
    """把常见抖音订单状态转换成侧边栏展示文案。"""
    status = safe_int(order_status, 0)
    status_map = {
        1: "已支付",
        101: "未完成",
        201: "已核销",
    }
    return status_map.get(status, f"状态{status}")


def build_store_douyin_order_summary(db: Session, poi_id: Any) -> dict[str, Any]:
    """按门店 poi_id 汇总近30天抖音已核销电池/电芯订单数。"""
    poi_id_text = str(poi_id or "").strip()
    empty_result = {
        "douyin_order_matched": False,
        "douyin_order_status": "no_poi_id" if not poi_id_text else "no_order",
        "douyin_order_date_range": "近30天核销",
        "douyin_order_count": 0,
        "douyin_total_order_count": 0,
        "douyin_verified_order_count": 0,
        "douyin_battery_order_count": 0,
        "douyin_cell_order_count": 0,
        "douyin_apple_battery_order_count": 0,
        "douyin_apple_cell_order_count": 0,
        "douyin_android_battery_order_count": 0,
        "douyin_android_cell_order_count": 0,
        "douyin_pay_amount": 0.0,
        "douyin_receipt_amount": 0.0,
        "douyin_certificate_count": 0,
        "douyin_verified_certificate_count": 0,
        "douyin_verify_time_source": "certificate.item_update_time",
        "douyin_last_sync_time": "",
        "douyin_recent_orders": [],
    }
    if not poi_id_text:
        return empty_result

    start_dt = datetime.now() - timedelta(days=30)
    base_filters = [
        DouyinOfficialOrder.verify_time >= start_dt,
        or_(
            DouyinOfficialOrder.poi_id == poi_id_text,
            DouyinOfficialOrder.intention_poi_id == poi_id_text,
        ),
        DouyinOfficialOrder.order_status.in_(VERIFIED_DOUYIN_ORDER_STATUSES),
        DouyinOfficialOrder.verified_certificate_count > 0,
    ]

    rows = (
        db.query(DouyinOfficialOrder)
        .filter(*base_filters)
        .filter(DouyinOfficialOrder.product_root_category.in_([ROOT_CATEGORY_BATTERY, ROOT_CATEGORY_CELL]))
        .order_by(
            DouyinOfficialOrder.verify_time.desc(),
            DouyinOfficialOrder.pay_time.desc(),
            DouyinOfficialOrder.id.desc(),
        )
        .all()
    )
    if not rows:
        return empty_result

    battery_order_ids = {row.order_id for row in rows if row.product_root_category == ROOT_CATEGORY_BATTERY}
    cell_order_ids = {row.order_id for row in rows if row.product_root_category == ROOT_CATEGORY_CELL}
    apple_battery_order_ids = {row.order_id for row in rows if row.product_category == CATEGORY_APPLE_BATTERY}
    apple_cell_order_ids = {row.order_id for row in rows if row.product_category == CATEGORY_APPLE_CELL}
    android_battery_order_ids = {row.order_id for row in rows if row.product_category == CATEGORY_ANDROID_BATTERY}
    android_cell_order_ids = {row.order_id for row in rows if row.product_category == CATEGORY_ANDROID_CELL}
    total_order_ids = battery_order_ids | cell_order_ids
    pay_amount = sum(float(row.pay_amount_yuan or 0) for row in rows)
    receipt_amount = sum(float(row.receipt_amount_yuan or 0) for row in rows)
    certificate_count = sum(int(row.certificate_count or 0) for row in rows)
    verified_certificate_count = sum(int(row.verified_certificate_count or 0) for row in rows)
    last_sync_dt = max((row.updated_at for row in rows if row.updated_at), default=None)
    last_sync_time = last_sync_dt.isoformat(timespec="seconds") if last_sync_dt else ""

    recent_rows = rows[:5]
    recent_orders = [
        {
            "order_id": row.order_id,
            "order_status": row.order_status,
            "order_status_text": douyin_order_status_text(row.order_status),
            "pay_amount": float(row.pay_amount_yuan or 0),
            "receipt_amount": float(row.receipt_amount_yuan or 0),
            "sku_name": row.sku_name or row.product_name or "",
            "product_name": row.product_name or row.sku_name or "",
            "pay_time": row.pay_time.isoformat(timespec="seconds") if row.pay_time else "",
            "verify_time": row.verify_time.isoformat(timespec="seconds") if row.verify_time else "",
            "create_order_time": row.create_order_time.isoformat(timespec="seconds") if row.create_order_time else "",
        }
        for row in recent_rows
    ]

    return {
        "douyin_order_matched": True,
        "douyin_order_status": "matched",
        "douyin_order_date_range": "近30天核销",
        "douyin_order_count": len(total_order_ids),
        "douyin_total_order_count": len(total_order_ids),
        "douyin_verified_order_count": len(total_order_ids),
        "douyin_battery_order_count": len(battery_order_ids),
        "douyin_cell_order_count": len(cell_order_ids),
        "douyin_apple_battery_order_count": len(apple_battery_order_ids),
        "douyin_apple_cell_order_count": len(apple_cell_order_ids),
        "douyin_android_battery_order_count": len(android_battery_order_ids),
        "douyin_android_cell_order_count": len(android_cell_order_ids),
        "douyin_pay_amount": round(pay_amount, 2),
        "douyin_receipt_amount": round(receipt_amount, 2),
        "douyin_certificate_count": certificate_count,
        "douyin_verified_certificate_count": verified_certificate_count,
        "douyin_verify_time_source": "certificate.item_update_time",
        "douyin_last_sync_time": last_sync_time,
        "douyin_recent_orders": recent_orders,
    }


def get_douyin_official_order_status(db: Session) -> dict[str, Any]:
    """返回抖音官方订单本地同步状态，不包含密钥和 token。"""
    total_orders = int(db.query(func.count(DouyinOfficialOrder.id)).scalar() or 0)
    latest_log = (
        db.query(DataSyncLog)
        .filter(DataSyncLog.sync_type == "douyin_official_orders")
        .order_by(DataSyncLog.started_at.desc())
        .first()
    )
    range_row = (
        db.query(
            func.min(DouyinOfficialOrder.create_order_time),
            func.max(DouyinOfficialOrder.create_order_time),
            func.max(DouyinOfficialOrder.updated_at),
            func.count(func.distinct(DouyinOfficialOrder.poi_id)),
        )
        .first()
    )
    settings = get_settings()

    return {
        "configured": bool(settings.douyin_client_key and settings.douyin_client_secret and settings.douyin_account_id),
        "account_id": settings.douyin_account_id,
        "total_orders": total_orders,
        "min_create_order_time": range_row[0].isoformat(timespec="seconds") if range_row and range_row[0] else "",
        "max_create_order_time": range_row[1].isoformat(timespec="seconds") if range_row and range_row[1] else "",
        "last_local_update_time": range_row[2].isoformat(timespec="seconds") if range_row and range_row[2] else "",
        "distinct_poi_count": int(range_row[3] or 0) if range_row else 0,
        "latest_sync_log": {
            "status": latest_log.status if latest_log else "",
            "total_records": latest_log.total_records if latest_log else 0,
            "error_message": latest_log.error_message if latest_log else "",
            "started_at": latest_log.started_at.isoformat(timespec="seconds") if latest_log and latest_log.started_at else "",
            "finished_at": latest_log.finished_at.isoformat(timespec="seconds") if latest_log and latest_log.finished_at else "",
        },
    }
