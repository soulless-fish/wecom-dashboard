from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging
import os
import sys
from pathlib import Path
from datetime import date
from app.config import get_settings
from app.routers import wecom, jssdk, callback, douyin, life_data, debug, store_performance, ai_context, fanke
from app.database.models import init_db, SessionLocal, StorePerformance
from app.services.cookie_storage import load_cookie
from app.services.scheduler import (
    start_scheduler, configure_scheduler_cookie, sync_current_month_data
)

settings = get_settings()
logger = logging.getLogger(__name__)

# 巨量引擎相关代码按项目要求放在仓库根目录 JLYQ 中，这里只做路由挂载。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from JLYQ.oauth_router import router as jlyq_router
from JLYQ.local_promotion_router import router as jlyq_local_promotion_router

# 环境变量控制是否启动调度器（解决多worker重复启动问题）
# 多进程部署时，只有设置 ENABLE_SCHEDULER=true 的worker才启动调度器
# 公开版本默认关闭定时任务，生产部署时再显式开启。
ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "false").lower() == "true"


async def _check_and_sync_data():
    """
    启动后检测数据是否更新到昨天，如果没有就立即同步。
    确保不管服务什么时候重启，数据都能补全到最新。
    """
    # 等待几秒确保服务完全启动
    await asyncio.sleep(3)

    cookie_config = load_cookie()
    if not cookie_config["cookie"] or not cookie_config["account_id"]:
        logger.info("启动检测: Cookie未配置，跳过数据检测")
        return

    today = date.today()
    yesterday = today.day - 1 if today.day > 1 else None

    # 如果今天是1号，昨天属于上个月，暂不处理跨月
    if yesterday is None:
        logger.info("启动检测: 今天是1号，跳过数据检测")
        return

    current_month = today.strftime("%Y-%m")

    try:
        db = SessionLocal()
        # 查询当前月份最新的 data_end_day
        latest = db.query(StorePerformance.data_end_day).filter(
            StorePerformance.data_month == current_month
        ).order_by(StorePerformance.data_end_day.desc()).first()
        db.close()

        latest_end_day = latest[0] if latest else 0

        if latest_end_day < yesterday:
            logger.info(
                f"启动检测: 数据未更新到最新 (当前end_day={latest_end_day}, 需要={yesterday})，"
                f"立即触发同步"
            )
            await sync_current_month_data()
        else:
            logger.info(f"启动检测: 数据已是最新 (end_day={latest_end_day})")
    except Exception as e:
        logger.error(f"启动检测数据失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化数据库
    try:
        init_db()
    except Exception:
        logging.getLogger(__name__).exception("DB init failed; continuing without DB")

    # 自动加载Cookie并启动定时任务（仅在ENABLE_SCHEDULER=true时）
    cookie_config = load_cookie()
    if cookie_config["cookie"]:
        logger.info("启动: 从文件加载Cookie配置成功")
        configure_scheduler_cookie(
            cookie_config["cookie"],
            cookie_config["account_id"],
            cookie_config.get("csrf_token", "")
        )
        # 同步更新store_performance的内存配置
        store_performance._gmv_cookie_config.update(cookie_config)

        # 只有ENABLE_SCHEDULER=true时才启动调度器（解决多worker问题）
        if ENABLE_SCHEDULER:
            logger.info("启动: ENABLE_SCHEDULER=true，启动定时任务调度器")
            start_scheduler()

            # 在后台检测数据并同步（不阻塞启动）
            asyncio.create_task(_check_and_sync_data())
        else:
            logger.info("启动: ENABLE_SCHEDULER=false，跳过调度器启动（多worker模式）")
    else:
        logger.info("启动: Cookie未配置，定时任务未启动")

    yield
    # 关闭时的清理工作


app = FastAPI(
    title=settings.app_name,
    description="企业微信侧边栏应用后端服务",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# CORS中间件
allow_credentials = "*" not in settings.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(wecom.router, prefix=settings.api_prefix, tags=["企业微信"])
app.include_router(jssdk.router, prefix=settings.api_prefix, tags=["JS-SDK"])
app.include_router(callback.router, tags=["回调接口"])  # 回调接口不加前缀
app.include_router(douyin.router, prefix=f"{settings.api_prefix}/douyin", tags=["抖音来客"])
app.include_router(life_data.router, prefix=f"{settings.api_prefix}/life-data", tags=["来客后台数据"])
app.include_router(store_performance.router, prefix=f"{settings.api_prefix}/store-performance", tags=["门店业绩数据"])
app.include_router(ai_context.router, prefix=f"{settings.api_prefix}/ai", tags=["AI门店数据接口"])
app.include_router(fanke.router, prefix=f"{settings.api_prefix}/fanke", tags=["凡科商城接口"])
app.include_router(jlyq_router, prefix=f"{settings.api_prefix}/jlyq", tags=["巨量引擎接口"])
app.include_router(jlyq_local_promotion_router, prefix=f"{settings.api_prefix}/jlyq", tags=["巨量引擎本地推接口"])
if settings.debug:
    app.include_router(debug.router, prefix=f"{settings.api_prefix}/debug", tags=["API调试"])


@app.get("/")
async def root():
    """健康检查"""
    return {"status": "ok", "app": settings.app_name}


@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "healthy"}
