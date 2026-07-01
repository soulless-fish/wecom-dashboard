"""
凡科商城 API 客户端

封装 OAuth 授权、token 刷新和基础数据接口调用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx


logger = logging.getLogger(__name__)


class FankeApiError(Exception):
    """凡科 API 调用异常"""


class FankeClient:
    """
    凡科商城 API 客户端。

    凡科文档要求换 token 使用 POST，请求参数放在查询参数中。
    """

    BASE_URL = "https://waybill.api.jz.fkw.com"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        return_url: str,
        platform_code: str = "FKW",
    ):
        """初始化凡科客户端配置"""
        self.client_id = client_id
        self.client_secret = client_secret
        self.return_url = return_url
        self.platform_code = platform_code or "FKW"

    def build_auth_url(self, state: str = "fanke_auth") -> str:
        """生成商家登录授权链接"""
        query = urlencode(
            {
                "cmd": "getAuthCode",
                "client_id": self.client_id,
                "platform_code": self.platform_code,
                "redirect_uri": self.return_url,
                "state": state,
            }
        )
        return f"{self.BASE_URL}/api/oauth/getAuthCode?{query}"

    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """使用凡科回调 code 换取 access_token"""
        query = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
            }
        )
        return await self._post_oauth(f"/api/oauth/getAccessToken?{query}")

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """使用 refresh_token 刷新 access_token"""
        query = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
        return await self._post_oauth(f"/api/oauth/refreshAccessToken?{query}")

    async def fetch_product_list(
        self,
        access_token: str,
        page_no: int = 1,
        page_size: int = 1,
    ) -> Dict[str, Any]:
        """获取商品列表，用于验证凡科数据接口是否可正常访问"""
        query = urlencode(
            {
                "access_token": access_token,
                "method": "getProductList",
                "page_no": page_no,
                "page_size": page_size,
            }
        )
        url = f"{self.BASE_URL}/api/product/getList?{query}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            response = await client.get(url)
        return self._parse_response(response)

    async def fetch_order_list(
        self,
        access_token: str,
        time_from: str,
        time_to: str,
        page_no: int = 1,
        page_size: int = 200,
    ) -> Dict[str, Any]:
        """获取凡科订单列表，用于同步近30天买家订单明细"""
        query = urlencode(
            {
                "access_token": access_token,
                "method": "getOrderList",
                "page_no": page_no,
                "page_size": page_size,
                "time_type": "created",
                "time_from": time_from,
                "time_to": time_to,
            }
        )
        url = f"{self.BASE_URL}/api/order/getList?{query}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
            response = await client.get(url)
        return self._parse_response(response)

    async def _post_oauth(self, path_with_query: str) -> Dict[str, Any]:
        """调用凡科 OAuth POST 接口并解析响应"""
        url = f"{self.BASE_URL}{path_with_query}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            response = await client.post(url)
        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: httpx.Response) -> Dict[str, Any]:
        """解析凡科响应，失败时抛出包含错误信息的异常"""
        try:
            data = response.json()
        except ValueError as exc:
            raise FankeApiError(f"凡科接口返回非JSON响应: status={response.status_code}") from exc

        success = data.get("success")
        rt = data.get("rt")
        # token 成功响应可能不带 success 字段，因此只在明确 success=false 时判失败。
        if success is False or (rt not in (None, 0) and "access_token" not in data):
            message = data.get("msg") or data.get("error_message") or "凡科接口调用失败"
            raise FankeApiError(message)

        return data


async def ensure_valid_token(
    client: FankeClient,
    token_record: Dict[str, Any],
    refresh_margin_seconds: int = 600,
) -> tuple[Dict[str, Any], bool]:
    """
    确保 access_token 可用。

    当 access_token 缺失、已过期或距离过期小于 refresh_margin_seconds 时，
    自动使用 refresh_token 刷新，并返回新的 token 记录。
    """
    access_token = str(token_record.get("access_token") or "")
    if not access_token:
        raise FankeApiError("凡科尚未授权，缺少access_token")

    expires_at = parse_iso_datetime(token_record.get("access_token_expires_at"))
    now = datetime.now(timezone.utc)
    if expires_at and expires_at > now + timedelta(seconds=refresh_margin_seconds):
        return token_record, False

    refresh_token = str(token_record.get("refresh_token") or "")
    if not refresh_token:
        raise FankeApiError("凡科refresh_token不存在，需要重新授权")

    refresh_expires_at = parse_iso_datetime(token_record.get("refresh_token_expires_at"))
    if refresh_expires_at and refresh_expires_at <= now:
        raise FankeApiError("凡科refresh_token已过期，需要重新授权")

    logger.info("凡科access_token即将过期或已过期，开始自动刷新")
    refreshed = await client.refresh_access_token(refresh_token)
    return build_token_record(refreshed, previous_record=token_record), True


def build_token_record(
    token_data: Dict[str, Any],
    previous_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    将凡科 token 响应整理成可持久化结构。

    凡科返回 expires_in/re_expires_in 是秒数，这里换算为 UTC ISO 时间。
    如果刷新接口没有返回部分店铺字段或 refresh_token，则沿用旧记录。
    """
    now = datetime.now(timezone.utc)
    previous_record = previous_record or {}
    expires_in = _safe_int(token_data.get("expires_in"), 7200)
    refresh_expires_raw = token_data.get("re_expires_in")
    refresh_expires_at = previous_record.get("refresh_token_expires_at") or ""
    if refresh_expires_raw is not None:
        refresh_expires_in = _safe_int(refresh_expires_raw, 2419200)
        refresh_expires_at = (now + timedelta(seconds=refresh_expires_in)).isoformat()

    return {
        "shop_id": str(token_data.get("shop_id") or previous_record.get("shop_id") or ""),
        "shop_name": str(token_data.get("shop_name") or previous_record.get("shop_name") or ""),
        "user_id": str(token_data.get("user_id") or previous_record.get("user_id") or ""),
        "platform_code": str(token_data.get("platform_code") or previous_record.get("platform_code") or ""),
        "access_token": str(token_data.get("access_token") or ""),
        "access_token_expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "refresh_token": str(token_data.get("refresh_token") or previous_record.get("refresh_token") or ""),
        "refresh_token_expires_at": refresh_expires_at,
        "token_type": str(token_data.get("token_type") or previous_record.get("token_type") or ""),
        "updated_at": now.isoformat(),
    }


def mask_token(value: Optional[str]) -> str:
    """隐藏 token 中间内容，仅用于接口状态展示"""
    token = value or ""
    if len(token) <= 10:
        return "***" if token else ""
    return f"{token[:4]}...{token[-4:]}"


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    """解析 ISO 时间字符串，返回带 UTC 时区的 datetime"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_int(value: Any, default: int) -> int:
    """安全转换整数，失败时返回默认值"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
