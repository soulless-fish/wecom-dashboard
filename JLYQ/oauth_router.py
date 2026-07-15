"""
巨量引擎 OAuth 回调路由。

建议在主项目中挂载到 /api/v1/jlyq，最终回调地址为：
https://your-domain.example/api/v1/jlyq/oauth/callback
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, Response

from .oauth_client import exchange_auth_code, load_oauth_settings
from .token_storage import append_callback_log, build_token_record, load_token_record, mask_value, save_token_record


logger = logging.getLogger(__name__)
router = APIRouter()


def is_callback_app_id_mismatched(callback_app_id: Optional[str], configured_app_id: str) -> bool:
    """判断回调传入的 app_id 是否和服务器配置的应用 ID 不一致。"""
    return bool(callback_app_id and configured_app_id and callback_app_id.strip() != configured_app_id.strip())


@router.head("/oauth/callback", summary="巨量引擎OAuth回调地址HEAD探测")
async def check_jlyq_oauth_callback() -> Response:
    """支持平台或人工用 HEAD 请求检测回调地址。"""
    return Response(status_code=200)


@router.get("/oauth/callback", summary="巨量引擎OAuth授权回调")
async def handle_jlyq_oauth_callback(
    auth_code: Optional[str] = Query(None, description="巨量引擎授权后回传的授权码"),
    state: Optional[str] = Query(None, description="授权链接中携带的自定义状态"),
    app_id: Optional[str] = Query(None, description="巨量引擎授权回调携带的应用ID"),
):
    """
    接收巨量引擎 OAuth 授权回调。

    已配置 JLYQ_APP_ID/JLYQ_APP_SECRET 时自动换取并保存 token；
    未配置时只确认回调地址可用，真实授权前需先补齐环境变量。
    """
    settings = load_oauth_settings()
    append_callback_log({
        "event": "oauth_callback",
        "has_auth_code": bool(auth_code),
        "auth_code": auth_code or "",
        "state": state or "",
        "callback_app_id": app_id or "",
        "configured": settings.ready_for_token_exchange,
    })
    logger.info(
        "收到巨量引擎OAuth回调: has_auth_code=%s, state=%s, callback_app_id=%s, configured=%s",
        bool(auth_code),
        state or "",
        app_id or "",
        settings.ready_for_token_exchange,
    )

    if not auth_code:
        return {
            "code": 0,
            "message": "巨量引擎OAuth回调地址可用",
            "data": {
                "callback_url": settings.callback_url,
                "received_auth_code": False,
                "state": state or "",
                "callback_app_id": app_id or "",
                "token_exchange_enabled": settings.ready_for_token_exchange,
            },
        }

    if not settings.ready_for_token_exchange:
        return {
            "code": -1,
            "message": "已收到auth_code，但服务器未配置JLYQ_APP_ID/JLYQ_APP_SECRET，暂未换取token",
            "data": {
                "received_auth_code": True,
                "state": state or "",
                "callback_app_id": app_id or "",
                "callback_url": settings.callback_url,
            },
        }

    if is_callback_app_id_mismatched(app_id, settings.app_id):
        logger.error(
            "巨量引擎OAuth回调应用ID和服务器配置不一致: callback_app_id=%s, configured_app_id=%s",
            app_id or "",
            settings.app_id,
        )
        return {
            "code": -1,
            "message": "巨量引擎OAuth回调app_id和服务器配置的JLYQ_APP_ID不一致，请更新服务器配置后重新授权",
            "data": {
                "state": state or "",
                "callback_app_id": app_id or "",
                "configured_app_id": settings.app_id,
            },
        }

    token_response = exchange_auth_code(auth_code=auth_code, settings=settings)
    if token_response.get("code") not in {0, "0", None}:
        logger.error("巨量引擎OAuth换取token失败: %s", token_response)
        return {
            "code": -1,
            "message": "巨量引擎OAuth换取token失败",
            "data": {
                "state": state or "",
                "raw_code": token_response.get("code"),
                "raw_message": token_response.get("message") or token_response.get("msg") or "",
            },
        }

    token_record = build_token_record(token_response, state=state or "")
    save_token_record(token_record)
    return {
        "code": 0,
        "message": "巨量引擎OAuth授权成功，token已保存",
        "data": {
            "state": state or "",
            "advertiser_ids": token_record.get("advertiser_ids", []),
            "access_token": mask_value(token_record.get("access_token", "")),
            "refresh_token": mask_value(token_record.get("refresh_token", "")),
            "access_token_expires_at": token_record.get("access_token_expires_at", ""),
            "refresh_token_expires_at": token_record.get("refresh_token_expires_at", ""),
        },
    }


@router.get("/oauth/status", summary="查看巨量引擎授权状态")
async def get_jlyq_oauth_status():
    """查看当前服务器是否已经保存巨量引擎授权结果。"""
    settings = load_oauth_settings()
    token = load_token_record()
    return {
        "code": 0,
        "message": "success",
        "data": {
            "callback_url": settings.callback_url,
            "token_exchange_enabled": settings.ready_for_token_exchange,
            "authorized": bool(token.get("authorized")),
            "advertiser_ids": token.get("advertiser_ids", []),
            "access_token": mask_value(token.get("access_token", "")),
            "refresh_token": mask_value(token.get("refresh_token", "")),
            "access_token_expires_at": token.get("access_token_expires_at", ""),
            "refresh_token_expires_at": token.get("refresh_token_expires_at", ""),
            "updated_at": token.get("updated_at", ""),
        },
    }


@router.get("/oauth/callback-page", summary="巨量引擎OAuth回调说明页")
async def get_jlyq_oauth_callback_page() -> HTMLResponse:
    """提供一个简单页面，方便人工打开确认回调用途。"""
    settings = load_oauth_settings()
    html = f"""
    <!doctype html>
    <html lang="zh-CN">
      <head><meta charset="utf-8"><title>巨量引擎授权回调</title></head>
      <body>
        <h1>巨量引擎授权回调地址</h1>
        <p>请在巨量营销应用后台填写：{settings.callback_url}</p>
        <p>授权完成后，平台会把 auth_code 和 state 拼接到该地址。</p>
      </body>
    </html>
    """
    return HTMLResponse(content=html)
