"""
巨量引擎 Marketing API OAuth 客户端。

当前先覆盖授权回调后的 token 换取和刷新，后续集成侧边栏数据时继续在
JLYQ 目录下扩展账户、报表、素材等接口客户端。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.oceanengine.com"
DEFAULT_CALLBACK_URL = "http://localhost:8000/api/v1/jlyq/oauth/callback"


@dataclass(frozen=True)
class JlyqOAuthSettings:
    """巨量引擎 OAuth 配置。"""

    app_id: str
    secret: str
    callback_url: str = DEFAULT_CALLBACK_URL

    @property
    def ready_for_token_exchange(self) -> bool:
        """判断是否已经具备换取 token 的必要配置。"""
        return bool(self.app_id and self.secret)


def load_oauth_settings() -> JlyqOAuthSettings:
    """从环境变量读取巨量引擎 OAuth 配置。"""
    return JlyqOAuthSettings(
        app_id=os.environ.get("JLYQ_APP_ID", "").strip(),
        secret=os.environ.get("JLYQ_APP_SECRET", "").strip(),
        callback_url=os.environ.get("JLYQ_CALLBACK_URL", DEFAULT_CALLBACK_URL).strip() or DEFAULT_CALLBACK_URL,
    )


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """向巨量引擎接口发送 JSON POST 请求。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """向巨量引擎接口发送 GET 请求。"""
    request = Request(url, headers=headers or {}, method="GET")
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def exchange_auth_code(auth_code: str, settings: JlyqOAuthSettings | None = None) -> dict[str, Any]:
    """使用回调返回的 auth_code 换取 access_token。"""
    resolved = settings or load_oauth_settings()
    if not resolved.ready_for_token_exchange:
        raise RuntimeError("缺少 JLYQ_APP_ID 或 JLYQ_APP_SECRET，无法换取巨量引擎 token")

    return post_json(
        f"{API_BASE}/open_api/oauth2/access_token/",
        {
            "app_id": int(resolved.app_id),
            "secret": resolved.secret,
            "auth_code": auth_code,
        },
    )


def refresh_access_token(refresh_token: str, settings: JlyqOAuthSettings | None = None) -> dict[str, Any]:
    """使用 refresh_token 刷新 access_token。"""
    resolved = settings or load_oauth_settings()
    if not resolved.ready_for_token_exchange:
        raise RuntimeError("缺少 JLYQ_APP_ID 或 JLYQ_APP_SECRET，无法刷新巨量引擎 token")

    return post_json(
        f"{API_BASE}/open_api/oauth2/refresh_token/",
        {
            "app_id": int(resolved.app_id),
            "secret": resolved.secret,
            "refresh_token": refresh_token,
        },
    )


def fetch_authorized_advertisers(access_token: str) -> dict[str, Any]:
    """获取当前 access_token 已授权的投放账户列表。"""
    query = urlencode({"access_token": access_token})
    return get_json(f"{API_BASE}/open_api/oauth2/advertiser/get/?{query}")


def fetch_user_info(access_token: str) -> dict[str, Any]:
    """获取当前授权用户信息和权限范围。"""
    return get_json(
        f"{API_BASE}/open_api/2/user/info/",
        headers={"Access-Token": access_token},
    )
