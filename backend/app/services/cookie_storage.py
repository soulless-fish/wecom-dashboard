"""
Cookie持久化存储模块

将Cookie配置存储到JSON文件中，确保服务重启后Cookie不丢失。
存储位置：
  - 上翻收益Cookie: backend/cookie_config.json
  - 来客后台Cookie: backend/life_data_cookie_config.json
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Cookie存储文件路径（backend目录下）
COOKIE_FILE = Path(__file__).resolve().parents[2] / "cookie_config.json"
LIFE_DATA_COOKIE_FILE = Path(__file__).resolve().parents[2] / "life_data_cookie_config.json"


def load_cookie() -> dict:
    """
    从JSON文件加载Cookie配置（上翻收益 life.douyin.com）

    Returns:
        dict: {"cookie": str, "account_id": str, "csrf_token": str}
    """
    default = {"cookie": "", "account_id": "", "csrf_token": ""}

    if not COOKIE_FILE.exists():
        logger.info(f"Cookie配置文件不存在: {COOKIE_FILE}")
        return default

    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"已从文件加载Cookie配置, account_id={data.get('account_id', '')}")
        return {
            "cookie": data.get("cookie", ""),
            "account_id": data.get("account_id", ""),
            "csrf_token": data.get("csrf_token", "")
        }
    except Exception as e:
        logger.error(f"读取Cookie配置文件失败: {e}")
        return default


def save_cookie(cookie: str, account_id: str, csrf_token: str = "") -> bool:
    """
    将Cookie配置保存到JSON文件（上翻收益 life.douyin.com）

    Args: v
        cookie: life.douyin.com的Cookie
        account_id: 抖音账号ID
        csrf_token: CSRF Token

    Returns:
        bool: 是否保存成功
    """
    try:
        data = {
            "cookie": cookie,
            "account_id": account_id,
            "csrf_token": csrf_token
        }
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Cookie配置已保存到文件, account_id={account_id}")
        return True
    except Exception as e:
        logger.error(f"保存Cookie配置文件失败: {e}")
        return False


def load_life_data_cookie() -> dict:
    """
    从JSON文件或环境变量加载来客后台Cookie配置（life-data.cn）

    优先从JSON文件加载，如果文件不存在则从环境变量读取。

    Returns:
        dict: {"cookie": str, "life_account_id": str, "csrf_token": str}
    """
    default = {"cookie": "", "life_account_id": "", "csrf_token": ""}

    # 优先从JSON文件加载
    if LIFE_DATA_COOKIE_FILE.exists():
        try:
            with open(LIFE_DATA_COOKIE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"已从文件加载来客后台Cookie配置, life_account_id={data.get('life_account_id', '')}")
            return {
                "cookie": data.get("cookie", ""),
                "life_account_id": data.get("life_account_id", ""),
                "csrf_token": data.get("csrf_token", "")
            }
        except Exception as e:
            logger.error(f"读取来客后台Cookie配置文件失败: {e}")

    # JSON文件不存在或读取失败，尝试从环境变量加载
    env_cookie = os.environ.get("LIFE_DATA_COOKIE", "")
    env_account_id = os.environ.get("LIFE_DATA_ACCOUNT_ID", "")
    env_csrf_token = os.environ.get("LIFE_DATA_CSRF_TOKEN", "")

    if env_cookie and env_account_id:
        logger.info(f"已从环境变量加载来客后台Cookie配置, life_account_id={env_account_id}")
        return {
            "cookie": env_cookie,
            "life_account_id": env_account_id,
            "csrf_token": env_csrf_token
        }

    logger.info(f"来客后台Cookie配置文件不存在且环境变量未配置: {LIFE_DATA_COOKIE_FILE}")
    return default


def save_life_data_cookie(cookie: str, life_account_id: str, csrf_token: str = "") -> bool:
    """
    将来客后台Cookie配置保存到JSON文件（life-data.cn）

    Args:
        cookie: life-data.cn的Cookie
        life_account_id: 来客商户账户ID
        csrf_token: CSRF Token

    Returns:
        bool: 是否保存成功
    """
    try:
        data = {
            "cookie": cookie,
            "life_account_id": life_account_id,
            "csrf_token": csrf_token
        }
        with open(LIFE_DATA_COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"来客后台Cookie配置已保存到文件, life_account_id={life_account_id}")
        return True
    except Exception as e:
        logger.error(f"保存来客后台Cookie配置文件失败: {e}")
        return False
