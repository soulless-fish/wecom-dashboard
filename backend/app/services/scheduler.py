"""
定时任务调度器

实现每天上午11点自动更新门店业绩数据的功能
数据范围：当月1号到昨天

创建日期: 2026-01-15
更新日期: 2026-01-21 - 改为每天8点执行，获取1号到昨天的数据
更新日期: 2026-01-24 - 改为每天9点执行，添加失败重试机制
更新日期: 2026-01-24 - 改为每天11点执行（抖音来客每天11点才能导出最新数据）
更新日期: 2026-01-31 - 添加门店概览数据同步（video_cnt_1d, poi_score）
"""

import asyncio
import logging
import random
from datetime import datetime, date, timedelta
from typing import Optional
from decimal import Decimal
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from app.database.models import SessionLocal, DataSyncLog, StorePerformance, format_duration
from app.services.gmv_data_client import GmvDataClient
from app.services.cookie_storage import load_cookie, load_life_data_cookie

logger = logging.getLogger(__name__)

# 重试配置
RETRY_DELAY_MIN_SECONDS = 180  # 重试间隔最小值（3分钟）
RETRY_DELAY_MAX_SECONDS = 300  # 重试间隔最大值（5分钟）

# 全局调度器实例
scheduler: Optional[AsyncIOScheduler] = None

# Cookie配置（启动时从持久化文件加载）
_scheduler_cookie_config = load_cookie()
_life_data_cookie_config = load_life_data_cookie()


def configure_scheduler_cookie(cookie: str, account_id: str, csrf_token: str = ""):
    """配置调度器使用的Cookie（上翻收益）"""
    _scheduler_cookie_config["cookie"] = cookie
    _scheduler_cookie_config["account_id"] = account_id
    _scheduler_cookie_config["csrf_token"] = csrf_token


def configure_life_data_cookie(cookie: str, life_account_id: str, csrf_token: str = ""):
    """配置调度器使用的来客后台Cookie"""
    _life_data_cookie_config["cookie"] = cookie
    _life_data_cookie_config["life_account_id"] = life_account_id
    _life_data_cookie_config["csrf_token"] = csrf_token


