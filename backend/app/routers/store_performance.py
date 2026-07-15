"""
门店业绩数据API路由

提供门店上翻收益、直播时长、视频数量等数据的查询和同步接口

作者: Claude AI
创建日期: 2026-01-15
"""

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
from pydantic import BaseModel, Field

from app.config import get_settings
from app.database.models import (
    get_db, init_db, StorePerformance, DataSyncLog,
    format_duration, parse_duration_to_seconds
)
from app.services.gmv_data_client import GmvDataClient
from app.services.scheduler import (
    configure_scheduler_cookie, configure_life_data_cookie,
    start_scheduler, stop_scheduler,
    get_scheduler_status, sync_current_month_data,
    _fetch_poi_overview, _fetch_poi_verify
)
from app.services.cookie_storage import load_cookie, save_cookie, load_life_data_cookie, save_life_data_cookie
from app.services.store_phone_mapping import load_store_phone_mapping, get_store_phone_display
from app.services.verify_average_client import (
    VerifyAverageAuthError,
    VerifyAverageClient,
    VerifyAverageError,
)

router = APIRouter()
_settings = get_settings()


# ==================== 请求/响应模型 ====================

class CookieConfigRequest(BaseModel):
    """Cookie配置请求"""
    cookie: str = Field(..., description="life.douyin.com的Cookie")
    account_id: str = Field(..., description="商户账户ID")
    csrf_token: Optional[str] = Field(None, description="CSRF Token")


class LifeDataCookieConfigRequest(BaseModel):
    """来客后台Cookie配置请求"""
    cookie: str = Field(..., description="life-data.cn的Cookie")
    life_account_id: str = Field(..., description="来客商户账户ID")
    csrf_token: Optional[str] = Field(None, description="CSRF Token")


class SyncRequest(BaseModel):
    """数据同步请求"""
    year: int = Field(..., description="年份")
    month: int = Field(..., ge=1, le=12, description="月份(1-12)")


class StorePerformanceResponse(BaseModel):
    """门店业绩响应"""
    poi_name: str
    gmv_yuan: float
    live_duration_formatted: str
    video_count: int
    data_month: str


# ==================== Cookie配置存储 ====================

# 从持久化文件加载Cookie配置（服务重启后不丢失）
_gmv_cookie_config = load_cookie()
_life_data_cookie_config = load_life_data_cookie()

# 平均核销金额由所有侧边栏共用，避免每个侧边栏都直接请求生意经并触发限流。
_verify_average_cache: Optional[dict[str, Any]] = None
_verify_average_cache_time = 0.0
_verify_average_cache_lock = asyncio.Lock()
_VERIFY_AVERAGE_CACHE_TTL_SECONDS = 300


def get_gmv_client() -> GmvDataClient:
    """获取上翻收益数据客户端"""
    if not _gmv_cookie_config["cookie"] or not _gmv_cookie_config["account_id"]:
        raise HTTPException(
            status_code=400,
            detail="Cookie未配置，请先调用 /cookie 接口配置"
        )

    return GmvDataClient(
        cookie=_gmv_cookie_config["cookie"],
        account_id=_gmv_cookie_config["account_id"],
        csrf_token=_gmv_cookie_config.get("csrf_token")
    )


# ==================== API路由 ====================

@router.post("/cookie", summary="配置上翻收益Cookie")
async def update_cookie(request: CookieConfigRequest):
    """
    配置life.douyin.com的Cookie

    用于设置访问上翻收益API所需的认证信息
    """
    _gmv_cookie_config["cookie"] = request.cookie
    _gmv_cookie_config["account_id"] = request.account_id
    _gmv_cookie_config["csrf_token"] = request.csrf_token

    # 持久化存储Cookie到文件
    save_cookie(request.cookie, request.account_id, request.csrf_token or "")

    # 同步更新调度器的Cookie配置
    configure_scheduler_cookie(
        request.cookie,
        request.account_id,
        request.csrf_token or ""
    )

    return {
        "code": 0,
        "message": "Cookie配置成功",
        "data": {
            "account_id": request.account_id,
            "cookie_length": len(request.cookie)
        }
    }


