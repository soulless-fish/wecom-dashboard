"""
企业微信回调接口
用于接收企业微信的事件推送和数据回调
"""
import secrets
import time
from fastapi import APIRouter, Request, Query, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
import logging

from app.config import get_settings, Settings
from app.core.wecom_crypto import (
    WeComCrypto,
    WeComCryptoError,
    extract_encrypt_from_xml,
    build_encrypted_reply_xml,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/callback/command")
async def verify_command_callback(
    msg_signature: str = Query(..., description="消息签名"),
    timestamp: str = Query(..., description="时间戳"),
    nonce: str = Query(..., description="随机数"),
    echostr: str = Query(..., description="加密的随机字符串"),
    settings: Settings = Depends(get_settings),
):
    """
    指令回调URL验证（GET请求）
    """
    logger.info(f"[Command Callback] URL验证请求: timestamp={timestamp}, nonce={nonce}")

    mode = (settings.wecom_callback_mode or "tool").lower()
    if mode in {"tool", "plain"}:
        return PlainTextResponse(content=echostr)

    if mode != "secure":
        raise HTTPException(status_code=500, detail="invalid wecom_callback_mode")

    if not settings.wecom_callback_token or not settings.wecom_callback_aes_key:
        raise HTTPException(status_code=500, detail="missing callback crypto settings")

    crypto = WeComCrypto(
        token=settings.wecom_callback_token,
        encoding_aes_key=settings.wecom_callback_aes_key,
        corp_id=settings.wecom_corp_id,
    )
    try:
        crypto.verify_signature(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypt=echostr,
        )
        plain = crypto.decrypt(encrypt=echostr)
    except WeComCryptoError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return PlainTextResponse(content=plain)


@router.post("/callback/command")
async def handle_command_callback(
    request: Request,
    msg_signature: str = Query(None, description="消息签名"),
    timestamp: str = Query(None, description="时间戳"),
    nonce: str = Query(None, description="随机数"),
    settings: Settings = Depends(get_settings),
):
    """
    指令回调（POST请求）
    接收企业微信的指令事件，如：菜单点击、进入应用等
    """
    body = await request.body()
    body_str = body.decode("utf-8", errors="replace")

    mode = (settings.wecom_callback_mode or "tool").lower()
    if mode == "secure":
        if not (msg_signature and timestamp and nonce):
            raise HTTPException(status_code=400, detail="missing signature params")
        if not settings.wecom_callback_token or not settings.wecom_callback_aes_key:
            raise HTTPException(status_code=500, detail="missing callback crypto settings")

        crypto = WeComCrypto(
            token=settings.wecom_callback_token,
            encoding_aes_key=settings.wecom_callback_aes_key,
            corp_id=settings.wecom_corp_id,
        )
        try:
            encrypt = extract_encrypt_from_xml(body_str)
            crypto.verify_signature(
                msg_signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
                encrypt=encrypt,
            )
            plain = crypto.decrypt(encrypt=encrypt)
        except WeComCryptoError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        logger.info(f"[Command Callback] 收到指令回调(已解密): {plain[:500]}")
        reply_nonce = secrets.token_hex(8)
        reply_ts = str(int(time.time()))
        reply_xml = build_encrypted_reply_xml(
            crypto=crypto,
            plaintext="success",
            timestamp=reply_ts,
            nonce=reply_nonce,
        )
        return Response(content=reply_xml, media_type="application/xml")

    logger.info(f"[Command Callback] 收到指令回调: {body_str[:500]}")

    # TODO: 根据实际业务需求处理指令回调
    # 内网穿透模式下收到的是明文XML或JSON数据

    return PlainTextResponse(content="success")


@router.get("/callback/data")
async def verify_data_callback(
    msg_signature: str = Query(..., description="消息签名"),
    timestamp: str = Query(..., description="时间戳"),
    nonce: str = Query(..., description="随机数"),
    echostr: str = Query(..., description="加密的随机字符串"),
    settings: Settings = Depends(get_settings),
):
    """
    数据回调URL验证（GET请求）
    """
    logger.info(f"[Data Callback] URL验证请求: timestamp={timestamp}, nonce={nonce}")

    mode = (settings.wecom_callback_mode or "tool").lower()
    if mode in {"tool", "plain"}:
        return PlainTextResponse(content=echostr)

    if mode != "secure":
        raise HTTPException(status_code=500, detail="invalid wecom_callback_mode")

    if not settings.wecom_callback_token or not settings.wecom_callback_aes_key:
        raise HTTPException(status_code=500, detail="missing callback crypto settings")

    crypto = WeComCrypto(
        token=settings.wecom_callback_token,
        encoding_aes_key=settings.wecom_callback_aes_key,
        corp_id=settings.wecom_corp_id,
    )
    try:
        crypto.verify_signature(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypt=echostr,
        )
        plain = crypto.decrypt(encrypt=echostr)
    except WeComCryptoError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return PlainTextResponse(content=plain)


@router.post("/callback/data")
async def handle_data_callback(
    request: Request,
    msg_signature: str = Query(None, description="消息签名"),
    timestamp: str = Query(None, description="时间戳"),
    nonce: str = Query(None, description="随机数"),
    settings: Settings = Depends(get_settings),
):
    """
    数据回调（POST请求）
    接收企业微信的数据事件，如：客户变更、消息等
    """
    body = await request.body()
    body_str = body.decode("utf-8", errors="replace")

    mode = (settings.wecom_callback_mode or "tool").lower()
    if mode == "secure":
        if not (msg_signature and timestamp and nonce):
            raise HTTPException(status_code=400, detail="missing signature params")
        if not settings.wecom_callback_token or not settings.wecom_callback_aes_key:
            raise HTTPException(status_code=500, detail="missing callback crypto settings")

        crypto = WeComCrypto(
            token=settings.wecom_callback_token,
            encoding_aes_key=settings.wecom_callback_aes_key,
            corp_id=settings.wecom_corp_id,
        )
        try:
            encrypt = extract_encrypt_from_xml(body_str)
            crypto.verify_signature(
                msg_signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
                encrypt=encrypt,
            )
            plain = crypto.decrypt(encrypt=encrypt)
        except WeComCryptoError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        logger.info(f"[Data Callback] 收到数据回调(已解密): {plain[:500]}")
        reply_nonce = secrets.token_hex(8)
        reply_ts = str(int(time.time()))
        reply_xml = build_encrypted_reply_xml(
            crypto=crypto,
            plaintext="success",
            timestamp=reply_ts,
            nonce=reply_nonce,
        )
        return Response(content=reply_xml, media_type="application/xml")

    logger.info(f"[Data Callback] 收到数据回调: {body_str[:500]}")

    # TODO: 根据实际业务需求处理数据回调
    # 内网穿透模式下收到的是明文XML或JSON数据

    return PlainTextResponse(content="success")
