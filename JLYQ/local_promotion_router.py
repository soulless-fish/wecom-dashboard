"""
巨量引擎本地推侧边栏接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database.models import JlyqLocalPromotionMetric, get_db

from .local_promotion_mapping import load_permission_bindings
from .local_promotion_service import get_sidebar_local_promotion_data, sync_jlyq_local_promotion_metrics
from .token_storage import load_token_record, mask_value
from .wecom_collection_service import (
    get_public_collection_form,
    mark_collection_form_sent,
    prepare_collection_form,
    submit_public_collection_form,
    sync_wecom_collection_forms,
)


router = APIRouter()


class CreateCollectionFormRequest(BaseModel):
    """创建并发送商家填写表所需的当前外部群和巨量账号上下文。"""

    group_name: str = ""
    poi_name: str = ""
    chat_id: str = ""
    binding_key: str = Field(..., min_length=1)
    account_id: str = ""
    account_name: str = ""


class CollectionFormSentRequest(BaseModel):
    """前端发送卡片后的确认参数。"""

    sent: bool = True


class SyncCollectionFormsRequest(BaseModel):
    """按当前页面账号范围同步官方收集表答案。"""

    binding_keys: list[str] = Field(default_factory=list)


class SubmitPublicCollectionFormRequest(BaseModel):
    """安全网页填写页提交的四项商家数据。"""

    wechat_count: Any
    recycle_count: Any
    sales_count: Any
    profit_yuan: Any


@router.get("/local-promotion/sidebar-data", summary="获取侧边栏巨量本地推数据")
async def get_local_promotion_sidebar_data(
    group_name: str = Query("", description="企业微信外部群名称"),
    poi_name: str = Query("", description="侧边栏当前门店名称"),
    extra_store_names: str = Query("", description="额外门店名，多个用英文逗号分隔"),
    db: Session = Depends(get_db),
):
    """按群名和门店名返回当前外部群可查看的本地推账号数据。"""
    extra_names = [item.strip() for item in extra_store_names.split(",") if item.strip()]
    data = get_sidebar_local_promotion_data(
        db,
        group_name=group_name,
        poi_name=poi_name,
        extra_store_names=extra_names,
    )
    return {
        "code": 0,
        "message": data.get("message", "success"),
        "data": data,
    }


@router.post("/local-promotion/sync", summary="手动同步巨量本地推数据")
async def sync_local_promotion_data():
    """手动触发巨量本地推数据同步，主要用于部署后验证和后台维护。"""
    result = await run_in_threadpool(sync_jlyq_local_promotion_metrics)
    return {
        "code": 0 if result.get("success") else -1,
        "message": "success" if result.get("success") else result.get("error", "巨量本地推同步未写入数据"),
        "data": result,
    }


@router.get("/local-promotion/status", summary="查看巨量本地推同步状态")
async def get_local_promotion_status(db: Session = Depends(get_db)):
    """查看授权、权限表和本地数据同步状态。"""
    token = load_token_record()
    latest = (
        db.query(JlyqLocalPromotionMetric)
        .order_by(JlyqLocalPromotionMetric.synced_at.desc())
        .first()
    )
    record_count = db.query(JlyqLocalPromotionMetric).count()
    bindings = load_permission_bindings()
    return {
        "code": 0,
        "message": "success",
        "data": {
            "authorized": bool(token.get("authorized")),
            "access_token": mask_value(token.get("access_token", "")),
            "permission_binding_count": len(bindings),
            "metric_record_count": record_count,
            "latest_data_month": latest.data_month if latest else "",
            "latest_synced_at": latest.synced_at.isoformat() if latest and latest.synced_at else "",
        },
    }


@router.post("/local-promotion/collection-form", summary="创建商家每日填写表")
async def create_collection_form(
    request: CreateCollectionFormRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """优先创建企业微信官方收集表，权限不足时自动切换安全网页填写表。"""
    try:
        data = await prepare_collection_form(
            db,
            settings,
            group_name=request.group_name,
            poi_name=request.poi_name,
            chat_id=request.chat_id,
            binding_key=request.binding_key,
            account_id=request.account_id,
            account_name=request.account_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "message": "success", "data": data}


@router.post("/local-promotion/collection-form/{record_id}/sent", summary="确认填写表已发送")
async def confirm_collection_form_sent(
    record_id: int,
    request: CollectionFormSentRequest,
    db: Session = Depends(get_db),
):
    """卡片发送成功后记录发送时间，便于页面展示状态。"""
    if not request.sent:
        return {"code": 0, "message": "未标记发送", "data": None}
    try:
        data = mark_collection_form_sent(db, record_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "message": "success", "data": data}


@router.post("/local-promotion/collection-form/sync", summary="同步商家填写结果")
async def sync_collection_form_answers(
    request: SyncCollectionFormsRequest,
    settings: Settings = Depends(get_settings),
):
    """立即同步当前页面相关的企业微信官方收集表答案。"""
    data = await sync_wecom_collection_forms(settings, request.binding_keys)
    return {
        "code": 0 if data.get("success") else -1,
        "message": "success" if data.get("success") else "部分填写表同步失败",
        "data": data,
    }


@router.get("/local-promotion/collection-form/public/{public_token}", summary="读取公开填写表")
async def read_public_collection_form(
    public_token: str,
    db: Session = Depends(get_db),
):
    """通过不可猜测的随机令牌读取商家填写页。"""
    try:
        data = get_public_collection_form(db, public_token)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "message": "success", "data": data}


@router.post("/local-promotion/collection-form/public/{public_token}", summary="提交公开填写表")
async def save_public_collection_form(
    public_token: str,
    request: SubmitPublicCollectionFormRequest,
    db: Session = Depends(get_db),
):
    """保存商家填写值，重复提交会覆盖当天数据并重新计算月累计。"""
    try:
        data = submit_public_collection_form(
            db,
            public_token,
            wechat_count=request.wechat_count,
            recycle_count=request.recycle_count,
            sales_count=request.sales_count,
            profit_yuan=request.profit_yuan,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "message": "提交成功", "data": data}
