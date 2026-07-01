"""
数据库模型定义

使用SQLAlchemy定义数据库表结构
支持MySQL数据库
"""
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Text, Numeric, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
from pathlib import Path

# 加载.env文件
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# MySQL数据库配置，公开版本不提供默认账号密码，部署时请通过环境变量或 .env 配置。
MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = os.environ.get("MYSQL_PORT", "3306")
MYSQL_USER = os.environ.get("MYSQL_USER", "")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "jiuxiujiang")

# MySQL连接字符串 (使用pymysql驱动)
DATABASE_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"

# 创建引擎
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=3600)

# 创建Session工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基类
Base = declarative_base()


class StorePerformance(Base):
    """
    门店业绩数据表

    存储每个门店的月度业绩数据：
    - 门店名称
    - 上翻收益(元)
    - 直播时长(格式化为X天X小时X分钟)
    - 视频数量
    """
    __tablename__ = "store_performance"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 核心数据字段
    poi_name = Column(String(255), nullable=False, comment="门店名称")
    gmv_yuan = Column(Numeric(12, 2), default=0.00, comment="上翻收益(元)")  # 使用Numeric保证精度，保留2位小数
    live_duration_formatted = Column(String(50), default="0天0小时0分钟", comment="直播时长(格式化)")
    live_duration_seconds = Column(Integer, default=0, comment="直播时长(秒)")
    video_count = Column(Integer, default=0, comment="视频数量")
    video_cnt_1d = Column(Integer, default=0, comment="新发布门店关联视频数(门店概览接口)")
    poi_score = Column(Numeric(3, 2), default=0.00, comment="门店评分")
    verify_amount_realtime = Column(String(50), default="¥0.00", comment="门店核销金额(当月实时)")
    verify_cert_cnt_realtime = Column(Integer, default=0, comment="门店核销券数(当月实时)")
    verify_amount = Column(String(50), default="¥0.00", comment="门店核销金额(近30天)")
    verify_cert_cnt = Column(Integer, default=0, comment="门店核销券数(近30天)")

    # 辅助字段
    poi_id = Column(String(50), nullable=True, comment="门店ID")
    data_month = Column(String(7), nullable=False, comment="数据月份(YYYY-MM)")
    data_start_day = Column(Integer, nullable=True, comment="数据开始日期(日)")
    data_end_day = Column(Integer, nullable=True, comment="数据结束日期(日)")

    # 时间戳
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<StorePerformance(poi_name='{self.poi_name}', gmv={self.gmv_yuan}, month='{self.data_month}')>"


class DataSyncLog(Base):
    """
    数据同步日志表

    记录每次数据同步的状态
    """
    __tablename__ = "data_sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_type = Column(String(50), nullable=False, comment="同步类型")
    data_month = Column(String(7), nullable=False, comment="同步的数据月份")
    total_records = Column(Integer, default=0, comment="同步记录数")
    status = Column(String(20), default="pending", comment="同步状态(pending/running/success/failed)")
    error_message = Column(Text, nullable=True, comment="错误信息")
    started_at = Column(DateTime, default=datetime.now, comment="开始时间")
    finished_at = Column(DateTime, nullable=True, comment="完成时间")

    def __repr__(self):
        return f"<DataSyncLog(type='{self.sync_type}', month='{self.data_month}', status='{self.status}')>"


