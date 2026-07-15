"""
巨量引擎授权凭证本地存储。

本文件只负责把已换取成功的授权结果保存到 JLYQ 目录下，避免和企业微信、
抖音来客、凡科等已有平台的配置混用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

MODULE_ROOT = Path(__file__).resolve().parent
TOKEN_FILE = MODULE_ROOT / "jlyq_token_config.json"
CALLBACK_LOG_FILE = MODULE_ROOT / "jlyq_oauth_callback_log.jsonl"


def mask_value(value: str, keep_start: int = 6, keep_end: int = 4) -> str:
    """隐藏敏感字段，只保留开头和结尾便于排查。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep_start + keep_end:
        return "*" * len(text)
    return f"{text[:keep_start]}...{text[-keep_end:]}"


def utc_now() -> datetime:
    """返回带时区的当前 UTC 时间。"""
    return datetime.now(timezone.utc)


def seconds_to_expire_time(seconds: Any) -> str:
    """把接口返回的剩余秒数转换为 ISO 时间。"""
    try:
        value = int(seconds or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return ""
    return (utc_now() + timedelta(seconds=value)).isoformat()


def default_token_record() -> dict[str, Any]:
    """返回默认授权记录结构。"""
    return {
        "authorized": False,
        "access_token": "",
        "refresh_token": "",
        "access_token_expires_at": "",
        "refresh_token_expires_at": "",
        "advertiser_ids": [],
        "state": "",
        "request_id": "",
        "updated_at": "",
    }


def load_token_record() -> dict[str, Any]:
    """读取巨量引擎授权记录。"""
    if not TOKEN_FILE.exists():
        return default_token_record()

    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("读取巨量引擎授权记录失败: %s", exc)
        return default_token_record()

    result = default_token_record()
    if isinstance(data, dict):
        result.update(data)
    result["authorized"] = bool(result.get("access_token"))
    return result


def build_token_record(token_response: dict[str, Any], state: str = "") -> dict[str, Any]:
    """把巨量引擎 access_token 接口响应整理成可保存结构。"""
    data = token_response.get("data") if isinstance(token_response.get("data"), dict) else token_response
    return {
        "authorized": bool(data.get("access_token")),
        "access_token": str(data.get("access_token") or ""),
        "refresh_token": str(data.get("refresh_token") or ""),
        "access_token_expires_at": seconds_to_expire_time(data.get("expires_in")),
        "refresh_token_expires_at": seconds_to_expire_time(data.get("refresh_token_expires_in")),
        "advertiser_ids": data.get("advertiser_ids") or [],
        "state": state or "",
        "request_id": str(data.get("request_id") or token_response.get("request_id") or ""),
        "updated_at": utc_now().isoformat(),
    }


def save_token_record(record: dict[str, Any]) -> bool:
    """保存巨量引擎授权记录。"""
    try:
        TOKEN_FILE.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("保存巨量引擎授权记录失败: %s", exc)
        return False
    return True


def append_callback_log(payload: dict[str, Any]) -> None:
    """追加 OAuth 回调日志，日志中不保存 auth_code 原文。"""
    safe_payload = dict(payload)
    if "auth_code" in safe_payload:
        safe_payload["auth_code"] = mask_value(str(safe_payload["auth_code"]))
    safe_payload["logged_at"] = utc_now().isoformat()
    CALLBACK_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CALLBACK_LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(safe_payload, ensure_ascii=False) + "\n")
