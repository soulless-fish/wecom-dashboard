"""
抖音来客API路由
提供给企业微信侧边栏调用的抖音来客数据接口
"""
import logging
import time
from datetime import datetime
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from fastapi.responses import JSONResponse
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.config import get_settings, Settings
from app.database.models import get_db
from app.services.douyin_token_manager import DouyinTokenManager
from app.services.douyin_client import DouyinClient
from app.services.douyin_order_sync import (
    get_douyin_official_order_status,
    sync_douyin_official_orders,
)
from app.services.douyin_computer_cleaning_sync import (
    build_store_group_purchase_summary,
    get_douyin_computer_cleaning_status,
    sync_douyin_computer_cleaning_status,
)
from app.services.douyin_poi_account_sync import (
    get_douyin_poi_account_binding_status,
    sync_douyin_poi_account_bindings,
)
from app.services.douyin_shop_business_status_sync import (
    get_douyin_shop_business_status,
    sync_douyin_shop_business_status,
)
from app.services.douyin_craftsman_sync import (
    build_store_douyin_account_detail,
    get_douyin_craftsman_binding_status,
    sync_douyin_craftsman_bindings,
)
from app.models.schemas import (
    APIResponse,
    DouyinTimeRangeQuery,
    DouyinMessageQuery,
    DouyinOrderQuery
)

router = APIRouter()
logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOUYIN_SPI_SUMMARY_DIR = PROJECT_ROOT / "测试文件" / "测试数据" / "抖音SPI回调"
DOUYIN_SPI_SUMMARY_FILE = DOUYIN_SPI_SUMMARY_DIR / "douyin_spi_callback_summary.jsonl"
DOUYIN_WEBHOOK_SUMMARY_DIR = PROJECT_ROOT / "测试文件" / "测试数据" / "抖音Webhook回调"
DOUYIN_WEBHOOK_SUMMARY_FILE = DOUYIN_WEBHOOK_SUMMARY_DIR / "douyin_webhook_callback_summary.jsonl"


def get_douyin_client(settings: Settings = Depends(get_settings)) -> DouyinClient:
    """
    获取抖音客户端依赖

    Args:
        settings: 应用配置

    Returns:
        DouyinClient: 抖音API客户端实例
    """
    token_manager = DouyinTokenManager(settings)
    return DouyinClient(token_manager)


def get_default_time_range() -> tuple[int, int]:
    """
    获取默认时间范围（最近7天）

    Returns:
        tuple: (start_time, end_time) 时间戳
    """
    end_time = int(time.time())
    start_time = end_time - 7 * 24 * 60 * 60  # 7天前
    return start_time, end_time


@router.get("/visitor", response_model=APIResponse, summary="获取来客列表")
async def get_visitors(
    start_time: Optional[int] = Query(None, description="开始时间戳(秒)，默认7天前"),
    end_time: Optional[int] = Query(None, description="结束时间戳(秒)，默认当前时间"),
    page_num: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    client: DouyinClient = Depends(get_douyin_client)
):
    """
    获取抖音来客访客列表

    - **start_time**: 开始时间戳，不传则默认7天前
    - **end_time**: 结束时间戳，不传则默认当前时间
    - **page_num**: 页码，从1开始
    - **page_size**: 每页数量，最大100
    """
    try:
        # 设置默认时间范围
        if start_time is None or end_time is None:
            default_start, default_end = get_default_time_range()
            start_time = start_time or default_start
            end_time = end_time or default_end

        result = await client.get_visitor_list(
            start_time=start_time,
            end_time=end_time,
            page_num=page_num,
            page_size=page_size
        )

        return APIResponse(
            code=0,
            message="success",
            data=result.get("data")
        )
    except Exception as e:
        return APIResponse(
            code=-1,
            message=str(e),
            data=None
        )


