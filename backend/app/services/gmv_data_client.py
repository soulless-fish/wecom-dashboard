"""
上翻收益数据客户端

用于获取抖音来客后台(life.douyin.com)的上翻收益数据
支持自然月日期范围查询

创建日期: 2026-01-15
"""

import httpx
import logging
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, date
from calendar import monthrange

logger = logging.getLogger(__name__)


class GmvDataClient:
    """
    上翻收益数据客户端

    获取门店的上翻收益、直播时长、视频数量等数据
    """

    # API基础URL
    BASE_URL = "https://life.douyin.com"

    # 上翻收益API
    GMV_API = "/life/homed_view/gmv_data/store_operating_list"

    def __init__(
        self,
        cookie: str,
        account_id: str,
        csrf_token: Optional[str] = None
    ):
        """
        初始化客户端

        Args:
            cookie: life.douyin.com的Cookie字符串
            account_id: 商户账户ID (root_life_account_id)
            csrf_token: x-secsdk-csrf-token
        """
        self.cookie = cookie
        self.account_id = account_id
        self.csrf_token = csrf_token
        self._client: Optional[httpx.AsyncClient] = None

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "content-type": "application/json",
            "cookie": self.cookie,
            "origin": self.BASE_URL,
            "referer": f"{self.BASE_URL}/p/liteapp/ls_homed/merchant-operation/operation-data",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "ac-tag": "ka_10h",
            "agw-js-conv": "str",
            "rpc-persist-life-merchant-role": "473489608",
            "rpc-persist-life-merchant-switch-role": "1",
        }

        if self.csrf_token:
            headers["x-secsdk-csrf-token"] = self.csrf_token

        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """获取HTTP客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0),
                follow_redirects=True
            )
        return self._client

    async def close(self):
        """关闭HTTP客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def get_month_range(year: int, month: int) -> tuple:
        """
        获取指定月份的日期范围

        Args:
            year: 年份 (如2025)
            month: 月份 (1-12)

        Returns:
            (start_day, end_day) 格式为YYYYMMDD的整数
        """
        first_day = date(year, month, 1)
        last_day_num = monthrange(year, month)[1]
        last_day = date(year, month, last_day_num)

        start_day = int(first_day.strftime("%Y%m%d"))
        end_day = int(last_day.strftime("%Y%m%d"))

        return start_day, end_day

    @staticmethod
    def get_current_month_range() -> tuple:
        """获取当前月份的日期范围"""
        today = date.today()
        return GmvDataClient.get_month_range(today.year, today.month)

    @staticmethod
    def get_last_month_range() -> tuple:
        """获取上个月的日期范围"""
        today = date.today()
        if today.month == 1:
            return GmvDataClient.get_month_range(today.year - 1, 12)
        else:
            return GmvDataClient.get_month_range(today.year, today.month - 1)

    @staticmethod
    def get_realtime_range() -> tuple:
        """
        获取实时数据日期范围：当月1号到昨天

        Returns:
            (start_day, end_day, start_date_str, end_date_str, start_day_num, end_day_num)
            start_day, end_day: YYYYMMDD格式的整数
            start_date_str, end_date_str: YYYY-MM-DD格式的字符串
            start_day_num, end_day_num: 日期中的日部分（1-31）
        """
        from datetime import timedelta

        today = date.today()
        yesterday = today - timedelta(days=1)

        # 如果是1号，返回上个月的数据
        if today.day == 1:
            if today.month == 1:
                year, month = today.year - 1, 12
            else:
                year, month = today.year, today.month - 1
            first_day = date(year, month, 1)
            last_day_num = monthrange(year, month)[1]
            last_day = date(year, month, last_day_num)
            start_day = int(first_day.strftime("%Y%m%d"))
            end_day = int(last_day.strftime("%Y%m%d"))
            return (
                start_day,
                end_day,
                first_day.strftime("%Y-%m-%d"),
                last_day.strftime("%Y-%m-%d"),
                1,
                last_day_num
            )
        else:
            first_day = date(today.year, today.month, 1)
            start_day = int(first_day.strftime("%Y%m%d"))
            end_day = int(yesterday.strftime("%Y%m%d"))
            return (
                start_day,
                end_day,
                first_day.strftime("%Y-%m-%d"),
                yesterday.strftime("%Y-%m-%d"),
                1,
                yesterday.day
            )

    async def fetch_page(
        self,
        start_day: int,
        end_day: int,
        page: int = 1,
        page_size: int = 50
    ) -> Dict[str, Any]:
        """
        获取单页数据

        Args:
            start_day: 开始日期 (YYYYMMDD)
            end_day: 结束日期 (YYYYMMDD)
            page: 页码
            page_size: 每页数量 (最大50)

        Returns:
            API响应数据
        """
        url = f"{self.BASE_URL}{self.GMV_API}?root_life_account_id={self.account_id}"

        payload = {
            "rank_params": {
                "gmv_params": {
                    "start_day": start_day,
                    "end_day": end_day,
                    "yesterday": end_day,
                    "from_snapshot": False
                },
                "page": page,
                "page_size": page_size,
                "sort_rules": ["write_off_order_gmv desc"]
            },
            "export": False,
            "exportColumns": [
                "poi_id", "poi_name", "write_off_order_gmv",
                "nickname", "live_duration", "video_count",
                "return_write_off_order_count"
            ],
            "is_user_poi_filter": False,
            "permission_common_param": {}
        }

        client = await self._get_client()

        try:
            response = await client.post(url, json=payload, headers=self._get_headers())
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP错误: {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"请求失败: {e}")
            raise

    async def fetch_all_data(
        self,
        start_day: int,
        end_day: int,
        on_progress: Optional[callable] = None
    ) -> List[Dict[str, Any]]:
        """
        分页获取所有数据

        Args:
            start_day: 开始日期 (YYYYMMDD)
            end_day: 结束日期 (YYYYMMDD)
            on_progress: 进度回调函数 (current, total)

        Returns:
            所有门店数据列表
        """
        all_data = []
        page = 1
        page_size = 50

        while True:
            result = await self.fetch_page(start_day, end_day, page, page_size)

            if result.get("status_code") != 0:
                error_msg = result.get("status_msg", "未知错误")
                logger.error(f"API错误: {error_msg}")
                raise Exception(f"API返回错误: {error_msg}")

            data_list = result.get("data", {}).get("data", [])

            if not data_list:
                break

            all_data.extend(data_list)

            if on_progress:
                on_progress(len(all_data), None)

            if len(data_list) < page_size:
                break

            page += 1
            await asyncio.sleep(0.3)  # 避免请求过快

        logger.info(f"获取完成，共 {len(all_data)} 条数据")
        return all_data

    async def fetch_month_data(
        self,
        year: int,
        month: int,
        on_progress: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        获取指定月份的数据

        Args:
            year: 年份
            month: 月份
            on_progress: 进度回调函数

        Returns:
            包含数据列表和汇总信息的字典
        """
        start_day, end_day = self.get_month_range(year, month)

        logger.info(f"开始获取 {year}年{month}月 数据 ({start_day} ~ {end_day})")

        data_list = await self.fetch_all_data(start_day, end_day, on_progress)

        # 计算汇总
        total_gmv = 0
        total_live_duration = 0
        total_video_count = 0

        for item in data_list:
            total_gmv += self._safe_int(item.get("write_off_order_gmv"))
            total_live_duration += self._parse_duration(item.get("live_duration"))
            total_video_count += self._safe_int(item.get("video_count"))

        return {
            "year": year,
            "month": month,
            "data_range": f"{start_day} ~ {end_day}",
            "total_stores": len(data_list),
            "summary": {
                "total_gmv_yuan": total_gmv / 100,
                "total_live_duration_seconds": total_live_duration,
                "total_video_count": total_video_count
            },
            "stores": data_list
        }

    @staticmethod
    def _safe_int(val) -> int:
        """安全转换为整数"""
        if val is None:
            return 0
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            try:
                return int(val)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _safe_float(val) -> float:
        """安全转换为浮点数"""
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _parse_duration(dur) -> int:
        """解析直播时长为秒数"""
        if dur is None:
            return 0
        if isinstance(dur, (int, float)):
            return int(dur)
        if isinstance(dur, str):
            try:
                return int(dur)
            except ValueError:
                pass

            # 解析中文格式
            import re
            total = 0

            hour_match = re.search(r'(\d+)\s*小时', dur)
            if hour_match:
                total += int(hour_match.group(1)) * 3600

            min_match = re.search(r'(\d+)\s*分', dur)
            if min_match:
                total += int(min_match.group(1)) * 60

            sec_match = re.search(r'(\d+)\s*秒', dur)
            if sec_match:
                total += int(sec_match.group(1))

            return total
        return 0