class FankeBuyerOrderItem(Base):
    """
    凡科买家订单商品明细表

    保存近30天凡科订单中的买家、收货信息和商品明细。
    每一行对应一个订单商品明细，便于后续按手机号匹配门店并汇总电池/电芯金额。
    """
    __tablename__ = "fanke_buyer_order_items"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 同步窗口字段，用于每天删除旧的近30天数据后重写。
    sync_start_date = Column(Date, nullable=False, comment="同步开始日期")
    sync_end_date = Column(Date, nullable=False, comment="同步结束日期")

    # 订单主信息。
    order_source = Column(String(32), default="normal", index=True, comment="订单来源代码")
    order_source_name = Column(String(64), default="普通商品订单", comment="订单来源名称")
    order_id = Column(String(64), nullable=False, comment="凡科订单号")
    merchant_id = Column(String(64), default="", index=True, comment="入驻商户ID")
    direct_merchant_id = Column(String(64), default="", comment="直营商户ID")
    order_status = Column(String(64), default="", comment="订单状态")
    order_price = Column(Numeric(12, 2), default=0.00, comment="订单总金额")
    pay_time = Column(String(32), default="", comment="支付时间")
    created_time = Column(String(32), default="", comment="订单创建时间")
    modified_time = Column(String(32), default="", comment="订单更新时间")

    # 买家和收货信息。
    buyer_id = Column(String(64), default="", comment="买家ID")
    buyer_acct = Column(String(128), default="", comment="买家账号")
    buyer_nickname = Column(String(255), default="", comment="买家昵称")
    consignee_name = Column(String(255), default="", comment="收货人")
    mobile = Column(String(50), default="", comment="订单手机号")
    telephone = Column(String(50), default="", comment="订单电话")
    normalized_mobile = Column(String(50), default="", index=True, comment="规整后的订单手机号")
    normalized_telephone = Column(String(50), default="", index=True, comment="规整后的订单电话")
    province = Column(String(64), default="", comment="省")
    city = Column(String(64), default="", comment="市")
    district = Column(String(64), default="", comment="区县")
    town = Column(String(128), default="", comment="街道乡镇")
    street = Column(String(512), default="", comment="详细地址")

    # 商品明细信息。
    item_id = Column(String(64), default="", comment="订单商品明细ID")
    product_id = Column(String(64), default="", comment="商品ID")
    product_code = Column(String(128), default="", comment="商品编码")
    product_title = Column(String(255), default="", comment="商品名称")
    sku_properties = Column(String(255), default="", comment="SKU规格")
    quantity = Column(Integer, default=0, comment="购买数量")
    unit_price = Column(Numeric(12, 2), default=0.00, comment="商品单价")
    item_amount = Column(Numeric(12, 2), default=0.00, comment="商品明细金额")
    refund_status = Column(String(64), default="", comment="售后状态")
    item_status = Column(String(64), default="", comment="商品状态")

    # 分类匹配结果。
    product_category = Column(String(32), default="", index=True, comment="商品分类代码")
    product_category_name = Column(String(64), default="", comment="商品分类名称")
    match_rule = Column(String(64), default="", comment="分类匹配规则")
    match_key = Column(String(255), default="", comment="分类匹配键")

    # 原始数据保留，便于后续排查和补充字段。
    raw_order_json = Column(Text, nullable=True, comment="原始订单JSON")
    raw_item_json = Column(Text, nullable=True, comment="原始商品明细JSON")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<FankeBuyerOrderItem(order_id='{self.order_id}', product_code='{self.product_code}')>"