@router.get("/message", response_model=APIResponse, summary="获取用户消息列表")
async def get_messages(
    start_time: Optional[int] = Query(None, description="开始时间戳(秒)"),
    end_time: Optional[int] = Query(None, description="结束时间戳(秒)"),
    message_type: int = Query(0, description="消息类型，0-全部"),
    username: Optional[str] = Query(None, description="用户名筛选"),
    page_num: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    client: DouyinClient = Depends(get_douyin_client)
):
    """
    获取抖音来客用户消息列表

    - **start_time**: 开始时间戳
    - **end_time**: 结束时间戳
    - **message_type**: 消息类型，0表示全部
    - **username**: 按用户名筛选
    - **page_num**: 页码
    - **page_size**: 每页数量
    """
    try:
        if start_time is None or end_time is None:
            default_start, default_end = get_default_time_range()
            start_time = start_time or default_start
            end_time = end_time or default_end

        result = await client.get_user_message(
            start_time=start_time,
            end_time=end_time,
            message_type=message_type,
            username=username,
            page_num=page_num,
            page_size=page_size
        )

        return APIResponse(
            code=0,
            message="success",
            data=result.get("data")
        )
    except Exception as e:
        return APIResponse(
            code=-1,
            message=str(e),
            data=None
        )


@router.get("/order", response_model=APIResponse, summary="获取订单列表")
async def get_orders(
    start_time: Optional[int] = Query(None, description="开始时间戳(秒)"),
    end_time: Optional[int] = Query(None, description="结束时间戳(秒)"),
    order_status: Optional[int] = Query(None, description="订单状态筛选"),
    page_num: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    client: DouyinClient = Depends(get_douyin_client)
):
    """
    获取抖音来客订单列表

    - **start_time**: 开始时间戳
    - **end_time**: 结束时间戳
    - **order_status**: 订单状态筛选
    - **page_num**: 页码
    - **page_size**: 每页数量
    """
    try:
        if start_time is None or end_time is None:
            default_start, default_end = get_default_time_range()
            start_time = start_time or default_start
            end_time = end_time or default_end

        result = await client.get_order_list(
            start_time=start_time,
            end_time=end_time,
            page_num=page_num,
            page_size=page_size,
            order_status=order_status
        )

        return APIResponse(
            code=0,
            message="success",
            data=result.get("data")
        )
    except Exception as e:
        return APIResponse(
            code=-1,
            message=str(e),
            data=None
        )


@router.get("/poi", response_model=APIResponse, summary="获取门店列表")
async def get_poi_list(
    page_num: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    client: DouyinClient = Depends(get_douyin_client)
):
    """
    获取抖音来客门店列表

    - **page_num**: 页码
    - **page_size**: 每页数量
    """
    try:
        result = await client.get_poi_list(
            page=page_num,
            size=page_size
        )

        return APIResponse(
            code=0,
            message="success",
            data=result.get("data")
        )
    except Exception as e:
        return APIResponse(
            code=-1,
            message=str(e),
            data=None
        )


@router.get("/poi/{poi_id}", response_model=APIResponse, summary="获取门店详情")
async def get_poi_info(
    poi_id: str,
    client: DouyinClient = Depends(get_douyin_client)
):
    """
    获取抖音来客门店详情

    - **poi_id**: 门店ID
    """
    try:
        result = await client.get_poi_info(poi_id=poi_id)

        return APIResponse(
            code=0,
            message="success",
            data=result.get("data")
        )
    except Exception as e:
        return APIResponse(
            code=-1,
            message=str(e),
            data=None
        )


@router.get("/customer/{open_id}", response_model=APIResponse, summary="获取客户详情")
async def get_customer_info(
    open_id: str,
    client: DouyinClient = Depends(get_douyin_client)
):
    """
    获取抖音来客客户详细信息

    - **open_id**: 客户的open_id
    """
    try:
        result = await client.get_customer_info(open_id=open_id)

        return APIResponse(
            code=0,
            message="success",
            data=result.get("data")
        )
    except Exception as e:
        return APIResponse(
            code=-1,
            message=str(e),
            data=None
        )


