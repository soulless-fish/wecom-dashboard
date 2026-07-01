"""
凡科近30天买家订单同步服务

该服务只负责凡科链路：读取凡科订单、匹配电池/电芯分类、写入凡科订单表，
并提供侧边栏按门店手机号聚合凡科金额的查询函数。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
import re
from typing import Any, Iterable, Optional

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import DataSyncLog, FankeBuyerOrderItem, SessionLocal
from app.services.fanke_client import FankeApiError, FankeClient, ensure_valid_token
from app.services.fanke_token_storage import load_fanke_token, save_fanke_token


logger = logging.getLogger(__name__)

PAGE_SIZE = 200
MAX_API_RETRY = 5
FANKE_BASE_URL = "https://waybill.api.jz.fkw.com"
ORDER_SOURCE_NORMAL = "normal"
ORDER_SOURCE_MERCHANT = "merchant"
ORDER_SOURCE_NAMES = {
    ORDER_SOURCE_NORMAL: "普通商品订单",
    ORDER_SOURCE_MERCHANT: "入驻商户订单",
}

CATEGORY_APPLE_BATTERY = "apple_battery"
CATEGORY_APPLE_CELL = "apple_cell"
CATEGORY_ANDROID_BATTERY = "android_battery"
CATEGORY_ANDROID_CELL = "android_cell"

CATEGORY_NAMES = {
    CATEGORY_APPLE_BATTERY: "苹果电池",
    CATEGORY_APPLE_CELL: "苹果电芯",
    CATEGORY_ANDROID_BATTERY: "安卓电池",
    CATEGORY_ANDROID_CELL: "安卓电芯",
}

# 只有已付款后的订单状态才计入采购金额，未付款和已取消订单仅保留明细。
COUNTED_PURCHASE_STATUSES = {"wait_seller_ship", "shipped", "finished"}


@dataclass(frozen=True)
class ProductCatalog:
    """凡科电池/电芯分类匹配表"""

    code_map: dict[str, tuple[str, str, str]]
    android_cell_pairs: dict[tuple[str, str], tuple[str, str]]


def project_root() -> Path:
    """获取项目根目录"""
    return Path(__file__).resolve().parents[3]


def fk_dir() -> Path:
    """获取凡科分类表目录"""
    return project_root() / "fk"


def normalize_text(value: Any) -> str:
    """规整文本用于匹配商品名称和规格"""
    text = str(value or "").strip()
    text = text.replace("：", ":")
    text = re.sub(r"\s+", "", text)
    return text.lower()


def normalize_code(value: Any) -> str:
    """规整商品编码用于匹配产品编号"""
    return str(value or "").strip().upper()


def normalize_phone(value: Any) -> str:
    """规整手机号，只保留数字"""
    return re.sub(r"\D+", "", str(value or ""))


def safe_decimal(value: Any, default: str = "0") -> Decimal:
    """安全转换金额数字"""
    if value in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def safe_int(value: Any, default: int = 0) -> int:
    """安全转换整数"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_product_catalog(base_dir: Optional[Path] = None) -> ProductCatalog:
    """读取4张凡科商品分类表，生成匹配索引"""
    resolved_dir = base_dir or fk_dir()
    code_map: dict[str, tuple[str, str, str]] = {}
    android_cell_pairs: dict[tuple[str, str], tuple[str, str]] = {}

    _load_code_file(
        resolved_dir / "苹果成品.xls",
        code_map,
        category=CATEGORY_APPLE_BATTERY,
        match_rule="产品编码",
    )
    _load_code_file(
        resolved_dir / "聚信电芯.xls",
        code_map,
        category=CATEGORY_APPLE_CELL,
        match_rule="产品编码",
    )
    _load_code_file(
        resolved_dir / "安卓电池.xls",
        code_map,
        category=CATEGORY_ANDROID_BATTERY,
        match_rule="产品编码",
    )
    _load_android_cell_file(resolved_dir / "安卓电芯.xls", android_cell_pairs)

    return ProductCatalog(code_map=code_map, android_cell_pairs=android_cell_pairs)


