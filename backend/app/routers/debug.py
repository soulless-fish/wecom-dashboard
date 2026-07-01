"""
企业微信API调试接口

用于在域名备案期间，通过HTTP工具直接测试企业微信API
无需配置可信域名即可使用

作者: Claude AI
创建日期: 2026-01-13
"""

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, Field
from typing import Optional, List
import httpx
import logging

from app.config import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)

# 企业微信API基础URL
WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


class SendMessageRequest(BaseModel):
    """发送消息请求"""
    touser: Optional[str] = Field(None, description="接收消息的成员ID列表，多个用|分隔")
    toparty: Optional[str] = Field(None, description="接收消息的部门ID列表，多个用|分隔")
    totag: Optional[str] = Field(None, description="接收消息的标签ID列表，多个用|分隔")
    msgtype: str = Field("text", description="消息类型：text/markdown/image/news等")
    content: str = Field(..., description="消息内容")
    safe: int = Field(0, description="是否保密消息，0否1是")


class SendTextCardRequest(BaseModel):
    """发送文本卡片消息请求"""
    touser: Optional[str] = Field(None, description="接收消息的成员ID列表")
    title: str = Field(..., description="卡片标题")
    description: str = Field(..., description="卡片描述")
    url: str = Field(..., description="点击后跳转的链接")
    btntxt: str = Field("详情", description="按钮文字")


# ==================== 凭证相关 ====================

@router.get("/token", summary="获取access_token")
async def get_access_token(
    corpid: Optional[str] = Query(None, description="企业ID，不填则使用配置文件"),
    corpsecret: Optional[str] = Query(None, description="应用Secret，不填则使用配置文件")
):
    """
    获取企业微信access_token

    这是调试API的第一步，获取到的token用于后续API调用
    token有效期为7200秒（2小时）
    """
    settings = get_settings()

    # 使用参数或配置文件中的值
    corp_id = corpid or settings.wecom_corp_id
    secret = corpsecret or settings.wecom_secret

    if not corp_id or not secret:
        raise HTTPException(
            status_code=400,
            detail="请提供corpid和corpsecret参数，或在.env中配置WECOM_CORP_ID和WECOM_SECRET"
        )

    url = f"{WECOM_API_BASE}/gettoken"
    params = {
        "corpid": corp_id,
        "corpsecret": secret
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        result = response.json()

    if result.get("errcode", 0) != 0:
        return {
            "code": result.get("errcode"),
            "message": result.get("errmsg"),
            "data": None,
            "hint": "常见错误: 40013-corpid无效, 40001-secret无效"
        }

    return {
        "code": 0,
        "message": "success",
        "data": {
            "access_token": result.get("access_token"),
            "expires_in": result.get("expires_in"),
            "hint": "请保存此token用于后续API调用，有效期2小时"
        }
    }


@router.get("/token/check", summary="检查access_token是否有效")
async def check_token(
    access_token: str = Query(..., description="要检查的access_token")
):
    """
    检查access_token是否有效

    通过调用获取IP列表接口来验证token
    """
    url = f"{WECOM_API_BASE}/get_api_domain_ip"
    params = {"access_token": access_token}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        result = response.json()

    if result.get("errcode", 0) != 0:
        return {
            "code": result.get("errcode"),
            "message": result.get("errmsg"),
            "data": {"valid": False},
            "hint": "token无效或已过期，请重新获取"
        }

    return {
        "code": 0,
        "message": "success",
        "data": {
            "valid": True,
            "ip_list": result.get("ip_list", [])[:5],  # 只返回前5个IP
            "hint": "token有效"
        }
    }


# ==================== 消息发送 ====================

@router.post("/message/send", summary="发送应用消息")
async def send_message(
    access_token: str = Query(..., description="access_token"),
    request: SendMessageRequest = Body(...)
):
    """
    发送应用消息

    支持发送文本、Markdown等多种类型的消息
    至少需要指定touser、toparty、totag中的一个
    """
    settings = get_settings()

    if not request.touser and not request.toparty and not request.totag:
        raise HTTPException(
            status_code=400,
            detail="请至少指定touser、toparty、totag中的一个"
        )

    url = f"{WECOM_API_BASE}/message/send"
    params = {"access_token": access_token}

    # 构建消息体
    payload = {
        "agentid": int(settings.wecom_agent_id) if settings.wecom_agent_id else 1000002,
        "msgtype": request.msgtype,
        "safe": request.safe
    }

    if request.touser:
        payload["touser"] = request.touser
    if request.toparty:
        payload["toparty"] = request.toparty
    if request.totag:
        payload["totag"] = request.totag

    # 根据消息类型设置内容
    if request.msgtype == "text":
        payload["text"] = {"content": request.content}
    elif request.msgtype == "markdown":
        payload["markdown"] = {"content": request.content}
    else:
        payload["text"] = {"content": request.content}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, params=params, json=payload)
        result = response.json()

    if result.get("errcode", 0) != 0:
        return {
            "code": result.get("errcode"),
            "message": result.get("errmsg"),
            "data": None,
            "hint": "常见错误: 40014-token无效, 60011-用户不在应用可见范围"
        }

    return {
        "code": 0,
        "message": "success",
        "data": {
            "msgid": result.get("msgid"),
            "invaliduser": result.get("invaliduser", ""),
            "invalidparty": result.get("invalidparty", ""),
            "invalidtag": result.get("invalidtag", "")
        }
    }


