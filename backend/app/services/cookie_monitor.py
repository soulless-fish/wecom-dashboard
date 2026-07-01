"""
Cookie时效监控服务

用于检测抖音来客Cookie是否有效，失效时通过企业微信应用消息提醒管理员

创建日期: 2026-01-21
"""

import httpx
import logging
from datetime import datetime
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


async def check_cookie_validity(client, start_day: int, end_day: int) -> bool:
    """
    检查Cookie是否有效

    Args:
        client: GmvDataClient实例
        start_day: 开始日期 (YYYYMMDD)
        end_day: 结束日期 (YYYYMMDD)

    Returns:
        True表示Cookie有效，False表示失效
    """
    try:
        result = await client.fetch_page(start_day, end_day, page=1, page_size=1)
        if result.get("status_code") != 0:
            logger.error(f"Cookie验证失败: {result.get('status_msg', '未知错误')}")
            await send_cookie_expired_notification()
            return False
        return True
    except Exception as e:
        logger.error(f"Cookie验证异常: {e}")
        await send_cookie_expired_notification()
        return False


async def send_cookie_expired_notification():
    """
    发送Cookie失效通知
    通过企业微信应用消息发送给管理员
    """
    from app.services.token_manager import TokenManager

    settings = get_settings()

    if not settings.cookie_alert_userid:
        logger.warning("未配置Cookie告警接收人(cookie_alert_userid)，跳过发送通知")
        return

    if not settings.wecom_corp_id or not settings.wecom_secret:
        logger.warning("企业微信配置不完整，无法发送Cookie失效通知")
        return

    try:
        token_manager = TokenManager(settings)
        access_token = await token_manager.get_access_token()

        # 发送应用消息
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        payload = {
            "touser": settings.cookie_alert_userid,
            "msgtype": "text",
            "agentid": int(settings.wecom_agent_id) if settings.wecom_agent_id else 0,
            "text": {
                "content": f"【极修匠系统告警】\n\n抖音来客Cookie已失效！\n\n告警时间: {current_time}\n\n请及时登录抖音来客后台重新获取Cookie并配置到系统中，否则将无法同步门店业绩数据。"
            }
        }

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            response = await http_client.post(url, json=payload)
            result = response.json()

            if result.get("errcode") == 0:
                logger.info(f"Cookie失效通知已发送给: {settings.cookie_alert_userid}")
            else:
                logger.error(f"发送Cookie失效通知失败: {result.get('errmsg', '未知错误')}")

    except Exception as e:
        logger.error(f"发送Cookie失效通知异常: {e}")


async def manual_check_and_notify() -> dict:
    """
    手动检查Cookie状态并返回结果

    Returns:
        包含检查结果的字典
    """
    from app.services.gmv_data_client import GmvDataClient
    from app.services.scheduler import _scheduler_cookie_config

    if not _scheduler_cookie_config["cookie"] or not _scheduler_cookie_config["account_id"]:
        return {
            "valid": False,
            "message": "Cookie未配置"
        }

    try:
        client = GmvDataClient(
            cookie=_scheduler_cookie_config["cookie"],
            account_id=_scheduler_cookie_config["account_id"],
            csrf_token=_scheduler_cookie_config.get("csrf_token")
        )

        start_day, end_day, *_ = GmvDataClient.get_realtime_range()
        result = await client.fetch_page(start_day, end_day, page=1, page_size=1)

        await client.close()

        if result.get("status_code") == 0:
            return {
                "valid": True,
                "message": "Cookie有效"
            }
        else:
            await send_cookie_expired_notification()
            return {
                "valid": False,
                "message": f"Cookie无效: {result.get('status_msg', '未知错误')}"
            }

    except Exception as e:
        await send_cookie_expired_notification()
        return {
            "valid": False,
            "message": f"检查异常: {str(e)}"
        }
