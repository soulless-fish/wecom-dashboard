"""
抖音开放平台 Access Token 管理器
用于获取和缓存抖音API调用凭证
"""
import time
import httpx
from typing import Any


class DouyinTokenManager:
    """
    抖音开放平台Access Token管理器
    注意: 生产环境应使用Redis缓存token
    """

    # 抖音开放平台API基础URL
    BASE_URL = "https://open.douyin.com"

    # 简单的内存缓存 (生产环境应使用Redis)
    _cache: dict[str, Any] = {}

    def __init__(self, settings):
        """
        初始化Token管理器

        Args:
            settings: 应用配置对象，需包含douyin_client_key和douyin_client_secret
        """
        self.client_key = settings.douyin_client_key
        self.client_secret = settings.douyin_client_secret
        self.account_id = settings.douyin_account_id

    async def get_client_token(self) -> str:
        """
        获取client_token (应用授权凭证)
        用于调用不需要用户授权的接口

        有效期2小时，需要缓存
        文档: https://open.douyin.com/oauth/client_token/

        Returns:
            str: access_token

        Raises:
            Exception: 获取token失败时抛出异常
        """
        cache_key = f"douyin_client_token:{self.client_key[:8]}"

        # 检查缓存
        cached = self._cache.get(cache_key)
        if cached and cached["expires_at"] > time.time():
            return cached["token"]

        # 请求新token
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/oauth/client_token/",
                json={
                    "client_key": self.client_key,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credential"
                },
                headers={
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as e:
                raise Exception(f"获取抖音client_token失败: 非JSON响应 {response.text[:200]}") from e

        # 检查响应
        data = result.get("data", {})
        error_code = data.get("error_code", 0)

        if error_code != 0:
            description = data.get("description", "未知错误")
            raise Exception(f"获取抖音client_token失败: {description} (error_code: {error_code})")

        token = data.get("access_token")
        expires_in = data.get("expires_in", 7200)
        refresh_in = max(int(expires_in) - 300, 0)

        if not token:
            raise Exception("获取抖音client_token失败: 响应中无access_token")

        # 缓存token (提前5分钟过期)
        self._cache[cache_key] = {
            "token": token,
            "expires_at": time.time() + refresh_in
        }

        return token

    async def get_access_token(self) -> str:
        """
        获取access_token的别名方法
        为了与其他token管理器保持一致的接口
        """
        return await self.get_client_token()

    def clear_cache(self):
        """清除所有缓存的token"""
        self._cache.clear()
  
    def get_account_id(self) -> str:
        """获取配置的来客商户账户ID"""
        return self.account_id
