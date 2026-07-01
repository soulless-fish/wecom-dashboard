"""
来客后台数据API路由

提供直播数据和门店/视频数据的获取接口

创建日期: 2026-01-13
"""

from fastapi import APIRouter, HTTPException, Query, Body
from typing import Optional
from datetime import datetime, timedelta
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.life_data_client import LifeDataClient, parse_excel_to_dict
from app.services.cookie_storage import load_life_data_cookie, save_life_data_cookie
from app.services.scheduler import configure_life_data_cookie

router = APIRouter()

_settings = get_settings()


class CookieUpdateRequest(BaseModel):
    """Cookie更新请求"""
    cookie: str = Field(..., description="来客后台Cookie字符串")
    life_account_id: str = Field(..., description="来客商户账户ID")
    csrf_token: Optional[str] = Field(None, description="x-secsdk-csrf-token (可选)")


class DateRangeRequest(BaseModel):
    """日期范围请求"""
    start_date: Optional[str] = Field(None, description="开始日期 (YYYY-MM-DD)")
    end_date: Optional[str] = Field(None, description="结束日期 (YYYY-MM-DD)")


# 从持久化文件加载Cookie配置（与store_performance和scheduler统一）
_cookie_config = load_life_data_cookie()


def get_life_data_client() -> LifeDataClient:
    """获取来客数据客户端实例"""
    if not _cookie_config["cookie"] or not _cookie_config["life_account_id"]:
        raise HTTPException(
            status_code=400,
            detail="来客后台Cookie未配置，请先调用 /cookie 接口配置"
        )

    return LifeDataClient(
        cookie=_cookie_config["cookie"],
        life_account_id=_cookie_config["life_account_id"],
        csrf_token=_cookie_config.get("csrf_token")
    )


@router.post("/cookie", summary="配置来客后台Cookie")
async def update_cookie(request: CookieUpdateRequest):
    """
    配置来客后台Cookie

    用于设置访问来客后台API所需的认证信息
    注意：此接口与 /store-performance/life-data-cookie 功能相同，
    都会持久化Cookie并同步更新调度器配置
    """
    _cookie_config["cookie"] = request.cookie
    _cookie_config["life_account_id"] = request.life_account_id
    _cookie_config["csrf_token"] = request.csrf_token

    # 持久化存储Cookie到文件（与store_performance统一）
    save_life_data_cookie(request.cookie, request.life_account_id, request.csrf_token or "")

    # 同步更新调度器的来客后台Cookie配置（与store_performance统一）
    configure_life_data_cookie(
        request.cookie,
        request.life_account_id,
        request.csrf_token or ""
    )

    return {
        "code": 0,
        "message": "Cookie配置成功（已持久化并同步调度器）",
        "data": {
            "life_account_id": request.life_account_id,
            "cookie_length": len(request.cookie)
        }
    }


@router.get("/cookie/status", summary="获取Cookie配置状态")
async def get_cookie_status():
    """
    获取当前Cookie配置状态
    """
    return {
        "code": 0,
        "message": "success",
        "data": {
            "configured": bool(_cookie_config["cookie"]),
            "life_account_id": _cookie_config.get("life_account_id", ""),
            "has_csrf_token": bool(_cookie_config.get("csrf_token"))
        }
    }