async def _do_sync_once():
    """
    执行一次同步操作（内部函数，带事务保护）
    返回: (success: bool, error_msg: str)
    """
    from app.services.cookie_monitor import check_cookie_validity

    if not _scheduler_cookie_config["cookie"] or not _scheduler_cookie_config["account_id"]:
        return False, "Cookie未配置"

    # 获取实时数据范围（1号到昨天）
    (
        start_day, end_day, start_date_str, end_date_str,
        start_day_num, end_day_num
    ) = GmvDataClient.get_realtime_range()

    # 从日期范围获取月份
    data_month = start_date_str[:7]  # YYYY-MM

    db = SessionLocal()
    client: Optional[GmvDataClient] = None
    sync_log_id = None

    try:
        # 创建同步日志（独立事务，确保日志一定能创建）
        sync_log = DataSyncLog(
            sync_type="scheduled_sync",
            data_month=data_month,
            status="running",
            started_at=datetime.now()
        )
        db.add(sync_log)
        db.commit()
        db.refresh(sync_log)
        sync_log_id = sync_log.id

        # 创建客户端
        client = GmvDataClient(
            cookie=_scheduler_cookie_config["cookie"],
            account_id=_scheduler_cookie_config["account_id"],
            csrf_token=_scheduler_cookie_config.get("csrf_token")
        )

        # 先检查Cookie是否有效
        try:
            test_result = await client.fetch_page(start_day, end_day, page=1, page_size=1)
            if test_result.get("status_code") != 0:
                await check_cookie_validity(client, start_day, end_day)
                raise Exception(f"API返回错误: {test_result.get('status_msg', '未知错误')}")
        except Exception as e:
            await check_cookie_validity(client, start_day, end_day)
            raise

        # 获取上翻收益数据
        data_list = await client.fetch_all_data(start_day, end_day)

        # 获取门店概览、当月实时核销、近30天核销数据。
        # 共享同一个 LifeDataClient，让当月概览和当月核销复用同一份Excel导出缓存。
        from app.services.life_data_client import LifeDataClient
        shared_life_client = LifeDataClient(
            cookie=_life_data_cookie_config["cookie"],
            life_account_id=_life_data_cookie_config["life_account_id"],
            csrf_token=_life_data_cookie_config.get("csrf_token")
        )
        try:
            # 获取门店概览数据（video_cnt_1d, poi_score）
            poi_overview = await _fetch_poi_overview(start_date_str, end_date_str, shared_client=shared_life_client)
            if data_list and not poi_overview:
                raise RuntimeError("门店概览数据为空，已中止同步，避免写入错误零值")
            logger.info(f"门店概览数据获取成功，共 {len(poi_overview)} 个门店")
            poi_overview_by_name = {}  # 用于 poi_id 匹配失败时的备选（按 poi_name 索引）
            for pid, data in poi_overview.items():
                name = data.get("poi_name", "")
                if name:
                    poi_overview_by_name[name] = data

            # 获取门店核销数据（当月实时: verify_amount_realtime, verify_cert_cnt_realtime）
            poi_verify_realtime = await _fetch_poi_verify(
                start_date_str,
                end_date_str,
                shared_client=shared_life_client,
                range_type="realtime"
            )
            if data_list and not poi_verify_realtime:
                raise RuntimeError("门店实时核销数据为空，已中止同步，避免写入错误零值")
            logger.info(f"门店实时核销数据获取成功，共 {len(poi_verify_realtime)} 个门店")
            poi_verify_realtime_by_name = {}
            for pid, data in poi_verify_realtime.items():
                name = data.get("poi_name", "")
                if name:
                    poi_verify_realtime_by_name[name] = data

            # 获取门店核销数据（近30天: verify_amount, verify_cert_cnt）
            poi_verify = await _fetch_poi_verify(
                start_date_str,
                end_date_str,
                shared_client=shared_life_client,
                range_type="last_thirty_days"
            )
            if data_list and not poi_verify:
                raise RuntimeError("门店近30天核销数据为空，已中止同步，避免写入错误零值")
            logger.info(f"门店近30天核销数据获取成功，共 {len(poi_verify)} 个门店")
            poi_verify_by_name = {}
            for pid, data in poi_verify.items():
                name = data.get("poi_name", "")
                if name:
                    poi_verify_by_name[name] = data
        finally:
            await shared_life_client.close()

        # 事务保护：删除+插入在同一事务中
        # 清除该月份的旧数据
        db.query(StorePerformance).filter(
            StorePerformance.data_month == data_month
        ).delete()

        # 插入新数据
        for store in data_list:
            live_duration_seconds = GmvDataClient._parse_duration(
                store.get("live_duration")
            )
            live_duration_formatted = format_duration(live_duration_seconds)

            gmv_yuan = Decimal(str(store.get("write_off_order_gmv", 0)))

            video_count = GmvDataClient._safe_int(store.get("video_count"))

            poi_id = str(store.get("poi_id", ""))
            poi_name = store.get("poi_name", "")

            # 合并门店概览数据（优先用 poi_id 匹配，失败时用 poi_name 匹配）
            overview = poi_overview.get(poi_id, {})
            if not overview and poi_name:
                overview = poi_overview_by_name.get(poi_name, {})
            video_cnt_1d = overview.get("video_cnt_1d", 0)
            poi_score = Decimal(str(overview.get("poi_score", 0)))

            # 合并门店核销数据（优先用 poi_id 匹配，失败时用 poi_name 匹配）
            verify_realtime = poi_verify_realtime.get(poi_id, {})
            if not verify_realtime and poi_name:
                verify_realtime = poi_verify_realtime_by_name.get(poi_name, {})
            verify_amount_realtime = verify_realtime.get("verify_amount", "¥0.00")
            verify_cert_cnt_realtime = verify_realtime.get("verify_cert_cnt", 0)

            verify = poi_verify.get(poi_id, {})
            if not verify and poi_name:
                verify = poi_verify_by_name.get(poi_name, {})
            verify_amount = verify.get("verify_amount", "¥0.00")
            verify_cert_cnt = verify.get("verify_cert_cnt", 0)

            record = StorePerformance(
                poi_id=poi_id,
                poi_name=store.get("poi_name", ""),
                gmv_yuan=gmv_yuan,
                live_duration_formatted=live_duration_formatted,
                live_duration_seconds=live_duration_seconds,
                video_count=video_count,
                video_cnt_1d=video_cnt_1d,
                poi_score=poi_score,
                verify_amount_realtime=verify_amount_realtime,
                verify_cert_cnt_realtime=verify_cert_cnt_realtime,
                verify_amount=verify_amount,
                verify_cert_cnt=verify_cert_cnt,
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
            sync_log.total_records = len(data_list)
            sync_log.finished_at = datetime.now()
            db.commit()

        logger.info(
            f"定时任务: {data_month} ({start_day_num}~{end_day_num}号) 同步完成，共 {len(data_list)} 条记录，"
            f"门店概览匹配 {len(poi_overview)} 条，实时核销匹配 {len(poi_verify_realtime)} 条，"
            f"近30天核销匹配 {len(poi_verify)} 条"
        )
        return True, ""

    except Exception as e:
        error_msg = str(e)
        logger.error(f"定时任务: 同步失败 - {error_msg}")

        # 异常时先回滚事务，防止删除操作被持久化
        try:
            db.rollback()
        except Exception as rb_err:
            logger.error(f"回滚事务失败: {rb_err}")

        # 使用独立session写失败日志，避免影响主事务
        if sync_log_id:
            log_db = SessionLocal()
            try:
                sync_log = log_db.query(DataSyncLog).filter(DataSyncLog.id == sync_log_id).first()
                if sync_log:
                    sync_log.status = "failed"
                    sync_log.error_message = error_msg
                    sync_log.finished_at = datetime.now()
                    log_db.commit()
            except Exception as log_err:
                logger.error(f"写入失败日志失败: {log_err}")
            finally:
                log_db.close()

        return False, error_msg
    finally:
        if client is not None:
            await client.close()
        db.close()


async def _fetch_poi_overview(start_date_str: str, end_date_str: str, shared_client=None) -> dict:
    """
    获取门店概览数据的辅助函数

    Args:
        start_date_str: 开始日期 (YYYY-MM-DD)
        end_date_str: 结束日期 (YYYY-MM-DD)
        shared_client: 可选的共享LifeDataClient实例（避免重复创建和分页请求）

    Returns:
        {poi_id: {"video_cnt_1d": int, "poi_score": float}, ...}
    """
    if not _life_data_cookie_config.get("cookie") or not _life_data_cookie_config.get("life_account_id"):
        raise RuntimeError("来客后台Cookie未配置，无法同步门店概览数据")

    # 方案1: 优先使用直接API方案
    api_error: Optional[Exception] = None
    try:
        from app.services.life_data_client import LifeDataClient

        logger.info("使用API方案获取门店概览数据...")
        if shared_client:
            life_client = shared_client
        else:
            life_client = LifeDataClient(
                cookie=_life_data_cookie_config["cookie"],
                life_account_id=_life_data_cookie_config["life_account_id"],
                csrf_token=_life_data_cookie_config.get("csrf_token")
            )

        try:
            result = await life_client.fetch_poi_overview_data(
                start_date=start_date_str,
                end_date=end_date_str
            )
            logger.info(f"API方案成功，获取到 {len(result)} 个门店数据")
            return result
        finally:
            if not shared_client:
                await life_client.close()
    except Exception as e:
        api_error = e
        logger.warning(f"API方案失败: {e}，尝试Playwright方案...")

    # 方案2: 回退到Playwright无头浏览器（可绕过反爬机制）
    playwright_error: Optional[Exception] = None
    try:
        from app.services.life_data_playwright import LifeDataPlaywright, PLAYWRIGHT_AVAILABLE

        if PLAYWRIGHT_AVAILABLE:
            logger.info("使用Playwright方案获取门店概览数据...")
            playwright_client = LifeDataPlaywright(
                cookie_string=_life_data_cookie_config["cookie"],
                life_account_id=_life_data_cookie_config["life_account_id"],
                headless=True
            )
            try:
                result = await playwright_client.fetch_poi_overview_via_api(
                    start_date=start_date_str,
                    end_date=end_date_str
                )
                logger.info(f"Playwright方案成功，获取到 {len(result)} 个门店数据")
                return result
            finally:
                await playwright_client.close()
        else:
            playwright_error = RuntimeError("Playwright未安装")
            logger.warning("Playwright未安装")
    except ImportError:
        playwright_error = RuntimeError("Playwright模块导入失败")
        logger.warning("Playwright模块导入失败")
    except Exception as e:
        playwright_error = e
        logger.error(f"Playwright方案也失败: {e}")

    error_messages = []
    if api_error:
        error_messages.append(f"API方案失败: {api_error}")
    if playwright_error:
        error_messages.append(f"Playwright方案失败: {playwright_error}")
    raise RuntimeError("门店概览数据获取失败; " + "；".join(error_messages))


async def _fetch_poi_verify(
    start_date_str: str,
    end_date_str: str,
    shared_client=None,
    *,
    range_type: str = "last_thirty_days"
) -> dict:
    """
    获取门店核销数据的辅助函数

    Args:
        start_date_str: 开始日期 (YYYY-MM-DD)
        end_date_str: 结束日期 (YYYY-MM-DD)
        shared_client: 可选的共享LifeDataClient实例
        range_type: "realtime" 表示当月实时范围；"last_thirty_days" 表示近30天

    Returns:
        {poi_id: {"verify_amount": "¥1,785.20", "verify_cert_cnt": 30, "poi_name": "..."}, ...}
    """
    if not _life_data_cookie_config.get("cookie") or not _life_data_cookie_config.get("life_account_id"):
        raise RuntimeError("来客后台Cookie未配置，无法同步门店核销数据")

    try:
        from app.services.life_data_client import LifeDataClient

        if range_type == "realtime":
            date_type = "custom"
            request_start_date = start_date_str
            request_end_date = end_date_str
            log_label = "实时核销数据"
        else:
            date_type = "last_thirty_days"
            request_start_date = None
            request_end_date = None
            log_label = "近30天核销数据"

        logger.info(f"使用API方案获取门店{log_label}...")
        if shared_client:
            life_client = shared_client
        else:
            life_client = LifeDataClient(
                cookie=_life_data_cookie_config["cookie"],
                life_account_id=_life_data_cookie_config["life_account_id"],
                csrf_token=_life_data_cookie_config.get("csrf_token")
            )

        try:
            result = await life_client.fetch_poi_verify_data(
                start_date=request_start_date,
                end_date=request_end_date,
                date_type=date_type,
                log_label=log_label
            )
            logger.info(f"门店{log_label}获取成功，共 {len(result)} 个门店")
            return result
        finally:
            if not shared_client:
                await life_client.close()
    except Exception as e:
        logger.error(f"获取门店核销数据失败({range_type}): {e}")
        raise RuntimeError(f"门店核销数据获取失败({range_type}): {e}") from e


async def sync_current_month_data():
    """
    同步当前月份的数据（1号到昨天）

    这是定时任务执行的函数，带有自动重试机制：
    - 最多重试5次（防止Cookie失效时无限循环）
    - 每次重试间隔随机3~5分钟
    """
    if not _scheduler_cookie_config["cookie"] or not _scheduler_cookie_config["account_id"]:
        logger.warning("定时任务: Cookie未配置，跳过同步")
        return

    # 获取日期范围用于日志
    (
        start_day, end_day, start_date_str, end_date_str,
        start_day_num, end_day_num
    ) = GmvDataClient.get_realtime_range()
    data_month = start_date_str[:7]

    logger.info(f"定时任务: 开始同步 {data_month} 数据 ({start_day_num}号~{end_day_num}号)")

    max_attempts = 5
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        logger.info(f"定时任务: 第 {attempt}/{max_attempts} 次尝试同步")

        success, error_msg = await _do_sync_once()

        if success:
            logger.info(f"定时任务: 同步成功（第 {attempt} 次尝试）")
            return

        if attempt >= max_attempts:
            logger.error(f"定时任务: 已达最大重试次数({max_attempts})，停止重试。最后错误: {error_msg}")
            return

        # 生成随机重试间隔（3~5分钟）
        retry_delay = random.randint(RETRY_DELAY_MIN_SECONDS, RETRY_DELAY_MAX_SECONDS)
        logger.warning(f"定时任务: 第 {attempt} 次尝试失败 ({error_msg})，{retry_delay}秒后重试...")
        await asyncio.sleep(retry_delay)


async def sync_fanke_recent_order_data():
    """
    同步凡科近30天订单数据

    该任务独立于抖音/来客同步任务，失败时只影响凡科侧边栏字段。
    """
    try:
        from app.services.fanke_order_sync import sync_fanke_recent_orders

        logger.info("凡科订单定时任务: 开始同步近30天订单")
        result = await sync_fanke_recent_orders(days=30)
        logger.info(
            "凡科订单定时任务: 同步成功，订单%s条，明细%s条",
            result.get("order_count"),
            result.get("item_count"),
        )
    except Exception as exc:
        logger.error("凡科订单定时任务: 同步失败 - %s", exc)


async def sync_douyin_official_recent_order_data():
    """
    同步抖音官方近期开单数据

    该任务按小时窗口从最新订单往前补，避免一次性拉取近30天大批量订单影响线上服务。
    """
    try:
        from app.services.douyin_order_sync import sync_douyin_official_orders

        logger.info("抖音官方订单定时任务: 开始同步最近48小时订单")
        result = await sync_douyin_official_orders(
            hours=48,
            max_pages=200,
            page_size=200,
            window_hours=1,
        )
        logger.info(
            "抖音官方订单定时任务: 同步完成，页%s，订单%s条，写入%s条",
            result.get("total_pages"),
            result.get("total_orders"),
            result.get("saved_rows"),
        )
    except Exception as exc:
        logger.error("抖音官方订单定时任务: 同步失败 - %s", exc)


async def sync_douyin_computer_cleaning_data():
    """
    同步抖音电脑清灰团购开通状态

    该任务每天跟随抖音来客门店数据更新节奏执行，用于刷新侧边栏“电脑清灰：已开通/未开通”字段。
    """
    try:
        from app.services.douyin_computer_cleaning_sync import sync_douyin_computer_cleaning_status

        logger.info("抖音电脑清灰定时任务: 开始同步团购商品开通状态")
        result = await sync_douyin_computer_cleaning_status()
        logger.info(
            "抖音电脑清灰定时任务: 同步完成，在线目标商品%s个，开通门店%s个，更新状态%s条",
            result.get("target_product_count"),
            result.get("opened_poi_count"),
            result.get("updated_status_count"),
        )
    except Exception as exc:
        logger.error("抖音电脑清灰定时任务: 同步失败 - %s", exc)


def start_scheduler():
    """启动定时任务调度器"""
    global scheduler

    if scheduler is not None:
        logger.warning("调度器已经在运行")
        return

    scheduler = AsyncIOScheduler()

    # 每天上午11:20执行（抖音来客每天11点更新，延迟20分钟避免数据不全或被检测）
    scheduler.add_job(
        sync_current_month_data,
        trigger=CronTrigger(hour=11, minute=20),
        id="sync_store_performance",
        name="同步门店业绩数据",
        replace_existing=True
    )

    # 每天上午11:50执行凡科订单同步，和抖音/来客同步错开。
    scheduler.add_job(
        sync_fanke_recent_order_data,
        trigger=CronTrigger(hour=11, minute=50),
        id="sync_fanke_recent_orders",
        name="同步凡科近30天订单",
        replace_existing=True
    )

    # 每天中午12:20执行抖音官方订单增量同步，和已有来客/凡科任务错开。
    scheduler.add_job(
        sync_douyin_official_recent_order_data,
        trigger=CronTrigger(hour=12, minute=20),
        id="sync_douyin_official_recent_orders",
        name="同步抖音官方近期订单",
        replace_existing=True
    )

    # 每天上午11:20刷新一次电脑清灰团购状态，和抖音来客门店数据更新任务保持同一时间。
    scheduler.add_job(
        sync_douyin_computer_cleaning_data,
        trigger=CronTrigger(hour=11, minute=20),
        id="sync_douyin_computer_cleaning_status",
        name="同步电脑清灰团购状态",
        replace_existing=True
    )

    scheduler.start()
    logger.info("定时任务调度器已启动 (抖音/来客每天11:20，电脑清灰每天11:20，凡科订单每天11:50，抖音官方订单每天12:20)")


def stop_scheduler():
    """停止定时任务调度器"""
    global scheduler

    if scheduler is not None:
        scheduler.shutdown()
        scheduler = None
        logger.info("定时任务调度器已停止")


def get_scheduler_status():
    """获取调度器状态"""
    if scheduler is None:
        return {"running": False, "jobs": []}

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None
        })

    return {
        "running": scheduler.running,
        "jobs": jobs
    }
