from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database.models import get_db
from app.services.ai_store_context import (
    StoreMatch,
    build_context_response,
    build_single_store_context,
    get_store_by_poi_id,
    resolve_store_matches,
)
from app.services.token_manager import TokenManager
from app.services.wecom_client import WeComClient

router = APIRouter()


class StoreContextRequest(BaseModel):
    chat_id: Optional[str] = Field(None, description="企业微信外部群 chat_id")
    group_name: Optional[str] = Field(None, description="企业微信外部群名称")
    topic: Optional[str] = Field(None, description="AI 当前关注主题，例如 video_progress")
    data_month: Optional[str] = Field(None, description="指定数据月份，格式 YYYY-MM；不传则取门店最新月份")


class ResolveStoreRequest(BaseModel):
    chat_id: Optional[str] = Field(None, description="企业微信外部群 chat_id")
    group_name: Optional[str] = Field(None, description="企业微信外部群名称")
    data_month: Optional[str] = Field(None, description="指定数据月份，格式 YYYY-MM；不传则取门店最新月份")


def verify_internal_token(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    settings: Settings = Depends(get_settings),
) -> None:
    token = getattr(settings, "internal_api_token", "")
    if token and x_internal_token != token:
        raise HTTPException(status_code=401, detail="内部接口令牌无效")


async def _resolve_group_name_from_chat_id(
    chat_id: Optional[str],
    settings: Settings,
) -> tuple[Optional[str], Optional[str]]:
    if not chat_id:
        return None, None

    if not settings.wecom_corp_id or not settings.wecom_secret:
        return None, "企业微信配置未设置，无法通过 chat_id 查询群名"

    try:
        token_manager = TokenManager(settings)
        access_token = await token_manager.get_access_token()
        client = WeComClient(access_token)
        group_info = await client.get_group_chat(chat_id)
    except Exception as exc:
        return None, f"通过 chat_id 查询群名失败: {exc}"

    group_chat = group_info.get("group_chat") if isinstance(group_info, dict) else None
    if isinstance(group_chat, dict):
        name = group_chat.get("name")
        if name:
            return str(name), None

    if isinstance(group_info, dict):
        name = group_info.get("name")
        if name:
            return str(name), None

    return None, "企业微信接口未返回群名"


async def _resolve_effective_group_name(
    chat_id: Optional[str],
    group_name: Optional[str],
    settings: Settings,
) -> tuple[Optional[str], list[str]]:
    warnings: list[str] = []
    if group_name and group_name.strip():
        return group_name.strip(), warnings

    resolved_group_name, warning = await _resolve_group_name_from_chat_id(chat_id, settings)
    if warning:
        warnings.append(warning)
    return resolved_group_name, warnings


@router.post(
    "/store-context",
    summary="AI统一门店上下文接口",
    dependencies=[Depends(verify_internal_token)],
)
async def get_ai_store_context(
    request: StoreContextRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    根据企业微信群信息返回门店实时经营数据、任务达标情况和可直接拼入AI上下文的摘要。
    """
    effective_group_name, warnings = await _resolve_effective_group_name(
        request.chat_id,
        request.group_name,
        settings,
    )

    if not effective_group_name:
        return {
            "code": 0,
            "message": "success",
            "data": {
                "found": False,
                "reason": "缺少 group_name，且无法通过 chat_id 获取群名",
                "warnings": warnings,
            },
        }

    matches, match_warnings = resolve_store_matches(
        db,
        effective_group_name,
        data_month=request.data_month,
    )
    data = build_context_response(
        matches,
        chat_id=request.chat_id,
        group_name=effective_group_name,
        warnings=warnings + match_warnings,
    )
    data["topic"] = request.topic

    return {
        "code": 0,
        "message": "success",
        "data": data,
    }


@router.post(
    "/resolve-store",
    summary="根据群信息解析门店",
    dependencies=[Depends(verify_internal_token)],
)
async def resolve_store(
    request: ResolveStoreRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    只返回群到门店的解析结果，不返回完整经营指标。
    """
    effective_group_name, warnings = await _resolve_effective_group_name(
        request.chat_id,
        request.group_name,
        settings,
    )

    if not effective_group_name:
        return {
            "code": 0,
            "message": "success",
            "data": {
                "found": False,
                "reason": "缺少 group_name，且无法通过 chat_id 获取群名",
                "warnings": warnings,
            },
        }

    matches, match_warnings = resolve_store_matches(
        db,
        effective_group_name,
        data_month=request.data_month,
    )

    stores = []
    for match in matches:
        store = match.store
        stores.append(
            {
                "chat_id": request.chat_id,
                "group_name": effective_group_name,
                "store_id": store.poi_id,
                "poi_id": store.poi_id,
                "store_name": store.poi_name,
                "poi_name": store.poi_name,
                "store_score": float(store.poi_score) if store.poi_score is not None else None,
                "data_month": store.data_month,
                "data_start_day": store.data_start_day,
                "data_end_day": store.data_end_day,
                "match_method": match.match_method,
                "match_confidence": match.match_confidence,
                "match_reason": match.match_reason,
            }
        )

    return {
        "code": 0,
        "message": "success",
        "data": {
            "found": bool(stores),
            "stores": stores,
            "store": stores[0] if stores else None,
            "warnings": warnings + match_warnings,
        },
    }


@router.get(
    "/store-performance/{poi_id}",
    summary="根据门店ID获取AI经营上下文",
    dependencies=[Depends(verify_internal_token)],
)
async def get_store_performance_context(
    poi_id: str,
    data_month: Optional[str] = Query(None, description="指定数据月份，格式 YYYY-MM；不传则取最新月份"),
    db: Session = Depends(get_db),
):
    """
    根据 poi_id 直接返回单门店经营指标和达标情况。
    """
    store = get_store_by_poi_id(db, poi_id, data_month=data_month)
    if not store:
        return {
            "code": 0,
            "message": "success",
            "data": {
                "found": False,
                "reason": f"未找到 poi_id={poi_id} 的门店数据",
            },
        }

    context = build_single_store_context(
        match=StoreMatch(
            store=store,
            match_method="poi_id_direct",
            match_confidence=1.0,
            match_reason="调用方直接提供 poi_id",
        ),
        chat_id=None,
        group_name=None,
    )

    return {
        "code": 0,
        "message": "success",
        "data": {
            "found": True,
            **context,
        },
    }