@router.post("/message/textcard", summary="发送文本卡片消息")
async def send_textcard(
    access_token: str = Query(..., description="access_token"),
    request: SendTextCardRequest = Body(...)
):
    """
    发送文本卡片消息

    卡片消息支持自定义标题、描述和跳转链接
    """
    settings = get_settings()

    url = f"{WECOM_API_BASE}/message/send"
    params = {"access_token": access_token}

    payload = {
        "touser": request.touser or "@all",
        "msgtype": "textcard",
        "agentid": int(settings.wecom_agent_id) if settings.wecom_agent_id else 1000002,
        "textcard": {
            "title": request.title,
            "description": request.description,
            "url": request.url,
            "btntxt": request.btntxt
        }
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, params=params, json=payload)
        result = response.json()

    if result.get("errcode", 0) != 0:
        return {
            "code": result.get("errcode"),
            "message": result.get("errmsg"),
            "data": None
        }

    return {
        "code": 0,
        "message": "success",
        "data": {"msgid": result.get("msgid")}
    }


# ==================== 通讯录管理 ====================

@router.get("/department/list", summary="获取部门列表")
async def get_department_list(
    access_token: str = Query(..., description="access_token"),
    id: Optional[int] = Query(None, description="部门ID，不填则获取全部")
):
    """
    获取部门列表

    可以获取全部部门或指定部门的子部门
    """
    url = f"{WECOM_API_BASE}/department/list"
    params = {"access_token": access_token}
    if id:
        params["id"] = id

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        result = response.json()

    if result.get("errcode", 0) != 0:
        return {
            "code": result.get("errcode"),
            "message": result.get("errmsg"),
            "data": None
        }

    return {
        "code": 0,
        "message": "success",
        "data": {
            "department": result.get("department", [])
        }
    }