@router.get("/token/test", response_model=APIResponse, summary="测试Token获取")
async def test_token(
    client: DouyinClient = Depends(get_douyin_client)
):
    """
    测试抖音access_token获取是否正常

    用于验证抖音开放平台凭证配置是否正确
    """
    try:
        token = await client.token_manager.get_access_token()

        # 隐藏部分token信息
        masked_token = token[:10] + "..." + token[-10:] if len(token) > 20 else token

        return APIResponse(
            code=0,
            message="Token获取成功",
            data={
                "token_preview": masked_token,
                "token_length": len(token)
            }
        )
    except Exception as e:
        return APIResponse(
            code=-1,
            message=f"Token获取失败: {str(e)}",
            data=None
        )


@router.post("/official-orders/sync", response_model=APIResponse, summary="同步抖音官方订单")
async def sync_official_orders(
    days: int = Query(30, ge=1, le=90, description="同步最近多少天订单"),
    hours: Optional[int] = Query(None, ge=1, le=2160, description="按小时同步时的最近小时数，传入后优先于days"),
    max_pages: int = Query(50, ge=1, le=5000, description="最多同步页数，用于控制线上任务耗时"),
    page_size: int = Query(200, ge=1, le=200, description="每页订单数，官方接口当前验证200可用"),
    window_hours: int = Query(24, ge=1, le=24, description="分页时间窗口小时数，受限时优先同步最新窗口"),
):
    """
    同步抖音官方订单到本地数据库。

    侧边栏不会实时分页调用抖音接口，而是读取该接口同步后的本地订单明细。
    """
    try:
        result = await sync_douyin_official_orders(
            days=days,
            hours=hours,
            max_pages=max_pages,
            page_size=page_size,
            window_hours=window_hours,
        )
        return APIResponse(code=0, message="抖音官方订单同步完成", data=result)
    except Exception as e:
        logger.exception("抖音官方订单同步失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.get("/official-orders/status", response_model=APIResponse, summary="抖音官方订单同步状态")
async def official_orders_status(db: Session = Depends(get_db)):
    """查看本地抖音官方订单明细的同步状态，不返回密钥或 token。"""
    try:
        return APIResponse(
            code=0,
            message="success",
            data=get_douyin_official_order_status(db),
        )
    except Exception as e:
        logger.exception("读取抖音官方订单同步状态失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.post("/computer-cleaning/sync", response_model=APIResponse, summary="同步电脑清灰团购开通状态")
async def sync_computer_cleaning_status(db: Session = Depends(get_db)):
    """
    同步商家已上线的电脑清灰团购商品状态。

    侧边栏读取本地状态表，不在用户打开侧边栏时实时调用抖音开放平台。
    """
    try:
        result = await sync_douyin_computer_cleaning_status(db)
        return APIResponse(code=0, message="电脑清灰团购状态同步完成", data=result)
    except Exception as e:
        logger.exception("电脑清灰团购状态同步失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.post("/group-purchase/sync", response_model=APIResponse, summary="同步门店团购链接状态")
async def sync_group_purchase_status(db: Session = Depends(get_db)):
    """同步商家已上线的团购链接商品状态。"""
    try:
        result = await sync_douyin_computer_cleaning_status(db)
        return APIResponse(code=0, message="团购链接状态同步完成", data=result)
    except Exception as e:
        logger.exception("团购链接状态同步失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.get("/computer-cleaning/status", response_model=APIResponse, summary="电脑清灰团购同步状态")
async def computer_cleaning_status(db: Session = Depends(get_db)):
    """查看电脑清灰团购状态同步情况，不返回密钥或 token。"""
    try:
        return APIResponse(
            code=0,
            message="success",
            data=get_douyin_computer_cleaning_status(db),
        )
    except Exception as e:
        logger.exception("读取电脑清灰团购状态失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.get("/group-purchase/status", response_model=APIResponse, summary="团购链接同步状态")
async def group_purchase_status(db: Session = Depends(get_db)):
    """查看团购链接同步情况，不返回密钥或 token。"""
    try:
        return APIResponse(
            code=0,
            message="success",
            data=get_douyin_computer_cleaning_status(db),
        )
    except Exception as e:
        logger.exception("读取团购链接状态失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.get("/group-purchase/store-links", response_model=APIResponse, summary="获取门店团购链接列表")
async def get_store_group_purchase_links(
    poi_id: str,
    db: Session = Depends(get_db),
):
    """按门店 ID 返回该门店命中的团购链接商品列表。"""
    try:
        return APIResponse(
            code=0,
            message="success",
            data=build_store_group_purchase_summary(db, poi_id),
        )
    except Exception as e:
        logger.exception("读取门店团购链接列表失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.post("/poi-account-bindings/sync", response_model=APIResponse, summary="同步门店子机构经营号")
async def sync_poi_account_bindings(db: Session = Depends(get_db)):
    """同步 goodlife 门店信息中的 SUB_ORG 子机构经营号。"""
    try:
        result = await sync_douyin_poi_account_bindings(db)
        return APIResponse(code=0, message="门店子机构经营号同步完成", data=result)
    except Exception as e:
        logger.exception("门店子机构经营号同步失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.get("/poi-account-bindings/status", response_model=APIResponse, summary="门店子机构经营号同步状态")
async def poi_account_binding_status(db: Session = Depends(get_db)):
    """查看门店子机构经营号同步状态，不返回密钥或 token。"""
    try:
        return APIResponse(
            code=0,
            message="success",
            data=get_douyin_poi_account_binding_status(db),
        )
    except Exception as e:
        logger.exception("读取门店子机构经营号同步状态失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.get("/poi-account-bindings/store-accounts", response_model=APIResponse, summary="获取门店抖音号详情")
async def get_store_douyin_accounts(
    poi_id: str = Query("", description="门店ID"),
    poi_name: str = Query("", description="门店名称"),
    db: Session = Depends(get_db),
):
    """按门店ID和门店名称返回子机构经营号、商家职人号、个人职人号。"""
    try:
        return APIResponse(
            code=0,
            message="success",
            data=build_store_douyin_account_detail(db, poi_id=poi_id, poi_name=poi_name),
        )
    except Exception as e:
        logger.exception("读取门店抖音号详情失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.post("/shop-business-status/sync", response_model=APIResponse, summary="同步门店营业状态")
async def sync_shop_business_status(db: Session = Depends(get_db)):
    """同步来客门店营业状态，侧边栏读取本地缓存表。"""
    try:
        result = await sync_douyin_shop_business_status(db)
        return APIResponse(code=0, message="门店营业状态同步完成", data=result)
    except Exception as e:
        logger.exception("门店营业状态同步失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.get("/shop-business-status/status", response_model=APIResponse, summary="门店营业状态同步状态")
async def shop_business_status(db: Session = Depends(get_db)):
    """查看门店营业状态同步情况，不返回 Cookie 或密钥。"""
    try:
        return APIResponse(
            code=0,
            message="success",
            data=get_douyin_shop_business_status(db),
        )
    except Exception as e:
        logger.exception("读取门店营业状态失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.post("/craftsman-bindings/sync", response_model=APIResponse, summary="同步抖音职人号绑定信息")
async def sync_craftsman_bindings(db: Session = Depends(get_db)):
    """同步商家总户下职人绑定信息，用于抖音号详情页展示。"""
    try:
        result = await sync_douyin_craftsman_bindings(db)
        return APIResponse(code=0, message="抖音职人号同步完成", data=result)
    except Exception as e:
        logger.exception("抖音职人号同步失败")
        return APIResponse(code=-1, message=str(e), data=None)


@router.get("/craftsman-bindings/status", response_model=APIResponse, summary="抖音职人号同步状态")
async def craftsman_binding_status(db: Session = Depends(get_db)):
    """查看本地抖音职人号同步状态，不返回密钥或 token。"""
    try:
        return APIResponse(
            code=0,
            message="success",
            data=get_douyin_craftsman_binding_status(db),
        )
    except Exception as e:
        logger.exception("读取抖音职人号同步状态失败")
        return APIResponse(code=-1, message=str(e), data=None)


def _build_spi_success_response(scope: str) -> dict:
    """生成抖音 SPI 回调成功响应，保持 data 和 extra 两种错误码结构都为成功。"""
    return {
        "data": {
            "error_code": 0,
            "description": "success",
        },
        "extra": {
            "error_code": 0,
            "description": "success",
        },
        "scope": scope,
    }


def _append_spi_callback_summary(summary: dict) -> None:
    """保存抖音 SPI 回调摘要，便于排查官方是否推送过数据。"""
    try:
        DOUYIN_SPI_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
        with DOUYIN_SPI_SUMMARY_FILE.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("保存抖音SPI回调摘要失败")


def _append_webhook_callback_summary(summary: dict) -> None:
    """保存抖音 Webhook 回调摘要，便于排查事件订阅和地址校验。"""
    try:
        DOUYIN_WEBHOOK_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
        with DOUYIN_WEBHOOK_SUMMARY_FILE.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("保存抖音Webhook回调摘要失败")


def _extract_webhook_challenge(payload: dict) -> tuple[Any, bool]:
    """从抖音 Webhook 校验请求中提取 challenge 原始值，并标记字段是否存在。"""
    content = payload.get("content")
    if isinstance(content, dict):
        for key in ("challenge", "CHALLENGE"):
            if key in content and content[key] is not None:
                return content[key], True

    for key in ("challenge", "CHALLENGE"):
        if key in payload and payload[key] is not None:
            return payload[key], True
    return None, False


def _build_webhook_content_summary(payload: dict) -> dict:
    """解析 Webhook content 摘要，便于订阅正式事件后判断消息结构，避免保存完整业务内容。"""
    if "content" not in payload:
        return {
            "content_raw_type": "",
            "content_parse_status": "missing",
            "content_json_type": "",
            "content_keys": [],
            "content_action": "",
            "content_msg_time": "",
            "content_order_keys": [],
            "content_has_order_id": False,
        }

    content = payload.get("content")
    parsed_content = content
    parse_status = "native_json"

    if isinstance(content, str):
        stripped_content = content.strip()
        if not stripped_content:
            parsed_content = None
            parse_status = "empty_string"
        else:
            try:
                parsed_content = json.loads(stripped_content)
                parse_status = "parsed_json_string"
            except Exception:
                parsed_content = None
                parse_status = "invalid_json_string"

    content_keys: list[str] = []
    content_action = ""
    content_msg_time: Any = ""
    content_order_keys: list[str] = []
    content_has_order_id = False

    if isinstance(parsed_content, dict):
        content_keys = sorted(str(key) for key in parsed_content.keys())
        content_action = str(parsed_content.get("action") or "")
        msg_time = parsed_content.get("msg_time")
        if isinstance(msg_time, (str, int, float)):
            content_msg_time = msg_time
        order = parsed_content.get("order")
        if isinstance(order, dict):
            content_order_keys = sorted(str(key) for key in order.keys())
            content_has_order_id = bool(order.get("order_id"))

    return {
        "content_raw_type": type(content).__name__,
        "content_parse_status": parse_status,
        "content_json_type": type(parsed_content).__name__ if parsed_content is not None else "",
        "content_keys": content_keys,
        "content_action": content_action,
        "content_msg_time": content_msg_time,
        "content_order_keys": content_order_keys,
        "content_has_order_id": content_has_order_id,
    }


@router.get("/spi/fulfil-check-info-sync", summary="抖音对账信息同步SPI健康检查")
async def douyin_fulfil_check_info_sync_health():
    """
    抖音对账信息同步 SPI 健康检查。

    控制台填写 POST 回调地址前，可以用 GET 访问确认地址已部署。
    """
    return _build_spi_success_response("life.fulfil.fulfil_check_info_sync")


@router.post("/spi/fulfil-check-info-sync", summary="抖音对账信息同步SPI回调")
async def douyin_fulfil_check_info_sync_callback(request: Request):
    """
    接收抖音生活服务对账信息同步 SPI 回调。

    当前先完成回调地址接入和连通性确认，不落库、不改变订单状态。
    后续正式接入核销明细时，再在这里补充验签、字段解析、幂等入库和异常告警。
    """
    raw_body = await request.body()
    content_type = request.headers.get("content-type", "")
    body_keys: list[str] = []
    parsed_body = None

    if raw_body:
        try:
            parsed_body = await request.json()
            if isinstance(parsed_body, dict):
                body_keys = sorted(str(key) for key in parsed_body.keys())
        except Exception:
            body_keys = []

    logger.info(
        "收到抖音对账信息同步SPI回调: content_type=%s body_size=%s body_keys=%s",
        content_type,
        len(raw_body),
        ",".join(body_keys),
    )
    _append_spi_callback_summary(
        {
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "scope": "life.fulfil.fulfil_check_info_sync",
            "client_host": request.client.host if request.client else "",
            "content_type": content_type,
            "body_size": len(raw_body),
            "body_keys": body_keys,
            "query_keys": sorted(request.query_params.keys()),
            "body_type": type(parsed_body).__name__ if parsed_body is not None else "",
        }
    )
    return _build_spi_success_response("life.fulfil.fulfil_check_info_sync")


@router.get("/webhook/events", summary="抖音Webhook健康检查")
async def douyin_webhook_health():
    """抖音 Webhook 接收地址健康检查。"""
    return {
        "code": 0,
        "message": "douyin webhook endpoint ready",
    }


@router.post("/webhook/events", summary="抖音Webhook事件回调")
async def douyin_webhook_events(request: Request):
    """
    接收抖音 Webhook 地址校验和事件推送。

    保存摘要用于排查，不保存完整事件体。地址校验请求返回包含 challenge 原始值的 JSON 对象。
    """
    raw_body = await request.body()
    content_type = request.headers.get("content-type", "")
    payload: dict = {}
    body_keys: list[str] = []

    if raw_body:
        try:
            parsed_body = await request.json()
            if isinstance(parsed_body, dict):
                payload = parsed_body
                body_keys = sorted(str(key) for key in payload.keys())
        except Exception:
            payload = {}
            body_keys = []

    event = str(payload.get("event") or "")
    client_key = str(payload.get("client_key") or "")
    challenge, challenge_present = _extract_webhook_challenge(payload)
    challenge_text = str(challenge) if challenge_present else ""
    content_summary = _build_webhook_content_summary(payload)

    _append_webhook_callback_summary(
        {
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "client_host": request.client.host if request.client else "",
            "content_type": content_type,
            "body_size": len(raw_body),
            "body_keys": body_keys,
            "event": event,
            "client_key_masked": client_key[:4] + "..." + client_key[-4:] if len(client_key) > 8 else "",
            "has_challenge": bool(challenge_text),
            "challenge_present": challenge_present,
            "challenge_type": type(challenge).__name__ if challenge_present else "",
            "challenge_length": len(challenge_text),
            "header_msg_id": request.headers.get("Msg-Id", ""),
            "header_signature_present": bool(request.headers.get("X-Douyin-Signature")),
            **content_summary,
        }
    )

    if event == "verify_webhook" and challenge_present:
        return JSONResponse(content={"challenge": challenge})

    return JSONResponse(content={"code": 0, "message": "success"})
