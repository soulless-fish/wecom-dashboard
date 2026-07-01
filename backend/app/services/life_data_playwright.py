"""
使用Playwright获取抖音来客后台门店概览数据

通过无头浏览器模拟真实浏览器环境，绕过反爬机制获取数据。

创建日期: 2026-01-31
"""

import asyncio
import logging
import json
import os
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Playwright依赖检查
try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright未安装，请运行: pip install playwright && playwright install chromium")


class LifeDataPlaywright:
    """
    使用Playwright获取来客后台门店概览数据

    通过无头浏览器模拟真实浏览器环境，可以：
    1. 自动获取动态的x-secsdk-csrf-token
    2. 执行导出操作并下载Excel
    3. 或直接从API响应中提取数据
    """

    BASE_URL = "https://www.life-data.cn"
    POI_OVERVIEW_URL = "https://www.life-data.cn/store/my/chain/poi/overview"

    def __init__(
        self,
        cookie_string: str,
        life_account_id: str,
        headless: bool = True,
        download_dir: Optional[str] = None
    ):
        """
        初始化Playwright客户端

        Args:
            cookie_string: Cookie字符串
            life_account_id: 来客商户账户ID
            headless: 是否无头模式（默认True）
            download_dir: 下载目录（默认为临时目录）
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright未安装，请运行: pip install playwright && playwright install chromium")

        self.cookie_string = cookie_string
        self.life_account_id = life_account_id
        self.headless = headless
        self.download_dir = download_dir or str(Path(__file__).parent.parent.parent / "downloads")

        # 确保下载目录存在
        os.makedirs(self.download_dir, exist_ok=True)

        self._browser: Optional[Browser] = None
        self._context = None
        self._page: Optional[Page] = None

    @staticmethod
    def _extract_poi_ranking(data) -> List[Dict[str, Any]]:
        """
        从嵌套的layout结构中递归查找poiRanking数据

        Args:
            data: API响应的data字段（dict类型）

        Returns:
            门店数据列表
        """
        def _search(obj):
            if isinstance(obj, dict):
                if "poiRanking" in obj:
                    poi_ranking = obj["poiRanking"]
                    if isinstance(poi_ranking, dict):
                        data_list = poi_ranking.get("data", [])
                        if isinstance(data_list, list) and data_list:
                            return data_list
                for value in obj.values():
                    result = _search(value)
                    if result:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = _search(item)
                    if result:
                        return result
            return None

        return _search(data) or []

    def _parse_cookies(self) -> List[Dict[str, Any]]:
        """将Cookie字符串解析为Playwright格式"""
        cookies = []
        for item in self.cookie_string.split(";"):
            item = item.strip()
            if "=" in item:
                name, value = item.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".life-data.cn",
                    "path": "/"
                })
        return cookies

    async def _init_browser(self):
        """初始化浏览器"""
        if self._browser is not None:
            return

        playwright = await async_playwright().start()
        self._browser = await playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu'
            ]
        )

        # 创建上下文，设置下载目录
        self._context = await self._browser.new_context(
            accept_downloads=True,
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0'
        )

        # 设置Cookies
        cookies = self._parse_cookies()
        await self._context.add_cookies(cookies)

        # 创建页面
        self._page = await self._context.new_page()

        logger.info("Playwright浏览器初始化完成")

    async def close(self):
        """关闭浏览器"""
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page = None
            logger.info("Playwright浏览器已关闭")

    async def fetch_poi_overview_via_api(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        通过拦截API请求获取门店概览数据

        这种方式更可靠，直接获取API返回的JSON数据。

        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)

        Returns:
            {poi_id: {"video_cnt_1d": int, "poi_score": float}, ...}
        """
        await self._init_browser()

        # 默认日期范围：当月1号到昨天
        if start_date is None:
            today = datetime.now()
            start_date = today.replace(day=1).strftime("%Y-%m-%d")
        if end_date is None:
            yesterday = datetime.now() - timedelta(days=1)
            end_date = yesterday.strftime("%Y-%m-%d")

        logger.info(f"通过Playwright获取门店概览数据: {start_date} ~ {end_date}")

        poi_data = {}
        api_response_data = None

        # 设置API响应拦截
        async def handle_response(response):
            nonlocal api_response_data
            if "/api/dito/query" in response.url:
                try:
                    json_data = await response.json()
                    if json_data.get("code") == 0:
                        data = json_data.get("data", {})

                        # 方式1：直接列表格式（旧格式兼容）
                        if isinstance(data, list) and data:
                            first_item = data[0] if data else {}
                            if first_item.get("poi_id") or first_item.get("poi_id_str"):
                                api_response_data = data
                                logger.info(f"拦截到门店数据API响应(列表格式)，共{len(data)}条")
                                return

                        # 方式2：嵌套layout结构（dito API实际返回格式）
                        if isinstance(data, dict):
                            store_list = self._extract_poi_ranking(data)
                            if store_list:
                                api_response_data = store_list
                                logger.info(f"拦截到门店数据API响应(layout格式)，共{len(store_list)}条")
                                return

                except Exception as e:
                    logger.debug(f"解析API响应失败: {e}")

        self._page.on("response", handle_response)

        try:
            # 构建带日期参数的URL
            url = f"{self.POI_OVERVIEW_URL}?start_date={start_date}&end_date={end_date}&date_type=nature_month"

            # 访问页面
            logger.info(f"正在访问: {url}")
            await self._page.goto(url, wait_until="networkidle", timeout=60000)

            # 检查当前URL和页面标题，判断是否被重定向到登录页面
            current_url = self._page.url
            page_title = await self._page.title()
            logger.info(f"当前URL: {current_url}")
            logger.info(f"页面标题: {page_title}")

            # 如果被重定向到登录页面
            if "login" in current_url.lower() or "登录" in page_title or "passport" in current_url.lower():
                logger.error("Cookie已过期，页面被重定向到登录页面")
                return {}

            # 等待页面加载完成
            await asyncio.sleep(3)

            # 如果没有拦截到数据，尝试触发加载
            if not api_response_data:
                logger.info("尝试触发数据加载...")
                # 尝试点击刷新或切换日期来触发API请求
                try:
                    # 等待表格加载
                    await self._page.wait_for_selector("table", timeout=10000)
                except:
                    pass
                await asyncio.sleep(2)

            # 处理拦截到的数据
            if api_response_data:
                for item in api_response_data:
                    if not isinstance(item, dict):
                        continue
                    poi_id = str(
                        item.get("poi_id", "") or
                        item.get("poi_id_str", "") or
                        ""
                    )
                    if poi_id and poi_id != "None" and poi_id != "null":
                        poi_data[poi_id] = {
                            "video_cnt_1d": self._safe_int(item.get("video_cnt_1d", 0)),
                            "poi_score": self._safe_float(item.get("poi_score", 0))
                        }
                logger.info(f"从API响应解析到 {len(poi_data)} 个门店数据")
            else:
                logger.warning("未能拦截到门店数据API响应")

            return poi_data

        except Exception as e:
            logger.error(f"Playwright获取数据失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}
        finally:
            self._page.remove_listener("response", handle_response)

    async def fetch_poi_overview_via_export(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        timeout: int = 120
    ) -> Dict[str, Dict[str, Any]]:
        """
        通过点击导出按钮下载Excel并解析

        这种方式可以获取所有门店数据（分页查询可能有限制）。

        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            timeout: 等待下载完成的超时时间（秒）

        Returns:
            {poi_id: {"video_cnt_1d": int, "poi_score": float}, ...}
        """
        await self._init_browser()

        # 默认日期范围
        if start_date is None:
            today = datetime.now()
            start_date = today.replace(day=1).strftime("%Y-%m-%d")
        if end_date is None:
            yesterday = datetime.now() - timedelta(days=1)
            end_date = yesterday.strftime("%Y-%m-%d")

        logger.info(f"通过Playwright导出Excel获取门店概览数据: {start_date} ~ {end_date}")

        poi_data = {}

        try:
            # 构建URL
            url = f"{self.POI_OVERVIEW_URL}?start_date={start_date}&end_date={end_date}&date_type=nature_month"

            # 访问页面
            logger.info(f"正在访问: {url}")
            await self._page.goto(url, wait_until="networkidle", timeout=60000)

            # 等待页面完全加载
            await asyncio.sleep(5)

            # 查找并点击导出按钮
            export_button = None

            # 尝试多种选择器
            selectors = [
                'button:has-text("导出")',
                'span:has-text("导出")',
                '[class*="export"]',
                'button:has-text("下载")',
            ]

            for selector in selectors:
                try:
                    export_button = await self._page.wait_for_selector(selector, timeout=5000)
                    if export_button:
                        logger.info(f"找到导出按钮: {selector}")
                        break
                except:
                    continue

            if not export_button:
                logger.warning("未找到导出按钮，尝试从页面数据中提取")
                # 回退到API拦截方式
                return await self.fetch_poi_overview_via_api(start_date, end_date)

            # 设置下载监听
            async with self._page.expect_download(timeout=timeout * 1000) as download_info:
                # 点击导出按钮
                await export_button.click()
                logger.info("已点击导出按钮，等待下载...")

            download = await download_info.value

            # 保存文件
            download_path = os.path.join(self.download_dir, download.suggested_filename)
            await download.save_as(download_path)
            logger.info(f"Excel下载完成: {download_path}")

            # 解析Excel
            poi_data = self._parse_excel(download_path)

            # 清理下载文件
            try:
                os.remove(download_path)
            except:
                pass

            return poi_data

        except Exception as e:
            logger.error(f"Playwright导出失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}

    def _parse_excel(self, file_path: str) -> Dict[str, Dict[str, Any]]:
        """解析Excel文件"""
        try:
            import openpyxl

            workbook = openpyxl.load_workbook(file_path, read_only=True)
            sheet = workbook.active

            rows = list(sheet.iter_rows(values_only=True))
            if len(rows) < 2:
                return {}

            headers = rows[0]
            poi_data = {}

            # 查找列索引
            poi_id_col = None
            video_cnt_col = None
            poi_score_col = None

            for i, header in enumerate(headers):
                if header:
                    header_str = str(header)
                    if "门店ID" in header_str or header_str in ("poi_id", "poi_id_str"):
                        poi_id_col = i
                    elif "视频" in header_str or header_str == "video_cnt_1d":
                        video_cnt_col = i
                    elif "评分" in header_str or header_str == "poi_score":
                        poi_score_col = i

            if poi_id_col is None:
                logger.warning("Excel中未找到门店ID列")
                return {}

            for row in rows[1:]:
                poi_id = str(row[poi_id_col]) if row[poi_id_col] else ""
                if poi_id and poi_id != "None" and poi_id != "null":
                    video_cnt = self._safe_int(row[video_cnt_col]) if video_cnt_col is not None else 0
                    poi_score = self._safe_float(row[poi_score_col]) if poi_score_col is not None else 0.0

                    poi_data[poi_id] = {
                        "video_cnt_1d": video_cnt,
                        "poi_score": poi_score
                    }

            logger.info(f"从Excel解析到 {len(poi_data)} 个门店数据")
            return poi_data

        except ImportError:
            logger.error("openpyxl未安装，请运行: pip install openpyxl")
            return {}
        except Exception as e:
            logger.error(f"解析Excel失败: {e}")
            return {}

    @staticmethod
    def _safe_int(val) -> int:
        """安全转换为整数"""
        if val is None:
            return 0
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            try:
                return int(val.replace(",", "").replace("，", ""))
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
                return float(val.replace(",", "").replace("，", ""))
            except ValueError:
                return 0.0
        return 0.0


async def fetch_poi_overview_data(
    cookie_string: str,
    life_account_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    method: str = "api"
) -> Dict[str, Dict[str, Any]]:
    """
    便捷函数：使用Playwright获取门店概览数据

    Args:
        cookie_string: Cookie字符串
        life_account_id: 来客商户账户ID
        start_date: 开始日期
        end_date: 结束日期
        method: 获取方式 ("api" 或 "export")

    Returns:
        {poi_id: {"video_cnt_1d": int, "poi_score": float}, ...}
    """
    client = LifeDataPlaywright(
        cookie_string=cookie_string,
        life_account_id=life_account_id,
        headless=True
    )

    try:
        if method == "export":
            return await client.fetch_poi_overview_via_export(start_date, end_date)
        else:
            return await client.fetch_poi_overview_via_api(start_date, end_date)
    finally:
        await client.close()