class DouyinOfficialOrder(Base):
    """
    抖音开放平台订单明细表

    保存通过抖音官方订单查询接口同步下来的订单数据。
    每一行对应一个抖音订单，侧边栏按 poi_id / intention_poi_id 聚合展示门店近30天订单。
    """
    __tablename__ = "douyin_official_orders"
    __table_args__ = (
        UniqueConstraint("account_id", "order_id", name="uq_douyin_account_order"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 同步窗口字段，用于判断本地订单数据覆盖范围。
    sync_start_date = Column(Date, nullable=False, comment="同步开始日期")
    sync_end_date = Column(Date, nullable=False, comment="同步结束日期")

    # 账号和订单主信息。
    account_id = Column(String(64), nullable=False, index=True, comment="抖音来客商户账号ID")
    account_name = Column(String(255), default="", comment="抖音来客商户名称")
    order_id = Column(String(64), nullable=False, index=True, comment="抖音订单ID")
    order_status = Column(Integer, default=0, index=True, comment="抖音订单状态")
    order_type = Column(Integer, default=0, comment="抖音订单类型")

    # 门店信息。trade 接口通常返回 poi_id，akte 接口通常返回 intention_poi_id。
    poi_id = Column(String(64), default="", index=True, comment="抖音门店ID")
    intention_poi_id = Column(String(64), default="", index=True, comment="下单意向门店ID")
    poi_name = Column(String(255), default="", comment="抖音门店名称")

    # 金额字段，接口金额单位为分，侧边栏展示时使用元。
    pay_amount_fen = Column(Integer, default=0, comment="实付金额(分)")
    original_amount_fen = Column(Integer, default=0, comment="原始金额(分)")
    receipt_amount_fen = Column(Integer, default=0, comment="实收金额(分)")
    pay_amount_yuan = Column(Numeric(12, 2), default=0.00, comment="实付金额(元)")
    receipt_amount_yuan = Column(Numeric(12, 2), default=0.00, comment="实收金额(元)")

    # 商品字段，优先保存订单中的第一条商品，后续如果需要多商品可再拆子表。
    sku_id = Column(String(64), default="", index=True, comment="SKU ID")
    sku_name = Column(String(255), default="", comment="SKU名称")
    product_id = Column(String(64), default="", index=True, comment="商品ID")
    product_name = Column(String(255), default="", comment="商品名称")
    third_sku_id = Column(String(64), default="", comment="第三方SKU ID")
    sub_order_id = Column(String(128), default="", index=True, comment="子订单ID")

    # 商品分类字段，用于侧边栏统计已核销电池/电芯订单数。
    product_category = Column(String(32), default="", index=True, comment="商品分类代码")
    product_category_name = Column(String(64), default="", comment="商品分类名称")
    product_root_category = Column(String(32), default="", index=True, comment="商品大类(battery/cell)")
    product_match_rule = Column(String(64), default="", comment="商品分类匹配规则")

    # 券码摘要字段，不保存明文券码。
    certificate_count = Column(Integer, default=0, comment="订单券码数量")
    verified_certificate_count = Column(Integer, default=0, comment="已核销券码数量")
    certificate_status_summary = Column(String(255), default="", comment="券码状态摘要")

    # 时间字段。
    create_order_time = Column(DateTime, nullable=True, index=True, comment="订单创建时间")
    pay_time = Column(DateTime, nullable=True, index=True, comment="支付时间")
    verify_time = Column(DateTime, nullable=True, index=True, comment="券码核销时间")
    update_order_time = Column(DateTime, nullable=True, index=True, comment="订单更新时间")

    # 原始响应脱敏后保存，便于后续补字段和排查。
    raw_order_json = Column(Text, nullable=True, comment="脱敏后的原始订单JSON")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<DouyinOfficialOrder(order_id='{self.order_id}', poi_id='{self.poi_id}')>"


class DouyinComputerCleaningStatus(Base):
    """
    抖音电脑清灰团购开通状态表

    独立保存每个门店是否关联指定的电脑清灰团购商品。
    该数据来自抖音开放平台商品线上数据列表接口，按 7 天频率更新，
    不随每天重写的 store_performance 月度业绩表一起删除。
    """
    __tablename__ = "douyin_computer_cleaning_status"
    __table_args__ = (
        UniqueConstraint("account_id", "poi_id", name="uq_douyin_cleaning_account_poi"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    account_id = Column(String(64), nullable=False, index=True, comment="抖音来客商户账号ID")
    poi_id = Column(String(64), nullable=False, index=True, comment="抖音门店ID")
    is_opened = Column(Integer, default=0, index=True, comment="电脑清灰是否已开通，1是0否")

    matched_product_ids = Column(Text, nullable=True, comment="命中的电脑清灰商品ID列表JSON")
    matched_product_names = Column(Text, nullable=True, comment="命中的电脑清灰商品名称列表JSON")
    matched_product_count = Column(Integer, default=0, comment="命中的电脑清灰商品数量")
    source_status = Column(String(32), default="success", comment="最近一次同步状态")
    last_sync_at = Column(DateTime, nullable=True, index=True, comment="最近一次抖音商品同步时间")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<DouyinComputerCleaningStatus(poi_id='{self.poi_id}', is_opened={self.is_opened})>"


def init_db():
    """初始化数据库，创建所有表"""
    Base.metadata.create_all(bind=engine)
    print(f"MySQL数据库已初始化: {MYSQL_DATABASE}@{MYSQL_HOST}:{MYSQL_PORT}")


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def format_duration(seconds: int) -> str:
    """
    将秒数转换为 "X天X小时X分钟" 格式

    Args:
        seconds: 总秒数

    Returns:
        格式化的时长字符串
    """
    if seconds <= 0:
        return "0天0小时0分钟"

    days = seconds // 86400
    remaining = seconds % 86400
    hours = remaining // 3600
    remaining = remaining % 3600
    minutes = remaining // 60

    return f"{days}天{hours}小时{minutes}分钟"


def parse_duration_to_seconds(duration_str) -> int:
    """
    将时长字符串或数字转换为秒数

    支持格式:
    - 数字(直接作为秒数)
    - "X小时X分X秒" 或 "X小时X分钟X秒"

    Args:
        duration_str: 时长字符串或数字

    Returns:
        总秒数
    """
    if duration_str is None:
        return 0

    if isinstance(duration_str, (int, float)):
        return int(duration_str)

    if isinstance(duration_str, str):
        # 尝试直接转换数字
        try:
            return int(duration_str)
        except ValueError:
            pass

        # 解析中文格式
        import re

        total_seconds = 0

        # 匹配小时
        hour_match = re.search(r'(\d+)\s*小时', duration_str)
        if hour_match:
            total_seconds += int(hour_match.group(1)) * 3600

        # 匹配分钟
        min_match = re.search(r'(\d+)\s*分', duration_str)
        if min_match:
            total_seconds += int(min_match.group(1)) * 60

        # 匹配秒
        sec_match = re.search(r'(\d+)\s*秒', duration_str)
        if sec_match:
            total_seconds += int(sec_match.group(1))

        return total_seconds

    return 0