@router.get("/live", summary="获取直播数据")
async def get_live_data(
    room_type: str = Query("ALL", description="直播类型过滤 (ALL|1商家自播|2达人一带一|3达人一带多)")
):
    """
    获取直播数据

    返回直播列表，包含直播时长、成交金额等信息
    """
    client = get_life_data_client()

    try:
        result = await client.get_live_data_parsed(room_type_filter=room_type)
        return {
            "code": 0,
            "message": "success",
            "data": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.get("/poi", summary="获取门店/视频数据")
async def get_poi_data(
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)")
):
    """
    获取门店/视频数据

    返回门店列表，包含视频发布数、核销金额等信息
    """
    client = get_life_data_client()

    try:
        result = await client.get_poi_data_parsed(
            start_date=start_date,
            end_date=end_date
        )
        return {
            "code": 0,
            "message": "success",
            "data": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.get("/summary", summary="获取汇总数据")
async def get_summary_data(
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)")
):
    """
    获取汇总数据

    同时获取直播和门店数据，返回关键指标汇总：
    - 总直播时长
    - 总成交金额
    - 视频发布总数
    - 核销总金额
    """
    client = get_life_data_client()

    try:
        result = await client.get_summary_data(
            start_date=start_date,
            end_date=end_date
        )

        # 格式化时长为可读格式
        duration_seconds = result.get("live_duration_total", 0)
        hours = duration_seconds // 3600
        minutes = (duration_seconds % 3600) // 60
        seconds = duration_seconds % 60
        duration_formatted = f"{hours}小时{minutes}分钟{seconds}秒"

        # 格式化金额（分转元）
        gmv_yuan = result.get("gmv_total", 0) / 100
        verify_yuan = result.get("verify_amount_total", 0) / 100

        return {
            "code": 0,
            "message": "success",
            "data": {
                "live_duration": {
                    "total_seconds": duration_seconds,
                    "formatted": duration_formatted
                },
                "gmv": {
                    "total_fen": result.get("gmv_total", 0),
                    "total_yuan": gmv_yuan,
                    "formatted": f"¥{gmv_yuan:.2f}"
                },
                "video_count": {
                    "total": result.get("video_count_total", 0)
                },
                "verify_amount": {
                    "total_fen": result.get("verify_amount_total", 0),
                    "total_yuan": verify_yuan,
                    "formatted": f"¥{verify_yuan:.2f}"
                },
                "excel_urls": {
                    "live": result.get("live_excel_url"),
                    "poi": result.get("poi_excel_url")
                },
                "query_date_range": result.get("query_date_range")
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.get("/live/export", summary="导出直播数据Excel")
async def export_live_data(
    room_type: str = Query("ALL", description="直播类型过滤")
):
    """
    获取直播数据Excel下载链接

    返回来客后台生成的Excel文件下载URL
    """
    client = get_life_data_client()

    try:
        result = await client.export_live_data(room_type_filter=room_type)
        data_list = result.get("data", [])

        if data_list and len(data_list) > 0:
            excel_url = data_list[0].get("url")
            return {
                "code": 0,
                "message": "success",
                "data": {
                    "excel_url": excel_url,
                    "task_id": data_list[0].get("task_id")
                }
            }
        else:
            return {
                "code": -1,
                "message": "未获取到导出链接",
                "data": None
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.get("/poi/export", summary="导出门店数据Excel")
async def export_poi_data(
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)")
):
    """
    获取门店数据Excel下载链接

    返回来客后台生成的Excel文件下载URL
    """
    client = get_life_data_client()

    try:
        result = await client.export_poi_data(
            start_date=start_date,
            end_date=end_date
        )
        data_list = result.get("data", [])

        if data_list and len(data_list) > 0:
            excel_url = data_list[0].get("url")
            return {
                "code": 0,
                "message": "success",
                "data": {
                    "excel_url": excel_url,
                    "task_id": data_list[0].get("task_id")
                }
             }
        else:
            return {
                "code": -1,
                "message": "未获取到导出链接",
                "data": None
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/live/parse-excel", summary="下载并解析直播Excel数据")
async def parse_live_excel(
    excel_url: str = Body(..., embed=True, description="Excel下载URL")
):
    """
    下载并解析直播Excel数据

    从给定的URL下载Excel文件并解析为JSON格式
    """
    client = get_life_data_client()

    try:
        excel_bytes = await client.download_excel(excel_url)
        data = parse_excel_to_dict(excel_bytes)

        return {
            "code": 0,
            "message": "success",
            "data": {
                "total": len(data),
                "records": data
            }
        }
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail="服务器未安装openpyxl库，无法解析Excel"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()
