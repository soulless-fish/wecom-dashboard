"""
凡科商城 token 持久化存储

将凡科 OAuth 授权结果单独保存，避免和抖音/来客 Cookie 配置混用。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict


logger = logging.getLogger(__name__)

# token 文件放在 backend 目录，和现有 cookie_config.json 同级。
FANKE_TOKEN_FILE = Path(__file__).resolve().parents[2] / "fanke_token_config.json"


def load_fanke_token() -> Dict[str, Any]:
    """
    读取凡科 token 配置。

    返回默认空结构，调用方可以直接判断 access_token 是否存在。
    """
    default = {
        "shop_id": "",
        "shop_name": "",
        "user_id": "",
        "platform_code": "",
        "access_token": "",
        "access_token_expires_at": "",
        "refresh_token": "",
        "refresh_token_expires_at": "",
        "token_type": "",
        "updated_at": "",
    }

    if not FANKE_TOKEN_FILE.exists():
        logger.info("凡科 token 文件不存在: %s", FANKE_TOKEN_FILE)
        return default

    try:
        with open(FANKE_TOKEN_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        result = default.copy()
        result.update(data if isinstance(data, dict) else {})
        logger.info("已加载凡科 token 配置, shop_id=%s", result.get("shop_id", ""))
        return result
    except Exception as exc:
        logger.error("读取凡科 token 配置失败: %s", exc)
        return default


def save_fanke_token(data: Dict[str, Any]) -> bool:
    """
    保存凡科 token 配置。

    Args:
        data: 已整理好的凡科授权信息

    Returns:
        是否保存成功
    """
    try:
        with open(FANKE_TOKEN_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        logger.info("凡科 token 配置已保存, shop_id=%s", data.get("shop_id", ""))
        return True
    except Exception as exc:
        logger.error("保存凡科 token 配置失败: %s", exc)
        return False