@router.get("/user/list", summary="获取部门成员列表")
async def get_user_list(
    access_token: str = Query(..., description="access_token"),
    department_id: int = Query(..., description="部门ID"),
    fetch_child: int = Query(0, description="是否递归获取子部门成员，1是0否")
):
    """
    获取部门成员列表

    获取指定部门的成员列表，可选择是否递归获取子部门
    """
    url = f"{WECOM_API_BASE}/user/simplelist"
    params = {
        "access_token": access_token,
        "department_id": department_id,
        "fetch_child": fetch_child
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        result = response.json()

    if result.get("errcode", 0) != 0:
        return {
            "code": result.get("errcode"),
            "message": result.get("errmsg"),
            "data": None,
            "hint": "常见错误: 60011-无权限访问该部门"
        }

    return {
        "code": 0,
        "message": "success",
        "data": {
            "userlist": result.get("userlist", [])
        }
    }


@router.get("/user/get", summary="获取成员详情")
async def get_user_detail(
    access_token: str = Query(..., description="access_token"),
    userid: str = Query(..., description="成员UserID")
):
    """
    获取成员详情

    获取指定成员的详细信息
    """
    url = f"{WECOM_API_BASE}/user/get"
    params = {
        "access_token": access_token,
        "userid": userid
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        result = response.json()

    if result.get("errcode", 0) != 0:
        return {
            "code": result.get("errcode"),
            "message": result.get("errmsg"),
            "data": None
        }

    # 过滤敏感信息
    safe_fields = ["userid", "name", "department", "position", "mobile",
                   "email", "avatar", "status", "enable"]
    filtered_result = {k: result.get(k) for k in safe_fields if k in result}

    return {
        "code": 0,
        "message": "success",
        "data": filtered_result
    }


# ==================== 应用管理 ====================

@router.get("/agent/get", summary="获取应用详情")
async def get_agent_detail(
    access_token: str = Query(..., description="access_token"),
    agentid: Optional[int] = Query(None, description="应用ID，不填则使用配置文件")
):
    """
    获取应用详情

    获取指定应用的详细信息
    """
    settings = get_settings()
    agent_id = agentid or (int(settings.wecom_agent_id) if settings.wecom_agent_id else None)

    if not agent_id:
        raise HTTPException(
            status_code=400,
            detail="请提供agentid参数，或在.env中配置WECOM_AGENT_ID"
        )

    url = f"{WECOM_API_BASE}/agent/get"
    params = {
        "access_token": access_token,
        "agentid": agent_id
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        result = response.json()

    if result.get("errcode", 0) != 0:
        return {
            "code": result.get("errcode"),
            "message": result.get("errmsg"),
            "data": None
        }

    return {
        "code": 0,
        "message": "success",
        "data": {
            "agentid": result.get("agentid"),
            "name": result.get("name"),
            "description": result.get("description"),
            "allow_userinfos": result.get("allow_userinfos"),
            "allow_partys": result.get("allow_partys"),
            "close": result.get("close"),
            "redirect_domain": result.get("redirect_domain"),
            "isreportenter": result.get("isreportenter"),
            "home_url": result.get("home_url")
        }
    }


# ==================== 快速测试 ====================

@router.get("/quick-test", summary="快速测试（一键获取token并发送消息）")
async def quick_test(
    userid: str = Query(..., description="接收消息的成员UserID"),
    message: str = Query("这是一条测试消息，来自极修匠API调试接口", description="消息内容")
):
    """
    快速测试

    一键完成获取token并发送测试消息，方便快速验证配置是否正确
    """
    settings = get_settings()

    # 检查配置
    if not settings.wecom_corp_id or not settings.wecom_secret:
        raise HTTPException(
            status_code=400,
            detail="请先在.env中配置WECOM_CORP_ID和WECOM_SECRET"
        )

    # 第一步：获取token
    token_url = f"{WECOM_API_BASE}/gettoken"
    token_params = {
        "corpid": settings.wecom_corp_id,
        "corpsecret": settings.wecom_secret
    }

    async with httpx.AsyncClient() as client:
        token_response = await client.get(token_url, params=token_params)
        token_result = token_response.json()

    if token_result.get("errcode", 0) != 0:
        return {
            "code": token_result.get("errcode"),
            "message": f"获取token失败: {token_result.get('errmsg')}",
            "step": "get_token",
            "data": None
        }

    access_token = token_result.get("access_token")

    # 第二步：发送消息
    msg_url = f"{WECOM_API_BASE}/message/send"
    msg_params = {"access_token": access_token}
    msg_payload = {
        "touser": userid,
        "msgtype": "text",
        "agentid": int(settings.wecom_agent_id) if settings.wecom_agent_id else 1000002,
        "text": {"content": message},
        "safe": 0
    }

    async with httpx.AsyncClient() as client:
        msg_response = await client.post(msg_url, params=msg_params, json=msg_payload)
        msg_result = msg_response.json()

    if msg_result.get("errcode", 0) != 0:
        return {
            "code": msg_result.get("errcode"),
            "message": f"发送消息失败: {msg_result.get('errmsg')}",
            "step": "send_message",
            "data": {
                "access_token": access_token[:20] + "...",
                "hint": "token获取成功，但发送消息失败"
            }
        }

    return {
        "code": 0,
        "message": "success",
        "data": {
            "step1_token": "获取成功",
            "step2_message": "发送成功",
            "msgid": msg_result.get("msgid"),
            "hint": f"测试消息已发送给用户 {userid}"
        }
    }


# ==================== 配置状态 ====================

@router.get("/config/status", summary="获取当前配置状态")
async def get_config_status():
    """
    获取当前配置状态

    检查.env中的企业微信配置是否已填写
    """
    settings = get_settings()

    return {
        "code": 0,
        "message": "success",
        "data": {
            "wecom_corp_id": {
                "configured": bool(settings.wecom_corp_id),
                "value": settings.wecom_corp_id[:8] + "..." if settings.wecom_corp_id else ""
            },
            "wecom_agent_id": {
                "configured": bool(settings.wecom_agent_id),
                "value": settings.wecom_agent_id if settings.wecom_agent_id else ""
            },
            "wecom_secret": {
                "configured": bool(settings.wecom_secret),
                "value": "***已配置***" if settings.wecom_secret else ""
            },
            "hint": "如未配置，请在backend/.env文件中添加相应配置"
        }
    }
