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

# MySQL数据库配置
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


class DouyinPoiAccountBinding(Base):
    """
    抖音门店绑定经营抖音号表

    保存 goodlife/v1/shop/poi/query/ 返回的子机构经营号关系，侧边栏按 poi_id 读取门店绑定的抖音号。
    """
    __tablename__ = "douyin_poi_account_bindings"
    __table_args__ = (
        UniqueConstraint("account_id", "poi_id", name="uq_douyin_poi_account_binding"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    account_id = Column(String(64), nullable=False, index=True, comment="抖音来客商户账号ID")
    poi_id = Column(String(64), nullable=False, index=True, comment="抖音门店ID")
    poi_name = Column(String(255), default="", index=True, comment="抖音门店名称")

    poi_account_id = Column(String(64), default="", index=True, comment="门店绑定经营抖音号ID")
    poi_account_name = Column(String(255), default="", index=True, comment="门店绑定经营抖音号昵称")
    poi_account_type = Column(String(64), default="", comment="绑定类型，例如 SUB_ORG")

    parent_account_id = Column(String(64), default="", index=True, comment="父级账号ID")
    parent_account_name = Column(String(255), default="", comment="父级账号名称")
    parent_account_type = Column(String(64), default="", comment="父级账号类型")

    root_account_id = Column(String(64), default="", index=True, comment="根账号ID")
    root_account_name = Column(String(255), default="", comment="根账号名称")
    root_account_type = Column(String(64), default="", comment="根账号类型")

    raw_json = Column(Text, nullable=True, comment="官方接口原始门店账号关系JSON")
    source_status = Column(String(32), default="success", index=True, comment="最近一次同步状态")
    last_sync_at = Column(DateTime, nullable=True, index=True, comment="最近一次同步时间")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<DouyinPoiAccountBinding(poi_id='{self.poi_id}', poi_account_name='{self.poi_account_name}')>"


class DouyinShopBusinessStatus(Base):
    """
    抖音门店营业状态表

    保存来客门店关系接口返回的门店营业状态，侧边栏按 poi_id 优先读取，
    poi_id 缺失或跨源不一致时再按规整后的门店名称兜底匹配。
    """
    __tablename__ = "douyin_shop_business_status"
    __table_args__ = (
        UniqueConstraint("account_id", "poi_id", name="uq_douyin_shop_business_account_poi"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    account_id = Column(String(64), nullable=False, index=True, comment="抖音来客商户账号ID")
    poi_id = Column(String(64), nullable=False, index=True, comment="抖音门店ID")
    poi_name = Column(String(255), default="", index=True, comment="抖音门店名称")
    normalized_poi_name = Column(String(255), default="", index=True, comment="规整后的门店名称")
    poi_remark_name = Column(String(255), default="", comment="来客门店备注名")
    poi_life_account_id = Column(String(64), default="", index=True, comment="门店生活服务账号ID")

    business_status_code = Column(Integer, default=0, index=True, comment="营业状态码")
    business_status_text = Column(String(32), default="", index=True, comment="营业状态文案")
    source_status = Column(String(32), default="success", index=True, comment="最近一次同步状态")
    raw_json = Column(Text, nullable=True, comment="来客门店关系接口原始JSON")
    last_sync_at = Column(DateTime, nullable=True, index=True, comment="最近一次同步时间")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<DouyinShopBusinessStatus(poi_id='{self.poi_id}', status='{self.business_status_text}')>"


class DouyinCraftsmanBinding(Base):
    """
    抖音职人号绑定信息表

    保存开放平台职人绑定接口返回的商家职人号和个人职人号，详情页按门店名称或门店ID展示。
    """
    __tablename__ = "douyin_craftsman_bindings"
    __table_args__ = (
        UniqueConstraint("account_id", "craftsman_type", "craftsman_uid", "poi_id", name="uq_douyin_craftsman_binding"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    account_id = Column(String(64), nullable=False, index=True, comment="抖音来客商户总户账号ID")
    source_account_id = Column(String(64), default="", index=True, comment="接口返回的职人所属账号ID")
    poi_id = Column(String(64), default="", index=True, comment="职人绑定门店ID")
    poi_name = Column(String(255), default="", index=True, comment="职人绑定门店名称")

    craftsman_type = Column(String(32), nullable=False, index=True, comment="职人号分类，merchant商家职人号，personal个人职人号")
    craftsman_uid = Column(String(64), default="", index=True, comment="职人唯一ID")
    aweme_id = Column(String(64), default="", index=True, comment="抖音号ID或短ID")
    aweme_name = Column(String(255), default="", index=True, comment="抖音号昵称")

    operator_name = Column(String(255), default="", comment="运营员工或店内身份信息")
    employee_info = Column(String(255), default="", comment="就职信息")
    position_title = Column(String(255), default="", comment="职位头衔")
    is_violation = Column(String(64), default="", comment="是否违规")
    valid_fans_count = Column(Integer, default=0, comment="有效粉丝数")
    bring_goods_permission = Column(String(64), default="", comment="带货权限")

    status = Column(String(64), default="", index=True, comment="职人绑定状态")
    raw_json = Column(Text, nullable=True, comment="职人绑定接口原始JSON")
    last_sync_at = Column(DateTime, nullable=True, index=True, comment="最近一次同步时间")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<DouyinCraftsmanBinding(poi_name='{self.poi_name}', aweme_name='{self.aweme_name}')>"


class JlyqLocalPromotionMetric(Base):
    """
    巨量引擎本地推账户指标表

    每天按当前展示窗口覆盖写入两类数据：
    - yesterday：昨天单日数据
    - month_to_yesterday：本月1号到昨天的累计数据
    """
    __tablename__ = "jlyq_local_promotion_metrics"
    __table_args__ = (
        UniqueConstraint(
            "binding_key",
            "range_type",
            "range_start_date",
            "range_end_date",
            name="uq_jlyq_local_binding_range",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 权限表绑定字段，用于把 Excel 中的门店和子账户稳定关联到数据库记录。
    binding_key = Column(String(191), nullable=False, index=True, comment="权限表账号绑定键")
    store_name = Column(String(255), default="", index=True, comment="权限表店铺名称")
    douyin_store_name = Column(String(255), default="", index=True, comment="权限表抖音来客店名")
    account_source_column = Column(String(64), default="", comment="账号来源列")

    # 巨量本地推账户字段，账号ID在未完成远端匹配时允许为空。
    account_id = Column(String(64), default="", index=True, comment="巨量本地推账户ID")
    account_name = Column(String(255), nullable=False, index=True, comment="巨量本地推账户名称")
    account_key = Column(String(191), nullable=False, index=True, comment="远端账号匹配键")

    # 数据窗口字段，保留昨天和本月累计两种窗口，侧边栏直接读取当前窗口。
    range_type = Column(String(32), nullable=False, index=True, comment="数据窗口类型")
    range_start_date = Column(Date, nullable=False, comment="数据开始日期")
    range_end_date = Column(Date, nullable=False, comment="数据结束日期")
    data_month = Column(String(7), nullable=False, index=True, comment="数据月份(YYYY-MM)")

    # 业务指标字段，金额统一按元保存。
    spend_yuan = Column(Numeric(14, 2), default=0.00, comment="消耗(元)")
    conversion_count = Column(Integer, default=0, comment="转化数")
    conversion_cost_yuan = Column(Numeric(14, 2), default=0.00, comment="转化成本(元)")
    balance_yuan = Column(Numeric(14, 2), default=0.00, comment="账户余额(元)")

    # 同步状态和原始响应摘要，便于后续排查字段变化。
    sync_status = Column(String(32), default="success", index=True, comment="同步状态")
    error_message = Column(Text, nullable=True, comment="同步错误信息")
    raw_report_json = Column(Text, nullable=True, comment="报表接口原始响应JSON")
    raw_fund_json = Column(Text, nullable=True, comment="余额接口原始响应JSON")
    synced_at = Column(DateTime, default=datetime.now, index=True, comment="同步时间")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<JlyqLocalPromotionMetric(account_name='{self.account_name}', range_type='{self.range_type}')>"


class JlyqMerchantDailyInput(Base):
    """
    巨量引擎商家每日填写数据表。

    每个巨量账号每天只保留一份填写记录，重复提交会覆盖当天数据，
    月累计值由同月每日记录实时汇总，避免重复提交造成累计翻倍。
    """
    __tablename__ = "jlyq_merchant_daily_inputs"
    __table_args__ = (
        UniqueConstraint(
            "binding_key",
            "data_date",
            name="uq_jlyq_merchant_binding_date",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 巨量账号和外部群上下文，用于把填写结果回写到正确模板。
    binding_key = Column(String(191), nullable=False, index=True, comment="权限表账号绑定键")
    account_id = Column(String(64), default="", index=True, comment="巨量本地推账号ID")
    account_name = Column(String(255), default="", index=True, comment="巨量本地推账号名称")
    store_name = Column(String(255), default="", index=True, comment="权限表店铺名称")
    group_name = Column(String(255), default="", index=True, comment="企业微信外部群名称")
    chat_id = Column(String(128), default="", index=True, comment="企业微信外部群ID")

    # 每日记录所属日期和月份，日期对应巨量模板中展示的昨天。
    data_date = Column(Date, nullable=False, index=True, comment="商家填写数据对应日期")
    data_month = Column(String(7), nullable=False, index=True, comment="数据月份(YYYY-MM)")

    # 官方收集表优先，缺少企业微信文档权限时自动使用安全网页填写页。
    channel = Column(String(32), default="web_fallback", index=True, comment="wecom_form或web_fallback")
    public_token = Column(String(128), nullable=False, unique=True, index=True, comment="公开填写页随机令牌")
    formid = Column(String(128), default=None, unique=True, nullable=True, comment="企业微信收集表ID")
    repeated_id = Column(String(128), default="", index=True, comment="企业微信收集表周期ID")
    share_url = Column(Text, nullable=True, comment="发送到外部群的填写链接")
    form_title = Column(String(255), default="", comment="填写表标题")
    permission_error = Column(Text, nullable=True, comment="企业微信文档权限错误摘要")

    # 商家每日填写项；利润按模板含义保存为当月截至当前的累计利润，不做逐日求和。
    wechat_count = Column(Integer, nullable=True, comment="当日加微信量")
    recycle_count = Column(Integer, nullable=True, comment="当日手机回收量")
    sales_count = Column(Integer, nullable=True, comment="当日手机销售量")
    profit_yuan = Column(Numeric(14, 2), nullable=True, comment="商家填报的本月抖音引流总利润")

    # 同步状态只保存答案标识和时间，不保存外部客户姓名、手机号等敏感信息。
    status = Column(String(32), default="created", index=True, comment="created/sent/submitted/sync_error")
    answer_id = Column(String(64), default="", comment="企业微信收集表答案ID")
    answer_mtime = Column(Integer, default=0, comment="企业微信答案修改时间戳")
    submit_count = Column(Integer, default=0, comment="该收集表当前有效提交数")
    sync_error = Column(Text, nullable=True, comment="答案同步错误摘要")
    sent_at = Column(DateTime, nullable=True, comment="文档卡片发送时间")
    submitted_at = Column(DateTime, nullable=True, index=True, comment="最近提交时间")
    last_sync_at = Column(DateTime, nullable=True, comment="最近同步时间")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    def __repr__(self):
        return (
            f"<JlyqMerchantDailyInput(binding_key='{self.binding_key}', "
            f"data_date='{self.data_date}', status='{self.status}')>"
        )


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
