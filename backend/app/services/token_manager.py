import time
import httpx
from typing import Any


class TokenManager:
    """
    企业微信Access Token管理器
    注意: 生产环境应使用Redis缓存token
    """

    BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin"

    # 简单的内存缓存 (生产环境应使用Redis)
    _cache: dict[str, Any] = {}

    def __init__(self, settings):
        self.corp_id = settings.wecom_corp_id
        self.secret = settings.wecom_secret
        self.agent_id = settings.wecom_agent_id
        self.external_contact_secret = getattr(settings, 'wecom_external_contact_secret', '')

    async def get_access_token(self) -> str:
        """
        获取access_token
        有效期2小时，需要缓存
        https://developer.work.weixin.qq.com/document/path/91039
        """
        cache_key = f"access_token:{self.corp_id}:{self.secret[:8]}"

        # 检查缓存
        cached = self._cache.get(cache_key)
        if cached and cached["expires_at"] > time.time():
            return cached["token"]

        # 请求新token
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/gettoken",
                params={"corpid": self.corp_id, "corpsecret": self.secret},
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as e:
                raise Exception(f"获取access_token失败: 非JSON响应 {response.text[:200]}") from e

        if result.get("errcode", 0) != 0:
            raise Exception(f"获取access_token失败: {result.get('errmsg')}")

        token = result["access_token"]
        expires_in = result.get("expires_in", 7200)
        refresh_in = max(int(expires_in) - 300, 0)

        # 缓存token (提前5分钟过期)
        self._cache[cache_key] = {
            "token": token,
            "expires_at": time.time() + refresh_in
        }

        return token

    async def get_jsapi_ticket(self) -> str:
        """
        获取企业jsapi_ticket
        用于JS-SDK签名
        https://developer.work.weixin.qq.com/document/path/90506
        """
        cache_key = f"jsapi_ticket:{self.corp_id}"

        # 检查缓存
        cached = self._cache.get(cache_key)
        if cached and cached["expires_at"] > time.time():
            return cached["ticket"]

        # 先获取access_token
        access_token = await self.get_access_token()

        # 请求jsapi_ticket
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/get_jsapi_ticket",
                params={"access_token": access_token},
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as e:
                raise Exception(f"获取jsapi_ticket失败: 非JSON响应 {response.text[:200]}") from e

        if result.get("errcode", 0) != 0:
            raise Exception(f"获取jsapi_ticket失败: {result.get('errmsg')}")

        ticket = result["ticket"]
        expires_in = result.get("expires_in", 7200)
        refresh_in = max(int(expires_in) - 300, 0)

        # 缓存
        self._cache[cache_key] = {
            "ticket": ticket,
            "expires_at": time.time() + refresh_in
        }

        return ticket

    async def get_agent_jsapi_ticket(self) -> str:
        """
        获取应用的jsapi_ticket
        用于agentConfig签名
        https://developer.work.weixin.qq.com/document/path/90506
        """
        cache_key = f"agent_ticket:{self.corp_id}:{self.agent_id}"

        # 检查缓存
        cached = self._cache.get(cache_key)
        if cached and cached["expires_at"] > time.time():
            return cached["ticket"]

        # 先获取access_token
        access_token = await self.get_access_token()

        # 请求agent jsapi_ticket
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/ticket/get",
                params={"access_token": access_token, "type": "agent_config"},
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as e:
                raise Exception(f"获取agent_ticket失败: 非JSON响应 {response.text[:200]}") from e

        if result.get("errcode", 0) != 0:
            raise Exception(f"获取agent_ticket失败: {result.get('errmsg')}")

        ticket = result["ticket"]
        expires_in = result.get("expires_in", 7200)
        refresh_in = max(int(expires_in) - 300, 0)

        # 缓存
        self._cache[cache_key] = {
            "ticket": ticket,
            "expires_at": time.time() + refresh_in
        }

        return ticket

    async def get_external_contact_access_token(self) -> str:
        """
        获取客户联系的access_token
        用于调用客户群相关API（externalcontact/groupchat/list、get等）
        https://developer.work.weixin.qq.com/document/path/92120
        """
        if not self.external_contact_secret:
            raise Exception("未配置客户联系secret (wecom_external_contact_secret)")

        cache_key = f"external_contact_token:{self.corp_id}:{self.external_contact_secret[:8]}"

        # 检查缓存
        cached = self._cache.get(cache_key)
        if cached and cached["expires_at"] > time.time():
            return cached["token"]

        # 请求新token（使用客户联系secret）
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/gettoken",
                params={"corpid": self.corp_id, "corpsecret": self.external_contact_secret},
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as e:
                raise Exception(f"获取客户联系access_token失败: 非JSON响应 {response.text[:200]}") from e

        if result.get("errcode", 0) != 0:
            raise Exception(f"获取客户联系access_token失败: {result.get('errmsg')}")

        token = result["access_token"]
        expires_in = result.get("expires_in", 7200)
        refresh_in = max(int(expires_in) - 300, 0)

        # 缓存token (提前5分钟过期)
        self._cache[cache_key] = {
            "token": token,
            "expires_at": time.time() + refresh_in
        }

        return token
