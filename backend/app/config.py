from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """应用配置"""
    # 应用信息
    app_name: str = "极修匠"
    debug: bool = False

    # 企业微信配置
    wecom_corp_id: str = ""
    wecom_agent_id: str = ""
    wecom_secret: str = ""
    wecom_external_contact_secret: str = ""  # 客户联系secret（用于调用客户群API）

    # 企业微信回调配置
    wecom_callback_mode: str = "tool"
    wecom_callback_token: str = ""
    wecom_callback_aes_key: str = ""

    # 抖音开放平台配置
    douyin_client_key: str = ""
    douyin_client_secret: str = ""
    douyin_account_id: str = ""

    # 来客后台数据配置
    life_data_cookie: str = ""
    life_data_account_id: str = ""
    life_data_csrf_token: str = ""

    # 凡科商城API配置
    fanke_client_id: str = ""
    fanke_client_secret: str = ""
    fanke_platform_code: str = "FKW"
    fanke_return_url: str = ""

    # Cookie失效告警配置
    cookie_alert_userid: str = ""  # 接收Cookie失效通知的管理员userid

    # 服务配置
    api_prefix: str = "/api/v1"
    public_base_url: str = "http://localhost:5173"
    cors_origins: list[str] = ["*"]
    internal_api_token: str = ""  # 内部服务调用令牌；为空时不启用校验

    # Redis配置
    redis_url: str = "redis://localhost:6379/0"

    # MySQL数据库配置
    mysql_host: str = "localhost"
    mysql_port: str = "3306"
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_database: str = "jiuxiujiang"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