@router.get("/cookie/status", summary="获取Cookie配置状态")
async def get_cookie_status():
    """获取当前Cookie配置状态"""
    return {
        "code": 0,
        "message": "success",
        "data": {
            "configured": bool(_gmv_cookie_config["cookie"]),
            "account_id": _gmv_cookie_config.get("account_id", ""),
            "life_data_configured": bool(_life_data_cookie_config.get("cookie")),
            "life_account_id": _life_data_cookie_config.get("life_account_id", "")
        }
    }


@router.get("/verify-average", summary="获取平均核销金额")
async def get_verify_average():
    """使用现有生意经 Cookie 获取当月和近30天平均核销金额。"""
    global _verify_average_cache, _verify_average_cache_time

    if not _life_data_cookie_config.get("cookie") or not _life_data_cookie_config.get("life_account_id"):
        raise HTTPException(status_code=400, detail="来客后台Cookie未配置")

    now = time.monotonic()
    if _verify_average_cache and now - _verify_average_cache_time < _VERIFY_AVERAGE_CACHE_TTL_SECONDS:
        # 短时间内的重复请求直接返回缓存，降低生意经接口压力。
        return {"code": 0, "message": "success", "data": _verify_average_cache}

    # 只允许一个请求刷新缓存，其余侧边栏等待同一份结果。
    async with _verify_average_cache_lock:
        now = time.monotonic()
        if _verify_average_cache and now - _verify_average_cache_time < _VERIFY_AVERAGE_CACHE_TTL_SECONDS:
            return {"code": 0, "message": "success", "data": _verify_average_cache}

        client = VerifyAverageClient(
            cookie=_life_data_cookie_config["cookie"],
            life_account_id=_life_data_cookie_config["life_account_id"],
            csrf_token=_life_data_cookie_config.get("csrf_token"),
        )
        try:
            data = await client.fetch_average_metrics()
        except VerifyAverageAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except VerifyAverageError as exc:
            raise HTTPException(status_code=502, detail="平均核销金额暂时无法获取，请稍后重试") from exc

        _verify_average_cache = data
        _verify_average_cache_time = time.monotonic()

        return {
            "code": 0,
            "message": "success",
            "data": data,
        }


@router.post("/life-data-cookie", summary="配置来客后台Cookie")
async def update_life_data_cookie(request: LifeDataCookieConfigRequest):
    """
    配置life-data.cn的Cookie

    用于设置访问来客后台门店概览API所需的认证信息
    """
    _life_data_cookie_config["cookie"] = request.cookie
    _life_data_cookie_config["life_account_id"] = request.life_account_id
    _life_data_cookie_config["csrf_token"] = request.csrf_token

    # Cookie 更新后清除旧的平均值缓存，下一次请求必须使用新的登录态重新抓取。
    global _verify_average_cache, _verify_average_cache_time
    _verify_average_cache = None
    _verify_average_cache_time = 0.0

    # 持久化存储Cookie到文件
    save_life_data_cookie(request.cookie, request.life_account_id, request.csrf_token or "")

    # 同步更新调度器的来客后台Cookie配置
    configure_life_data_cookie(
        request.cookie,
        request.life_account_id,
        request.csrf_token or ""
    )

    return {
        "code": 0,
        "message": "来客后台Cookie配置成功",
        "data": {
            "life_account_id": request.life_account_id,
            "cookie_length": len(request.cookie)
        }
    }