def _load_code_file(path: Path, code_map: dict[str, tuple[str, str, str]], category: str, match_rule: str) -> None:
    """读取按产品编码匹配的分类表"""
    rows = _read_xls_rows(path)
    if not rows:
        return
    header = [str(value or "").strip() for value in rows[0]]
    code_index = _find_header_index(header, ["产品编码"])
    name_index = _find_header_index(header, ["产品名称", "商品规格", "商品名称"], required=False)
    if code_index is None:
        raise RuntimeError(f"分类表缺少产品编码列: {path}")

    for row in rows[1:]:
        code = normalize_code(_cell(row, code_index))
        if not code:
            continue
        name = str(_cell(row, name_index) or "").strip() if name_index is not None else ""
        code_map[code] = (category, match_rule, name or code)


def _load_android_cell_file(path: Path, android_cell_pairs: dict[tuple[str, str], tuple[str, str]]) -> None:
    """读取安卓电芯分类表，按商品名称和商品规格匹配"""
    rows = _read_xls_rows(path)
    if not rows:
        return
    header = [str(value or "").strip() for value in rows[0]]
    name_index = _find_header_index(header, ["商品名称"])
    spec_index = _find_header_index(header, ["商品规格"])
    if name_index is None or spec_index is None:
        raise RuntimeError(f"安卓电芯分类表缺少商品名称或商品规格列: {path}")

    for row in rows[1:]:
        name = normalize_text(_cell(row, name_index))
        spec = normalize_text(_cell(row, spec_index))
        if not name or not spec:
            continue
        android_cell_pairs[(name, spec)] = (CATEGORY_ANDROID_CELL, "商品名称+商品规格")


def _read_xls_rows(path: Path) -> list[list[Any]]:
    """使用 xlrd 读取老式 xls 文件"""
    if not path.exists():
        raise FileNotFoundError(f"凡科分类表不存在: {path}")
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError("缺少 xlrd 依赖，无法读取 .xls 分类表") from exc

    workbook = xlrd.open_workbook(str(path))
    sheet = workbook.sheet_by_index(0)
    return [sheet.row_values(row_index) for row_index in range(sheet.nrows)]


def _find_header_index(header: list[str], names: Iterable[str], required: bool = True) -> Optional[int]:
    """根据候选表头名称查找列索引"""
    for name in names:
        if name in header:
            return header.index(name)
    if required:
        raise RuntimeError(f"表头缺少列: {', '.join(names)}")
    return None


def _cell(row: list[Any], index: Optional[int]) -> Any:
    """安全读取行内单元格"""
    if index is None or index >= len(row):
        return ""
    return row[index]


def classify_order_item(item: dict[str, Any], catalog: ProductCatalog) -> tuple[str, str, str, str]:
    """根据商品编码或商品名称规格判断订单商品分类"""
    product_code = normalize_code(item.get("product_code"))
    if product_code and product_code in catalog.code_map:
        category, rule, match_name = catalog.code_map[product_code]
        return category, CATEGORY_NAMES[category], rule, product_code or match_name

    title_key = normalize_text(item.get("title"))
    spec_key = normalize_text(item.get("sku_properties"))
    product_code_text_key = normalize_text(item.get("product_code"))
    clean_spec_key = re.sub(r"^(型号|规格|颜色|属性):", "", spec_key)
    for (name, spec), (category, rule) in catalog.android_cell_pairs.items():
        # 安卓电芯订单可能只命中商品名称或商品规格，不要求两个字段同时匹配。
        name_matched = (
            (bool(title_key) and (title_key == name or name in title_key or title_key in name))
            or product_code_text_key == name
            or (bool(product_code_text_key) and name in product_code_text_key)
        )
        spec_matched = (
            spec_key == spec
            or clean_spec_key == spec
            or spec in spec_key
            or spec in clean_spec_key
            or spec in title_key
        )
        if name_matched or spec_matched:
            matched_rule = "商品名称或商品规格"
            matched_key = name if name_matched else spec
            return category, CATEGORY_NAMES[category], matched_rule or rule, matched_key

    fallback_category = classify_order_item_by_text(item)
    if fallback_category[0]:
        return fallback_category

    return "", "", "", ""


