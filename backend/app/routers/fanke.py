"""
凡科商城接口路由

当前先提供 OAuth 回调地址，满足凡科要求的外网域名直达校验。
后续真正接入凡科数据时，可以在本文件继续扩展授权换 token 和数据同步接口。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database.models import FankeBuyerOrderItem, get_db
from app.services.fanke_client import (
    FankeApiError,
    FankeClient,
    build_token_record,
    ensure_valid_token,
    mask_token,
    parse_iso_datetime,
)
from app.services.fanke_order_sync import sync_fanke_recent_orders
from app.services.fanke_token_storage import load_fanke_token, save_fanke_token


logger = logging.getLogger(__name__)
router = APIRouter()


@router.head("/oauth/callback", summary="凡科OAuth回调地址HEAD探测")
async def check_fanke_oauth_callback():
    """
    凡科或人工使用 HEAD 请求检测回调地址时，直接返回 200。

    这个接口不做跳转，避免触发凡科对“回调地址不可重定向”的限制。
    """
    return Response(status_code=200)


@router.get("/oauth/callback", summary="凡科OAuth授权回调")
async def handle_fanke_oauth_callback(
    code: Optional[str] = Query(None, description="凡科授权后回传的授权码"),
    state: Optional[str] = Query(None, description="授权链接中携带的自定义状态"),
    settings: Settings = Depends(get_settings),
):
    """
    接收凡科 OAuth 授权回调。

    没有 code 时仅作为地址探测；有 code 时自动换取并保存 token。
    """
    logger.info(
        "收到凡科OAuth回调: has_code=%s, state=%s",
        bool(code),
        state or "",
    )

    if not code:
        return {
            "code": 0,
            "message": "仅凡科OAuth回调地址使用",
            "data": {
                "received_code": False,
                "state": state or "",
            },
        }

    if not settings.fanke_client_id or not settings.fanke_client_secret:
        logger.error("凡科OAuth回调收到code，但服务器未配置凡科client_id/client_secret")
        return {
            "code": -1,
            "message": "凡科API凭据未配置，无法换取token",
            "data": {
                "received_code": True,
                "state": state or "",
            },
        }

    client = _build_fanke_client(settings)
    try:
        token_data = await client.exchange_code_for_token(code)
        token_record = build_token_record(token_data)
        save_fanke_token(token_record)
    except FankeApiError as exc:
        logger.error("凡科OAuth code换token失败: %s", exc)
        return {
            "code": -1,
            "message": f"凡科OAuth授权失败：{exc}",
            "data": {
                "received_code": True,
                "state": state or "",
            },
        }

    return {
        "code": 0,
        "message": "凡科OAuth授权成功，token已保存",
        "data": {
            "received_code": True,
            "state": state or "",
            "shop_id": token_record.get("shop_id", ""),
            "shop_name": token_record.get("shop_name", ""),
            "platform_code": token_record.get("platform_code", ""),
            "access_token_expires_at": token_record.get("access_token_expires_at", ""),
            "refresh_token_expires_at": token_record.get("refresh_token_expires_at", ""),
        },
    }


@router.get("/oauth/auth-url", summary="获取凡科商家授权链接")
async def get_fanke_auth_url(
    state: str = Query("fanke_auth", description="授权回调携带的自定义状态"),
    settings: Settings = Depends(get_settings),
):
    """生成凡科商家登录授权链接"""
    if not settings.fanke_client_id:
        return {
            "code": -1,
            "message": "凡科client_id未配置",
            "data": None,
        }

    client = _build_fanke_client(settings)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "auth_url": client.build_auth_url(state=state),
        },
    }


@router.get("/token/status", summary="查看凡科授权状态")
async def get_fanke_token_status():
    """查看当前是否已经保存凡科授权 token"""
    token = load_fanke_token()
    warnings = _build_token_warnings(token)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "authorized": bool(token.get("access_token")),
            "shop_id": token.get("shop_id", ""),
            "shop_name": token.get("shop_name", ""),
            "platform_code": token.get("platform_code", ""),
            "access_token": mask_token(token.get("access_token", "")),
            "access_token_expires_at": token.get("access_token_expires_at", ""),
            "refresh_token": mask_token(token.get("refresh_token", "")),
            "refresh_token_expires_at": token.get("refresh_token_expires_at", ""),
            "updated_at": token.get("updated_at", ""),
            "warnings": warnings,
        },
    }


@router.post("/token/refresh", summary="手动刷新凡科access_token")
async def refresh_fanke_token(settings: Settings = Depends(get_settings)):
    """手动触发凡科 token 刷新，用于排查和验证自动刷新链路"""
    token = load_fanke_token()
    if not token.get("refresh_token"):
        return {
            "code": -1,
            "message": "凡科refresh_token不存在，需要重新授权",
            "data": None,
        }

    client = _build_fanke_client(settings)
    try:
        refreshed = await client.refresh_access_token(token["refresh_token"])
        token_record = build_token_record(refreshed, previous_record=token)
        save_fanke_token(token_record)
    except FankeApiError as exc:
        logger.error("手动刷新凡科token失败: %s", exc)
        return {
            "code": -1,
            "message": f"手动刷新凡科token失败：{exc}",
            "data": None,
        }

    return {
        "code": 0,
        "message": "凡科token刷新成功",
        "data": {
            "shop_id": token_record.get("shop_id", ""),
            "shop_name": token_record.get("shop_name", ""),
            "access_token_expires_at": token_record.get("access_token_expires_at", ""),
            "refresh_token_expires_at": token_record.get("refresh_token_expires_at", ""),
            "warnings": _build_token_warnings(token_record),
        },
    }


@router.get("/test/product-list", summary="测试获取凡科商品列表")
async def test_fanke_product_list(
    page_no: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(1, ge=1, le=200, description="每页数量"),
    settings: Settings = Depends(get_settings),
):
    """使用已保存的凡科 access_token 测试获取商品列表"""
    token = load_fanke_token()
    if not token.get("access_token"):
        return {
            "code": -1,
            "message": "尚未完成凡科授权，无法测试商品列表",
            "data": None,
        }

    client = _build_fanke_client(settings)
    try:
        token, refreshed = await ensure_valid_token(client, token)
        if refreshed:
            save_fanke_token(token)
        result = await client.fetch_product_list(
            access_token=token["access_token"],
            page_no=page_no,
            page_size=page_size,
        )
    except FankeApiError as exc:
        logger.error("凡科商品列表测试失败: %s", exc)
        return {
            "code": -1,
            "message": f"凡科商品列表测试失败：{exc}",
            "data": None,
        }

    return {
        "code": 0,
        "message": "success",
        "data": {
            "total": result.get("total", 0),
            "product_count": len(result.get("product_list", []) or []),
            "token_refreshed": refreshed,
            "warnings": _build_token_warnings(token),
            "raw": result,
        },
    }


@router.post("/orders/sync", summary="手动同步凡科近30天订单")
async def sync_fanke_orders(days: int = Query(30, ge=1, le=90, description="同步天数")):
    """手动同步凡科近30天买家订单，用于部署后立即验证"""
    try:
        result = await sync_fanke_recent_orders(days=days)
    except Exception as exc:
        logger.error("凡科订单同步失败: %s", exc)
        return {
            "code": -1,
            "message": f"凡科订单同步失败：{exc}",
            "data": None,
        }

    return {
        "code": 0,
        "message": "凡科订单同步成功",
        "data": result,
    }


@router.get("/orders/status", summary="查看凡科订单同步状态")
async def get_fanke_orders_status(db: Session = Depends(get_db)):
    """查看当前本地保存的凡科订单明细数量和同步范围"""
    total, sync_start_date, sync_end_date, updated_at = (
        db.query(
            func.count(FankeBuyerOrderItem.id),
            func.min(FankeBuyerOrderItem.sync_start_date),
            func.max(FankeBuyerOrderItem.sync_end_date),
            func.max(FankeBuyerOrderItem.updated_at),
        )
        .first()
    )
    source_rows = (
        db.query(
            FankeBuyerOrderItem.order_source,
            FankeBuyerOrderItem.order_source_name,
            func.count(FankeBuyerOrderItem.id),
            func.count(func.distinct(FankeBuyerOrderItem.order_id)),
        )
        .group_by(FankeBuyerOrderItem.order_source, FankeBuyerOrderItem.order_source_name)
        .all()
    )
    return {
        "code": 0,
        "message": "success",
        "data": {
            "item_count": total,
            "sync_start_date": sync_start_date.isoformat() if sync_start_date else "",
            "sync_end_date": sync_end_date.isoformat() if sync_end_date else "",
            "updated_at": updated_at.isoformat() if updated_at else "",
            "source_summary": [
                {
                    "order_source": source or "",
                    "order_source_name": source_name or "",
                    "item_count": int(item_count or 0),
                    "order_count": int(order_count or 0),
                }
                for source, source_name, item_count, order_count in source_rows
            ],
        },
    }


def _build_fanke_client(settings: Settings) -> FankeClient:
    """根据当前配置创建凡科客户端"""
    return FankeClient(
        client_id=settings.fanke_client_id,
        client_secret=settings.fanke_client_secret,
        return_url=settings.fanke_return_url,
        platform_code=settings.fanke_platform_code,
    )


def _build_token_warnings(token: dict) -> list[str]:
    """生成凡科 token 状态提醒"""
    warnings: list[str] = []
    refresh_expires_at = parse_iso_datetime(token.get("refresh_token_expires_at"))
    if not refresh_expires_at:
        return warnings

    now = datetime.now(timezone.utc)
    if refresh_expires_at <= now:
        warnings.append("凡科refresh_token已过期，需要重新授权")
    elif refresh_expires_at <= now + timedelta(days=5):
        warnings.append("凡科refresh_token将在5天内过期，请提前重新授权")
    return warnings