@router.post("/sync", summary="同步指定月份数据")
async def sync_month_data(
    request: SyncRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    同步指定月份的门店业绩数据

    从抖音来客后台获取数据并存入数据库
    """
    # 初始化数据库
    init_db()

    # 创建同步日志
    data_month = f"{request.year}-{request.month:02d}"
    sync_log = DataSyncLog(
        sync_type="gmv_data",
        data_month=data_month,
        status="running",
        started_at=datetime.now()
    )
    db.add(sync_log)
    db.commit()
    db.refresh(sync_log)

    # 在后台执行同步任务
    background_tasks.add_task(
        _do_sync,
        request.year,
        request.month,
        sync_log.id
    )

    return {
        "code": 0,
        "message": "同步任务已启动",
        "data": {
            "sync_log_id": sync_log.id,
            "data_month": data_month,
            "status": "running"
        }
    }


async def _do_sync(year: int, month: int, sync_log_id: int):
    """执行同步任务（带事务保护）"""
    from app.database.models import SessionLocal
    from calendar import monthrange
    import logging

    logger = logging.getLogger(__name__)
    db = SessionLocal()

    try:
        client = get_gmv_client()
        result = await client.fetch_month_data(year, month)

        data_month = f"{year}-{month:02d}"

        # 计算日期范围
        today = datetime.now()
        start_day_num = 1
        if year == today.year and month == today.month:
            # 当前月份：1号到昨天
            end_day_num = today.day - 1 if today.day > 1 else 1
        else:
            # 过去月份：1号到月末
            end_day_num = monthrange(year, month)[1]

        # 事务保护：删除+插入在同一事务中
        # 清除该月份的旧数据
        db.query(StorePerformance).filter(
            StorePerformance.data_month == data_month
        ).delete()

        # 插入新数据
        for store in result["stores"]:
            # 解析直播时长
            live_duration_seconds = GmvDataClient._parse_duration(
                store.get("live_duration")
            )
            live_duration_formatted = format_duration(live_duration_seconds)

            # GMV（API返回的就是元）
            gmv_yuan = Decimal(str(store.get("write_off_order_gmv", 0)))

            # 视频数量
            video_count = GmvDataClient._safe_int(store.get("video_count"))

            record = StorePerformance(
                poi_id=str(store.get("poi_id", "")),
                poi_name=store.get("poi_name", ""),
                gmv_yuan=gmv_yuan,
                live_duration_formatted=live_duration_formatted,
                live_duration_seconds=live_duration_seconds,
                video_count=video_count,
                data_month=data_month,
                data_start_day=start_day_num,
                data_end_day=end_day_num
            )
            db.add(record)

        # 数据操作成功，提交事务
        db.commit()

        # 更新同步日志（成功）
        sync_log = db.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
        if sync_log:
            sync_log.status = "success"
            sync_log.total_records = len(result["stores"])
            sync_log.finished_at = datetime.now()
            db.commit()

        await client.close()

    except Exception as e:
        # 异常时先回滚事务，防止删除操作被持久化
        try:
            db.rollback()
        except Exception as rb_err:
            logger.error(f"回滚事务失败: {rb_err}")

        # 使用独立session写失败日志，避免影响主事务
        log_db = SessionLocal()
        try:
            sync_log = log_db.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
            if sync_log:
                sync_log.status = "failed"
                sync_log.error_message = str(e)
                sync_log.finished_at = datetime.now()
                log_db.commit()
        except Exception as log_err:
            logger.error(f"写入失败日志失败: {log_err}")
        finally:
            log_db.close()

        raise
    finally:
        db.close()


@router.get("/sync/status/{sync_log_id}", summary="获取同步任务状态")
async def get_sync_status(sync_log_id: int, db: Session = Depends(get_db)):
    """获取同步任务的状态"""
    sync_log = db.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()

    if not sync_log:
        raise HTTPException(status_code=404, detail="同步任务不存在")

    return {
        "code": 0,
        "message": "success",
        "data": {
            "id": sync_log.id,
            "sync_type": sync_log.sync_type,
            "data_month": sync_log.data_month,
            "status": sync_log.status,
            "total_records": sync_log.total_records,
            "error_message": sync_log.error_message,
            "started_at": sync_log.started_at.isoformat() if sync_log.started_at else None,
            "finished_at": sync_log.finished_at.isoformat() if sync_log.finished_at else None
        }
    }


@router.get("/stores", summary="获取门店业绩列表")
async def get_stores(
    data_month: Optional[str] = Query(None, description="数据月份(YYYY-MM)，默认当前月"),
    poi_name: Optional[str] = Query(None, description="门店名称(模糊搜索)"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db)
):
    """
    获取门店业绩列表

    返回指定月份的门店业绩数据
    """
    # 初始化数据库
    init_db()

    # 默认当前月
    if not data_month:
        today = date.today()
        data_month = f"{today.year}-{today.month:02d}"

    # 构建查询
    query = db.query(StorePerformance).filter(
        StorePerformance.data_month == data_month
    )

    # 门店名称搜索
    if poi_name:
        query = query.filter(StorePerformance.poi_name.contains(poi_name))

    # 总数
    total = query.count()

    # 分页
    stores = query.order_by(StorePerformance.gmv_yuan.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    phone_mapping = load_store_phone_mapping()

    return {
        "code": 0,
        "message": "success",
        "data": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data_month": data_month,
            "stores": [
                {
                    "poi_name": s.poi_name,
                    "store_phone": get_store_phone_display(s.poi_id, mapping=phone_mapping),
                    "gmv_yuan": s.gmv_yuan,
                    "live_duration_formatted": s.live_duration_formatted,
                    "video_count": s.video_count,
                    "video_cnt_1d": s.video_cnt_1d,
                    "poi_score": float(s.poi_score) if s.poi_score is not None else None,
                    "verify_amount_realtime": s.verify_amount_realtime,
                    "verify_cert_cnt_realtime": s.verify_cert_cnt_realtime,
                    "verify_amount": s.verify_amount,
                    "verify_cert_cnt": s.verify_cert_cnt,
                }
                for s in stores
            ]
        }
    }


@router.get("/store/{poi_name}", summary="获取单个门店业绩")
async def get_store_by_name(
    poi_name: str,
    data_month: Optional[str] = Query(None, description="数据月份(YYYY-MM)"),
    db: Session = Depends(get_db)
):
    """
    根据门店名称获取业绩数据

    用于企业微信侧边栏展示
    """
    # 初始化数据库
    init_db()

    # 默认当前月
    if not data_month:
        today = date.today()
        data_month = f"{today.year}-{today.month:02d}"

    # 精确匹配或模糊匹配
    store = db.query(StorePerformance).filter(
        StorePerformance.data_month == data_month,
        StorePerformance.poi_name == poi_name
    ).first()

    # 如果精确匹配没找到，尝试模糊匹配
    if not store:
        store = db.query(StorePerformance).filter(
            StorePerformance.data_month == data_month,
            StorePerformance.poi_name.contains(poi_name)
        ).first()

    if not store:
        return {
            "code": -1,
            "message": "未找到门店数据",
            "data": None
        }
    phone_mapping = load_store_phone_mapping()

    return {
        "code": 0,
        "message": "success",
        "data": {
            "poi_name": store.poi_name,
            "store_phone": get_store_phone_display(store.poi_id, mapping=phone_mapping),
            "gmv_yuan": store.gmv_yuan,
            "live_duration_formatted": store.live_duration_formatted,
            "video_count": store.video_count,
            "video_cnt_1d": store.video_cnt_1d,
            "poi_score": float(store.poi_score) if store.poi_score is not None else None,
            "verify_amount_realtime": store.verify_amount_realtime,
            "verify_cert_cnt_realtime": store.verify_cert_cnt_realtime,
            "verify_amount": store.verify_amount,
            "verify_cert_cnt": store.verify_cert_cnt,
            "data_month": store.data_month
        }
    }


@router.get("/summary", summary="获取月度汇总数据")
async def get_summary(
    data_month: Optional[str] = Query(None, description="数据月份(YYYY-MM)"),
    db: Session = Depends(get_db)
):
    """获取指定月份的汇总数据"""
    # 初始化数据库
    init_db()

    # 默认当前月
    if not data_month:
        today = date.today()
        data_month = f"{today.year}-{today.month:02d}"

    # 查询该月所有数据
    stores = db.query(StorePerformance).filter(
        StorePerformance.data_month == data_month
    ).all()

    if not stores:
        return {
            "code": 0,
            "message": "success",
            "data": {
                "data_month": data_month,
                "total_stores": 0,
                "total_gmv_yuan": 0,
                "total_live_duration_formatted": "0天0小时0分钟",
                "total_video_count": 0,
                "total_video_cnt_1d": 0,
                "total_verify_amount_realtime": "¥0.00",
                "total_verify_cert_cnt_realtime": 0,
                "total_verify_amount": "¥0.00",
                "total_verify_cert_cnt": 0
            }
        }

    # 计算汇总
    total_gmv = sum(s.gmv_yuan for s in stores)
    total_live_seconds = sum(s.live_duration_seconds for s in stores)
    total_video = sum(s.video_count for s in stores)
    total_video_cnt_1d = sum(s.video_cnt_1d or 0 for s in stores)

    def sum_amount_field(field_name: str) -> str:
        total_fen = 0
        for item in stores:
            amount_str = getattr(item, field_name, None) or "¥0.00"
            try:
                num_str = amount_str.replace("¥", "").replace(",", "")
                total_fen += round(float(num_str) * 100)
            except (ValueError, AttributeError):
                pass
        return f"¥{total_fen / 100:,.2f}"

    # 核销金额汇总：解析字符串求和后重新格式化
    total_verify_amount_realtime = sum_amount_field("verify_amount_realtime")
    total_verify_cert_cnt_realtime = sum(s.verify_cert_cnt_realtime or 0 for s in stores)
    total_verify_fen = 0
    for s in stores:
        amount_str = s.verify_amount or "¥0.00"
        try:
            num_str = amount_str.replace("¥", "").replace(",", "")
            total_verify_fen += round(float(num_str) * 100)
        except (ValueError, AttributeError):
            pass
    total_verify_yuan = total_verify_fen / 100
    total_verify_amount = f"¥{total_verify_yuan:,.2f}"
    total_verify_cert_cnt = sum(s.verify_cert_cnt or 0 for s in stores)

    return {
        "code": 0,
        "message": "success",
        "data": {
            "data_month": data_month,
            "total_stores": len(stores),
            "total_gmv_yuan": round(total_gmv, 2),
            "total_live_duration_formatted": format_duration(total_live_seconds),
            "total_video_count": total_video,
            "total_video_cnt_1d": total_video_cnt_1d,
            "total_verify_amount_realtime": total_verify_amount_realtime,
            "total_verify_cert_cnt_realtime": total_verify_cert_cnt_realtime,
            "total_verify_amount": total_verify_amount,
            "total_verify_cert_cnt": total_verify_cert_cnt
        }
    }


# ==================== 定时任务管理 ====================

@router.post("/scheduler/start", summary="启动定时任务")
async def start_scheduler_api():
    """
    启动每日自动更新定时任务

    需要先配置Cookie才能启动
    """
    if not _gmv_cookie_config["cookie"]:
        raise HTTPException(status_code=400, detail="请先配置Cookie")

    start_scheduler()

    return {
        "code": 0,
        "message": "定时任务已启动",
        "data": get_scheduler_status()
    }


@router.post("/scheduler/stop", summary="停止定时任务")
async def stop_scheduler_api():
    """停止定时任务"""
    stop_scheduler()

    return {
        "code": 0,
        "message": "定时任务已停止",
        "data": {"running": False}
    }


@router.get("/scheduler/status", summary="获取定时任务状态")
async def get_scheduler_status_api():
    """获取定时任务调度器状态"""
    return {
        "code": 0,
        "message": "success",
        "data": get_scheduler_status()
    }


@router.post("/scheduler/run-now", summary="立即执行同步任务")
async def run_sync_now(background_tasks: BackgroundTasks):
    """
    立即执行一次数据同步

    不等待定时任务，立即执行当前月份的数据同步
    """
    if not _gmv_cookie_config["cookie"]:
        raise HTTPException(status_code=400, detail="请先配置Cookie")

    background_tasks.add_task(sync_current_month_data)

    return {
        "code": 0,
        "message": "同步任务已加入后台执行",
        "data": None
    }


@router.post("/sync-poi-overview", summary="单独同步门店概览数据")
async def sync_poi_overview_now(background_tasks: BackgroundTasks):
    """
    单独同步门店概览数据（video_cnt_1d, poi_score）和核销数据

    不依赖上翻收益Cookie，仅使用来客后台Cookie获取数据。
    会更新数据库中已有门店记录的video_cnt_1d、poi_score、
    当月实时核销金额/券数、近30天核销金额/券数字段。
    """
    if not _life_data_cookie_config.get("cookie") or not _life_data_cookie_config.get("life_account_id"):
        raise HTTPException(status_code=400, detail="来客后台Cookie未配置")

    background_tasks.add_task(_do_poi_overview_sync)

    return {
        "code": 0,
        "message": "门店概览和核销数据同步任务已加入后台执行",
        "data": None
    }


async def _do_poi_overview_sync():
    """独立执行门店概览数据同步（严格校验后一次性提交）"""
    import logging
    from app.database.models import SessionLocal
    from datetime import timedelta

    logger = logging.getLogger(__name__)

    logger.warning("开始独立同步门店概览数据...")

    db = SessionLocal()
    try:
        # 获取日期范围（当月1号到昨天）
        today = datetime.now()
        start_date = today.replace(day=1).strftime("%Y-%m-%d")
        yesterday = today - timedelta(days=1)
        end_date = yesterday.strftime("%Y-%m-%d")
        data_month = today.strftime("%Y-%m")

        # 创建共享客户端，概览和核销共用同一个实例（缓存分页结果，减少API请求）
        from app.services.life_data_client import LifeDataClient
        from app.services.cookie_storage import load_life_data_cookie
        life_cfg = load_life_data_cookie()
        shared_life_client = LifeDataClient(
            cookie=life_cfg["cookie"],
            life_account_id=life_cfg["life_account_id"],
            csrf_token=life_cfg.get("csrf_token")
        )

        try:
            poi_overview = await _fetch_poi_overview(start_date, end_date, shared_client=shared_life_client)
            if not poi_overview:
                raise RuntimeError("门店概览数据为空，已中止更新，避免部分覆盖旧数据")

            logger.warning(f"获取到 {len(poi_overview)} 个门店的概览数据")

            # 获取核销数据（当月实时）- 复用共享客户端的当月Excel缓存
            poi_verify_realtime = await _fetch_poi_verify(
                start_date,
                end_date,
                shared_client=shared_life_client,
                range_type="realtime"
            )
            if not poi_verify_realtime:
                raise RuntimeError("门店实时核销数据为空，已中止更新，避免部分覆盖旧数据")

            # 获取核销数据（近30天）
            poi_verify = await _fetch_poi_verify(
                start_date,
                end_date,
                shared_client=shared_life_client,
                range_type="last_thirty_days"
            )
            if not poi_verify:
                raise RuntimeError("门店近30天核销数据为空，已中止更新，避免部分覆盖旧数据")
        finally:
            await shared_life_client.close()

        logger.warning(f"获取到 {len(poi_verify_realtime)} 个门店的实时核销数据")
        poi_verify_realtime_name_map = {}
        for pid, data in poi_verify_realtime.items():
            name = data.get("poi_name", "")
            if name:
                poi_verify_realtime_name_map[name] = data

        logger.warning(f"获取到 {len(poi_verify)} 个门店的近30天核销数据")
        poi_verify_name_map = {}
        for pid, data in poi_verify.items():
            name = data.get("poi_name", "")
            if name:
                poi_verify_name_map[name] = data

        # 建立poi_name到概览数据的映射（因为数据库中的poi_id可能为空）
        poi_name_map = {}
        for poi_id, data in poi_overview.items():
            poi_name = data.get("poi_name", "")
            if poi_name:
                poi_name_map[poi_name] = data

        logger.warning(f"建立poi_name映射: {len(poi_name_map)} 个门店")

        # 更新数据库中已有的门店记录
        updated_count = 0
        overview_updated_count = 0
        verify_realtime_updated_count = 0
        verify_updated_count = 0
        stores = db.query(StorePerformance).filter(
            StorePerformance.data_month == data_month
        ).all()

        # 当前月无数据时，回退到数据库中最新的月份（避免同步后无任何记录被更新）
        if not stores:
            latest = db.query(StorePerformance.data_month).order_by(
                StorePerformance.data_month.desc()
            ).first()
            if latest:
                data_month = latest[0]
                stores = db.query(StorePerformance).filter(
                    StorePerformance.data_month == data_month
                ).all()
                logger.warning(f"当月无数据，回退到最新月份: {data_month}，共 {len(stores)} 条记录")

        logger.warning(f"数据库中目标月份({data_month})门店数: {len(stores)}")

        for store in stores:
            try:
                store_updated = False

                # 优先用poi_id匹配，如果poi_id为空则用poi_name匹配
                overview = None
                if store.poi_id:
                    overview = poi_overview.get(store.poi_id, {})
                if not overview and store.poi_name:
                    overview = poi_name_map.get(store.poi_name, {})

                if overview:
                    store.video_cnt_1d = overview.get("video_cnt_1d", 0)
                    store.poi_score = Decimal(str(overview.get("poi_score", 0)))
                    overview_updated_count += 1
                    store_updated = True
                    # 如果数据库中没有poi_id，尝试从概览数据中获取并更新
                    if not store.poi_id:
                        for pid, pdata in poi_overview.items():
                            if pdata.get("poi_name") == store.poi_name:
                                store.poi_id = pid
                                break

                # 合并实时核销数据
                verify_realtime_data = None
                if store.poi_id:
                    verify_realtime_data = poi_verify_realtime.get(store.poi_id)
                if not verify_realtime_data and store.poi_name:
                    verify_realtime_data = poi_verify_realtime_name_map.get(store.poi_name)
                if verify_realtime_data:
                    store.verify_amount_realtime = verify_realtime_data.get("verify_amount", "¥0.00")
                    store.verify_cert_cnt_realtime = verify_realtime_data.get("verify_cert_cnt", 0)
                    verify_realtime_updated_count += 1
                    store_updated = True

                # 合并近30天核销数据
                verify_data = None
                if store.poi_id:
                    verify_data = poi_verify.get(store.poi_id)
                if not verify_data and store.poi_name:
                    verify_data = poi_verify_name_map.get(store.poi_name)
                if verify_data:
                    store.verify_amount = verify_data.get("verify_amount", "¥0.00")
                    store.verify_cert_cnt = verify_data.get("verify_cert_cnt", 0)
                    verify_updated_count += 1
                    store_updated = True

                if store_updated:
                    updated_count += 1

            except Exception as e:
                logger.warning(f"更新门店 {store.poi_id} ({store.poi_name}) 失败: {e}")
                # 单条失败不影响整体

        db.commit()
        logger.warning(
            f"门店概览数据独立同步完成: 更新了 {updated_count}/{len(stores)} 个门店, "
            f"概览字段匹配 {overview_updated_count} 条, 实时核销字段匹配 {verify_realtime_updated_count} 条, "
            f"近30天核销字段匹配 {verify_updated_count} 条"
        )

    except Exception as e:
        logger.error(f"门店概览数据独立同步失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        # 尝试回滚
        try:
            db.rollback()
        except:
            pass
    finally:
        db.close()