def classify_order_item_by_text(item: dict[str, Any]) -> tuple[str, str, str, str]:
    """按商品标题和规格兜底识别入驻商户无编码电池/电芯商品"""
    title_key = normalize_text(item.get("title"))
    spec_key = normalize_text(item.get("sku_properties"))
    combined_key = f"{title_key}{spec_key}"
    if not combined_key:
        return "", "", "", ""

    if "安卓成品电池" in combined_key or "安卓电池" in combined_key:
        return CATEGORY_ANDROID_BATTERY, CATEGORY_NAMES[CATEGORY_ANDROID_BATTERY], "商品标题或规格关键词", "安卓电池"

    if "聚信电芯" in combined_key:
        return CATEGORY_APPLE_CELL, CATEGORY_NAMES[CATEGORY_APPLE_CELL], "商品标题或规格关键词", "聚信电芯"

    # 同一个入驻商户链接标题可能同时写“官方电/官方屏”，只有规格明确为官电时才按电池计入。
    spec_has_official_battery = "官电" in spec_key or "官方电" in spec_key
    spec_has_only_screen = ("官屏" in spec_key or "官方屏" in spec_key) and not spec_has_official_battery
    if spec_has_only_screen:
        return "", "", "", ""

    is_apple_text = "iphone" in combined_key or "苹果" in combined_key
    is_apple_battery_text = (
        "iphone原厂诊断电池" in combined_key
        or ("成品电池" in combined_key and "安卓" not in combined_key)
        or spec_has_official_battery
        or (is_apple_text and spec_has_official_battery)
    )
    if is_apple_battery_text:
        return CATEGORY_APPLE_BATTERY, CATEGORY_NAMES[CATEGORY_APPLE_BATTERY], "商品标题或规格关键词", "苹果电池"

    return "", "", "", ""


def build_recent_date_range(days: int = 30) -> tuple[date, date, str, str]:
    """生成近30天订单查询范围"""
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    return (
        start_date,
        end_date,
        f"{start_date:%Y-%m-%d} 00:00:00",
        f"{end_date:%Y-%m-%d} 23:59:59",
    )


def mark_order_source(order: dict[str, Any], source: str) -> dict[str, Any]:
    """给订单原始数据补充来源字段，方便后续写库和排查。"""
    marked_order = dict(order)
    marked_order["_order_source"] = source
    marked_order["_order_source_name"] = ORDER_SOURCE_NAMES.get(source, source)
    return marked_order


def extract_order_id(order: dict[str, Any]) -> str:
    """统一提取凡科订单号，供同步和跨来源去重使用。"""
    return str(order.get("id") or order.get("order_id") or "").strip()


