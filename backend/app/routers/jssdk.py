import time
import hashlib
import secrets
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.services.token_manager import TokenManager
from app.config import get_settings, Settings

router = APIRouter()


class JssdkConfigRequest(BaseModel):
    """JS-SDK配置请求"""
    url: str


class JssdkConfigResponse(BaseModel):
    """JS-SDK配置响应"""
    corp_id: str
    agent_id: str
    timestamp: int
    nonce_str: str
    signature: str


def generate_signature(jsapi_ticket: str, nonce_str: str, timestamp: int, url: str) -> str:
    """
    生成JS-SDK签名
    签名算法: sha1(jsapi_ticket=xxx&noncestr=xxx&timestamp=xxx&url=xxx)
    """
    params = {
        "jsapi_ticket": jsapi_ticket,
        "noncestr": nonce_str,
        "timestamp": str(timestamp),
        "url": url
    }
    # 按key排序拼接
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    param_str = "&".join([f"{k}={v}" for k, v in sorted_params])

    # SHA1签名
    signature = hashlib.sha1(param_str.encode("utf-8")).hexdigest()
    return signature


@router.post("/jssdk/config", response_model=JssdkConfigResponse)
async def get_jssdk_config(
    request: JssdkConfigRequest,
    settings: Settings = Depends(get_settings)
):
    """
    获取JS-SDK配置
    前端调用企业微信JS-SDK前需要获取签名配置
    """
    token_manager = TokenManager(settings)

    # 获取jsapi_ticket
    jsapi_ticket = await token_manager.get_jsapi_ticket()

    # 生成签名参数
    timestamp = int(time.time())
    nonce_str = secrets.token_hex(8)

    # 计算签名
    signature = generate_signature(
        jsapi_ticket=jsapi_ticket,
        nonce_str=nonce_str,
        timestamp=timestamp,
        url=request.url
    )

    return JssdkConfigResponse(
        corp_id=settings.wecom_corp_id,
        agent_id=settings.wecom_agent_id,
        timestamp=timestamp,
        nonce_str=nonce_str,
        signature=signature
    )


@router.get("/jssdk/agent-config")
async def get_agent_config(
    url: str,
    settings: Settings = Depends(get_settings)
):
    """
    获取应用级别JS-SDK配置（agentConfig）
    用于调用应用级别的接口
    """
    token_manager = TokenManager(settings)

    # 获取应用级别的jsapi_ticket
    agent_ticket = await token_manager.get_agent_jsapi_ticket()

    timestamp = int(time.time())
    nonce_str = secrets.token_hex(8)

    signature = generate_signature(
        jsapi_ticket=agent_ticket,
        nonce_str=nonce_str,
        timestamp=timestamp,
        url=url
    )

    return {
        "corp_id": settings.wecom_corp_id,
        "agent_id": settings.wecom_agent_id,
        "timestamp": timestamp,
        "nonce_str": nonce_str,
        "signature": signature
    }
