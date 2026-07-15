"""
抖音来客API客户端
用于调用抖音开放平台的来客相关接口
"""
import httpx
from typing import Any, Optional
from .douyin_token_manager import DouyinTokenManager


class DouyinClient:
    """
    抖音来客API客户端
    封装抖音开放平台的来客相关接口调用
    """

    # 抖音开放平台API基础URL
    BASE_URL = "https://open.douyin.com"

    def __init__(self, token_manager: DouyinTokenManager):
        """
        初始化客户端

        Args:
            token_manager: Token管理器实例
        """
        self.token_manager = token_manager

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        use_account_header: bool = False
    ) -> dict[str, Any]:
        """
        发送API请求

        Args:
            method: HTTP方法 (GET/POST)
            endpoint: API端点路径
            data: POST请求体数据
            params: URL查询参数
            use_account_header: 是否在header中添加Rpc-Transit-Life-Account

        Returns:
            dict: API响应数据

        Raises:
            Exception: API调用失败时抛出异常
        """
        # 获取access_token
        access_token = await self.token_manager.get_access_token()

        # 构建请求头
        headers = {
            "access-token": access_token,
            "Content-Type": "application/json"
        }

        # 如果需要，添加来客商户账户ID到header
        if use_account_header:
            account_id = self.token_manager.get_account_id()
            if account_id:
                headers["Rpc-Transit-Life-Account"] = account_id

        url = f"{self.BASE_URL}{endpoint}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            if method.upper() == "GET":
                response = await client.get(url, params=params, headers=headers)
            elif method.upper() == "POST":
                response = await client.post(url, json=data, headers=headers)
            else:
                raise ValueError(f"不支持的HTTP方法: {method}")

            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as e:
                raise Exception(f"抖音API返回非JSON响应: {response.text[:200]}") from e

        # 检查响应
        self._check_response(result)

        return result

    def _check_response(self, result: dict) -> None:
        """
        检查API响应是否成功

        Args:
            result: API响应数据

        Raises:
            Exception: 如果响应表示错误
        """
        # 检查data中的error_code
        data = result.get("data", {})
        error_code = data.get("error_code", 0)

        if error_code != 0:
            description = data.get("description", "未知错误")
            raise Exception(f"抖音API调用失败: {description} (error_code: {error_code})")

        # 检查extra中的error_code (有些接口错误信息在extra中)
        extra = result.get("extra", {})
        extra_error_code = extra.get("error_code", 0)

        if extra_error_code != 0:
            description = extra.get("description", "未知错误")
            raise Exception(f"抖音API调用失败: {description} (error_code: {extra_error_code})")

    # ==================== 来客相关接口 ====================

    async def get_user_message(
        self,
        start_time: int,
        end_time: int,
        message_type: int = 0,
        username: Optional[str] = None,
        page_num: int = 1,
        page_size: int = 20
    ) -> dict[str, Any]:
        """
        获取用户消息列表

        Args:
            start_time: 开始时间戳(秒)
            end_time: 结束时间戳(秒)
            message_type: 消息类型 (0-全部)
            username: 用户名筛选
            page_num: 页码
            page_size: 每页数量

        Returns:
            dict: 消息列表数据
        """
        data = {
            "start_time": start_time,
            "end_time": end_time,
            "type": message_type,
            "page_num": page_num,
            "page_size": page_size
        }

        if username:
            data["username"] = username

        return await self._request(
            "POST",
            "/goodlife/v1/message/get_user_message/",
            data=data,
            use_account_header=True
        )

    async def get_poi_list(
        self,
        page: int = 1,
        size: int = 100
    ) -> dict[str, Any]:
        """
        获取门店列表

        Args:
            page: 页码
            size: 每页数量

        Returns:
            dict: 门店列表数据
        """
        return await self._request(
            "GET",
            "/goodlife/v1/shop/poi/query/",
            params={
                "account_id": self.token_manager.get_account_id(),
                "page": page,
                "size": size,
            },
            use_account_header=True
        )

    async def get_poi_info(self, poi_id: str) -> dict[str, Any]:
        """
        获取门店详情

        Args:
            poi_id: 门店ID

        Returns:
            dict: 门店详情数据
        """
        return await self._request(
            "GET",
            "/goodlife/v1/shop/poi/",
            params={"poi_id": poi_id},
            use_account_header=True
        )

    async def get_order_list(
        self,
        start_time: int,
        end_time: int,
        page_num: int = 1,
        page_size: int = 20,
        order_status: Optional[int] = None
    ) -> dict[str, Any]:
        """
        获取订单列表

        Args:
            start_time: 开始时间戳(秒)
            end_time: 结束时间戳(秒)
            page_num: 页码
            page_size: 每页数量
            order_status: 订单状态筛选

        Returns:
            dict: 订单列表数据
        """
        data = {
            "start_time": start_time,
            "end_time": end_time,
            "page_num": page_num,
            "page_size": page_size
        }

        if order_status is not None:
            data["order_status"] = order_status

        return await self._request(
            "POST",
            "/goodlife/v1/trade/order/query/",
            data=data,
            use_account_header=True
        )

    async def get_visitor_list(
        self,
        start_time: int,
        end_time: int,
        page_num: int = 1,
        page_size: int = 20
    ) -> dict[str, Any]:
        """
        获取来客列表 (访客记录)

        Args:
            start_time: 开始时间戳(秒)
            end_time: 结束时间戳(秒)
            page_num: 页码
            page_size: 每页数量

        Returns:
            dict: 来客列表数据
        """
        data = {
            "start_time": start_time,
            "end_time": end_time,
            "page_num": page_num,
            "page_size": page_size
        }

        return await self._request(
            "POST",
            "/goodlife/v1/customer/visitor/query/",
            data=data,
            use_account_header=True
        )

    async def get_customer_info(self, open_id: str) -> dict[str, Any]:
        """
        获取客户详细信息

        Args:
            open_id: 客户的open_id

        Returns:
            dict: 客户详细信息
        """
        return await self._request(
            "GET",
            "/goodlife/v1/customer/info/",
            params={"open_id": open_id},
            use_account_header=True
        )

    # ==================== 通用方法 ====================

    async def call_api(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        use_account_header: bool = True
    ) -> dict[str, Any]:
        """
        通用API调用方法
        用于调用未封装的接口

        Args:
            method: HTTP方法
            endpoint: API端点
            data: POST请求体
            params: URL查询参数
            use_account_header: 是否使用账户header

        Returns:
            dict: API响应
        """
        return await self._request(
            method,
            endpoint,
            data=data,
            params=params,
            use_account_header=use_account_header
        )