def dedupe_cross_source_orders(orders: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """去掉普通订单接口中和入驻商户接口重复的订单。

    凡科普通订单接口返回结构里也带有商户字段，存在未来把同一入驻商户订单同时返回到
    普通订单接口和入驻商户接口的风险。侧边栏按明细直接求和，所以这里按订单号跨来源去重：
    同一 order_id 同时存在时保留入驻商户来源，跳过普通订单来源。
    """
    raw_source_counts: dict[str, int] = defaultdict(int)
    for order in orders:
        raw_source_counts[str(order.get("_order_source") or ORDER_SOURCE_NORMAL)] += 1

    merchant_order_ids = {
        extract_order_id(order)
        for order in orders
        if str(order.get("_order_source") or ORDER_SOURCE_NORMAL) == ORDER_SOURCE_MERCHANT
        and extract_order_id(order)
    }

    deduped_orders: list[dict[str, Any]] = []
    skipped_normal_order_ids: list[str] = []
    for order in orders:
        source = str(order.get("_order_source") or ORDER_SOURCE_NORMAL)
        order_id = extract_order_id(order)
        if source == ORDER_SOURCE_NORMAL and order_id and order_id in merchant_order_ids:
            skipped_normal_order_ids.append(order_id)
            continue
        deduped_orders.append(order)

    deduped_source_counts: dict[str, int] = defaultdict(int)
    for order in deduped_orders:
        deduped_source_counts[str(order.get("_order_source") or ORDER_SOURCE_NORMAL)] += 1

    return deduped_orders, {
        "raw_source_order_counts": dict(sorted(raw_source_counts.items())),
        "deduped_source_order_counts": dict(sorted(deduped_source_counts.items())),
        "skipped_normal_duplicate_order_count": len(skipped_normal_order_ids),
        "skipped_normal_duplicate_order_samples": sorted(set(skipped_normal_order_ids))[:20],
    }


async def fetch_recent_normal_orders(
    client: FankeClient,
    access_token: str,
    time_from: str,
    time_to: str,
) -> list[dict[str, Any]]:
    """分页获取普通商品订单"""
    orders: list[dict[str, Any]] = []
    page_no = 1

    while True:
        result = await fetch_normal_order_page(client, access_token, time_from, time_to, page_no)
        page_orders = result.get("order_list") or []
        if not isinstance(page_orders, list):
            page_orders = []
        orders.extend(mark_order_source(order, ORDER_SOURCE_NORMAL) for order in page_orders)

        total = result.get("total") or result.get("count")
        logger.info("凡科普通订单同步: 第%s页，当前%s/%s", page_no, len(orders), total)

        if len(page_orders) < PAGE_SIZE:
            break
        if isinstance(total, int) and len(orders) >= total:
            break
        page_no += 1
        if page_no > 1000:
            raise RuntimeError("凡科普通订单分页超过1000页，已中止")

    return orders


async def fetch_normal_order_page(
    client: FankeClient,
    access_token: str,
    time_from: str,
    time_to: str,
    page_no: int,
) -> dict[str, Any]:
    """请求普通订单单页，遇到凡科临时维护或网络波动时重试"""
    last_error: Exception | None = None
    for attempt in range(1, MAX_API_RETRY + 1):
        try:
            return await client.fetch_order_list(
                access_token=access_token,
                time_from=time_from,
                time_to=time_to,
                page_no=page_no,
                page_size=PAGE_SIZE,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= MAX_API_RETRY:
                break
            await asyncio.sleep(min(30, attempt * 3))
    raise FankeApiError(f"凡科普通订单第{page_no}页请求失败: {last_error}")


async def fetch_recent_merchant_orders(
    access_token: str,
    time_from: str,
    time_to: str,
) -> list[dict[str, Any]]:
    """分页获取 7.2 入驻商户订单"""
    orders: list[dict[str, Any]] = []
    page_no = 1

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as http_client:
        while True:
            result = await fetch_merchant_order_page(http_client, access_token, time_from, time_to, page_no)

            page_orders = result.get("order_list") or []
            if not isinstance(page_orders, list):
                page_orders = []
            orders.extend(mark_order_source(order, ORDER_SOURCE_MERCHANT) for order in page_orders)

            total = result.get("total") or result.get("count")
            logger.info("凡科入驻商户订单同步: 第%s页，当前%s/%s", page_no, len(orders), total)

            if len(page_orders) < PAGE_SIZE:
                break
            if isinstance(total, int) and len(orders) >= total:
                break
            page_no += 1
            if page_no > 1000:
                raise RuntimeError("凡科入驻商户订单分页超过1000页，已中止")

    return orders


async def fetch_merchant_order_page(
    http_client: httpx.AsyncClient,
    access_token: str,
    time_from: str,
    time_to: str,
    page_no: int,
) -> dict[str, Any]:
    """请求入驻商户订单单页，遇到凡科临时维护或网络波动时重试"""
    params = {
        "access_token": access_token,
        "page_no": page_no,
        "page_size": PAGE_SIZE,
        "time_type": "created",
        "time_from": time_from,
        "time_to": time_to,
    }
    last_error: Exception | None = None
    for attempt in range(1, MAX_API_RETRY + 1):
        try:
            response = await http_client.get(f"{FANKE_BASE_URL}/api/order/getMerchantOrderList", params=params)
            try:
                result = response.json()
            except ValueError as exc:
                raise FankeApiError(f"凡科入驻商户订单接口返回非JSON响应: status={response.status_code}") from exc

            if result.get("success") is False or result.get("rt") not in (None, 0):
                message = result.get("msg") or result.get("error_message") or "凡科入驻商户订单接口调用失败"
                raise FankeApiError(message)
            return result
        except Exception as exc:
            last_error = exc
            if attempt >= MAX_API_RETRY:
                break
            await asyncio.sleep(min(30, attempt * 3))
    raise FankeApiError(f"凡科入驻商户订单第{page_no}页请求失败: {last_error}")


async def fetch_recent_orders(
    client: FankeClient,
    access_token: str,
    days: int = 30,
) -> tuple[list[dict[str, Any]], date, date, dict[str, int], dict[str, Any]]:
    """分页获取近30天普通商品订单和入驻商户订单"""
    start_date, end_date, time_from, time_to = build_recent_date_range(days=days)
    normal_orders = await fetch_recent_normal_orders(client, access_token, time_from, time_to)
    merchant_orders = await fetch_recent_merchant_orders(access_token, time_from, time_to)
    orders, dedupe_summary = dedupe_cross_source_orders(normal_orders + merchant_orders)
    source_counts = {
        ORDER_SOURCE_NORMAL: int(dedupe_summary["deduped_source_order_counts"].get(ORDER_SOURCE_NORMAL, 0)),
        ORDER_SOURCE_MERCHANT: int(dedupe_summary["deduped_source_order_counts"].get(ORDER_SOURCE_MERCHANT, 0)),
    }
    return orders, start_date, end_date, source_counts, dedupe_summary


async def sync_fanke_recent_orders(days: int = 30) -> dict[str, Any]:
    """同步凡科近30天订单到本地数据库"""
    settings = get_settings()
    token = load_fanke_token()
    if not token.get("access_token"):
        raise FankeApiError("尚未完成凡科授权，无法同步订单")

    client = FankeClient(
        client_id=settings.fanke_client_id,
        client_secret=settings.fanke_client_secret,
        return_url=settings.fanke_return_url,
        platform_code=settings.fanke_platform_code,
    )

    token, refreshed = await ensure_valid_token(client, token)
    if refreshed:
        save_fanke_token(token)

    catalog = load_product_catalog()
    orders, start_date, end_date, source_counts, dedupe_summary = await fetch_recent_orders(client, token["access_token"], days=days)
    rows = build_order_item_rows(orders, catalog, start_date, end_date)

    db = SessionLocal()
    sync_log_id = None
    try:
        sync_log = DataSyncLog(
            sync_type="fanke_recent_orders",
            data_month=end_date.strftime("%Y-%m"),
            status="running",
            started_at=datetime.now(),
        )
        db.add(sync_log)
        db.commit()
        db.refresh(sync_log)
        sync_log_id = sync_log.id

        db.query(FankeBuyerOrderItem).delete()
        if rows:
            db.bulk_save_objects(rows)
        db.commit()

        sync_log.status = "success"
        sync_log.total_records = len(rows)
        sync_log.finished_at = datetime.now()
        db.commit()

        summary = summarize_rows(rows)
        logger.info(
            "凡科订单同步完成: 订单%s条，明细%s条，来源%s，跨来源去重%s",
            len(orders),
            len(rows),
            source_counts,
            dedupe_summary,
        )
        return {
            "success": True,
            "token_refreshed": refreshed,
            "order_count": len(orders),
            "source_order_counts": source_counts,
            "order_dedupe_summary": dedupe_summary,
            "item_count": len(rows),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "summary": summary,
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


def build_order_item_rows(
    orders: list[dict[str, Any]],
    catalog: ProductCatalog,
    start_date: date,
    end_date: date,
) -> list[FankeBuyerOrderItem]:
    """将订单列表展开成数据库明细行"""
    rows: list[FankeBuyerOrderItem] = []
    for order in orders:
        order_items = order.get("order_items") or []
        if not isinstance(order_items, list):
            order_items = []

        raw_order = dict(order)
        raw_order.pop("order_items", None)
        raw_order_json = json.dumps(raw_order, ensure_ascii=False)
        order_source = str(order.get("_order_source") or ORDER_SOURCE_NORMAL)
        order_source_name = str(order.get("_order_source_name") or ORDER_SOURCE_NAMES.get(order_source, order_source))

        for item in order_items:
            if not isinstance(item, dict):
                continue
            category, category_name, match_rule, match_key = classify_order_item(item, catalog)
            quantity = safe_int(item.get("quantity"), 0)
            unit_price = safe_decimal(item.get("price"))
            if item.get("item_discount_price") not in (None, ""):
                item_amount = safe_decimal(item.get("item_discount_price"))
            elif item.get("item_original_price") not in (None, ""):
                item_amount = safe_decimal(item.get("item_original_price"))
            else:
                item_amount = unit_price * Decimal(quantity)

            rows.append(
                FankeBuyerOrderItem(
                    sync_start_date=start_date,
                    sync_end_date=end_date,
                    order_source=order_source,
                    order_source_name=order_source_name,
                    order_id=str(order.get("id") or order.get("order_id") or ""),
                    merchant_id=str(order.get("merchant_id") or ""),
                    direct_merchant_id=str(order.get("direct_merchant_id") or ""),
                    order_status=str(order.get("status") or ""),
                    order_price=safe_decimal(order.get("price")),
                    pay_time=str(order.get("pay_time") or ""),
                    created_time=str(order.get("created_time") or order.get("settle_time") or ""),
                    modified_time=str(order.get("modified_time") or ""),
                    buyer_id=str(order.get("buyer_id") or ""),
                    buyer_acct=str(order.get("buyer_acct") or ""),
                    buyer_nickname=str(order.get("buyer_nickname") or ""),
                    consignee_name=str(order.get("consignee_name") or ""),
                    mobile=str(order.get("mobile") or ""),
                    telephone=str(order.get("telephone") or ""),
                    normalized_mobile=normalize_phone(order.get("mobile")),
                    normalized_telephone=normalize_phone(order.get("telephone")),
                    province=str(order.get("province") or ""),
                    city=str(order.get("city") or ""),
                    district=str(order.get("district") or ""),
                    town=str(order.get("town") or ""),
                    street=str(order.get("street") or ""),
                    item_id=str(item.get("id") or ""),
                    product_id=str(item.get("product_id") or ""),
                    product_code=normalize_code(item.get("product_code")),
                    product_title=str(item.get("title") or ""),
                    sku_properties=str(item.get("sku_properties") or ""),
                    quantity=quantity,
                    unit_price=unit_price,
                    item_amount=item_amount,
                    refund_status=str(item.get("refund_status") or ""),
                    item_status=str(item.get("status") or ""),
                    product_category=category,
                    product_category_name=category_name,
                    match_rule=match_rule,
                    match_key=match_key,
                    raw_order_json=raw_order_json,
                    raw_item_json=json.dumps(item, ensure_ascii=False),
                )
            )
    return rows


def summarize_rows(rows: Iterable[FankeBuyerOrderItem]) -> dict[str, float]:
    """汇总订单明细中的四类电池/电芯金额"""
    totals = {
        CATEGORY_APPLE_BATTERY: Decimal("0"),
        CATEGORY_APPLE_CELL: Decimal("0"),
        CATEGORY_ANDROID_BATTERY: Decimal("0"),
        CATEGORY_ANDROID_CELL: Decimal("0"),
    }
    for row in rows:
        if not is_counted_purchase_status(row.order_status):
            continue
        if row.product_category in totals:
            totals[row.product_category] += Decimal(row.item_amount or 0)
    return {key: float(value) for key, value in totals.items()}


def is_counted_purchase_status(status: Any) -> bool:
    """判断订单状态是否应计入购买金额汇总"""
    return str(status or "").strip() in COUNTED_PURCHASE_STATUSES


def build_store_fanke_purchase_summary(db: Session, store_phones: Iterable[str]) -> dict[str, Any]:
    """按门店电话汇总近30天凡科电池和电芯采购金额及订单数"""
    normalized_phones = sorted({normalize_phone(phone) for phone in store_phones if normalize_phone(phone)})
    empty_result = {
        "fanke_phone_matched": False,
        "fanke_phone_status": "phone_mismatch" if normalized_phones else "no_store_phone",
        "fanke_battery_order_count": 0,
        "fanke_cell_order_count": 0,
        "fanke_apple_battery_order_count": 0,
        "fanke_apple_cell_order_count": 0,
        "fanke_android_battery_order_count": 0,
        "fanke_android_cell_order_count": 0,
        "fanke_battery_amount": 0.0,
        "fanke_cell_amount": 0.0,
        "fanke_apple_battery_amount": 0.0,
        "fanke_apple_cell_amount": 0.0,
        "fanke_android_battery_amount": 0.0,
        "fanke_android_cell_amount": 0.0,
        "fanke_order_date_range": "近30天",
        "fanke_matched_phones": [],
    }
    if not normalized_phones:
        return empty_result

    rows = (
        db.query(FankeBuyerOrderItem)
        .filter(
            or_(
                FankeBuyerOrderItem.normalized_mobile.in_(normalized_phones),
                FankeBuyerOrderItem.normalized_telephone.in_(normalized_phones),
            )
        )
        .all()
    )
    if not rows:
        return empty_result

    totals = {
        CATEGORY_APPLE_BATTERY: Decimal("0"),
        CATEGORY_APPLE_CELL: Decimal("0"),
        CATEGORY_ANDROID_BATTERY: Decimal("0"),
        CATEGORY_ANDROID_CELL: Decimal("0"),
    }
    order_ids_by_category = {
        CATEGORY_APPLE_BATTERY: set(),
        CATEGORY_APPLE_CELL: set(),
        CATEGORY_ANDROID_BATTERY: set(),
        CATEGORY_ANDROID_CELL: set(),
    }
    matched_phones = set()
    for row in rows:
        if row.normalized_mobile in normalized_phones:
            matched_phones.add(row.normalized_mobile)
        if row.normalized_telephone in normalized_phones:
            matched_phones.add(row.normalized_telephone)
        if not is_counted_purchase_status(row.order_status):
            continue
        if row.product_category in totals:
            totals[row.product_category] += Decimal(row.item_amount or 0)
            if row.order_id:
                order_ids_by_category[row.product_category].add(row.order_id)

    battery_amount = totals[CATEGORY_APPLE_BATTERY] + totals[CATEGORY_ANDROID_BATTERY]
    cell_amount = totals[CATEGORY_APPLE_CELL] + totals[CATEGORY_ANDROID_CELL]
    battery_order_ids = order_ids_by_category[CATEGORY_APPLE_BATTERY] | order_ids_by_category[CATEGORY_ANDROID_BATTERY]
    cell_order_ids = order_ids_by_category[CATEGORY_APPLE_CELL] | order_ids_by_category[CATEGORY_ANDROID_CELL]

    return {
        "fanke_phone_matched": True,
        "fanke_phone_status": "matched",
        "fanke_battery_order_count": len(battery_order_ids),
        "fanke_cell_order_count": len(cell_order_ids),
        "fanke_apple_battery_order_count": len(order_ids_by_category[CATEGORY_APPLE_BATTERY]),
        "fanke_apple_cell_order_count": len(order_ids_by_category[CATEGORY_APPLE_CELL]),
        "fanke_android_battery_order_count": len(order_ids_by_category[CATEGORY_ANDROID_BATTERY]),
        "fanke_android_cell_order_count": len(order_ids_by_category[CATEGORY_ANDROID_CELL]),
        "fanke_battery_amount": float(battery_amount),
        "fanke_cell_amount": float(cell_amount),
        "fanke_apple_battery_amount": float(totals[CATEGORY_APPLE_BATTERY]),
        "fanke_apple_cell_amount": float(totals[CATEGORY_APPLE_CELL]),
        "fanke_android_battery_amount": float(totals[CATEGORY_ANDROID_BATTERY]),
        "fanke_android_cell_amount": float(totals[CATEGORY_ANDROID_CELL]),
        "fanke_order_date_range": "近30天",
        "fanke_matched_phones": sorted(matched_phones),
    }
