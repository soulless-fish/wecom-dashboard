from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime

class APIResponse(BaseModel):
    """统一API响应格式"""
    code: int = 0
    message: str = "success"
    data: Any = None


class WeComUserInfo(BaseModel):
    """企业微信用户信息"""
    user_id: str
    name: str | None = None
    department: list[int] | None = None
    position: str | None = None
    mobile: str | None = None
    email: str | None = None
    avatar: str | None = None


class GroupChatInfo(BaseModel):
    """群聊信息"""
    chat_id: str
    name: str | None = None
    owner: str | None = None
    create_time: int | None = None
    member_count: int = 0


class MessagePayload(BaseModel):
    """消息载荷"""
    chat_id: str
    msg_type: str = "text"
    content: str


# ==================== 抖音来客相关模型 ====================

class DouyinTimeRangeQuery(BaseModel):
    """抖音时间范围查询参数"""
    start_time: Optional[int] = Field(None, description="开始时间戳(秒)")
    end_time: Optional[int] = Field(None, description="结束时间戳(秒)")
    page_num: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


class DouyinMessageQuery(DouyinTimeRangeQuery):
    """抖音消息查询参数"""
    message_type: int = Field(0, description="消息类型，0-全部")
    username: Optional[str] = Field(None, description="用户名筛选")


class DouyinOrderQuery(DouyinTimeRangeQuery):
    """抖音订单查询参数"""
    order_status: Optional[int] = Field(None, description="订单状态筛选")


class DouyinVisitorInfo(BaseModel):
    """抖音来客访客信息"""
    open_id: Optional[str] = Field(None, description="访客open_id")
    nickname: Optional[str] = Field(None, description="访客昵称")
    avatar: Optional[str] = Field(None, description="访客头像")
    visit_time: Optional[int] = Field(None, description="访问时间戳")
    source: Optional[str] = Field(None, description="来源渠道")


class DouyinPoiInfo(BaseModel):
    """抖音门店信息"""
    poi_id: Optional[str] = Field(None, description="门店ID")
    poi_name: Optional[str] = Field(None, description="门店名称")
    address: Optional[str] = Field(None, description="门店地址")
    longitude: Optional[float] = Field(None, description="经度")
    latitude: Optional[float] = Field(None, description="纬度")


class DouyinOrderInfo(BaseModel):
    """抖音订单信息"""
    order_id: Optional[str] = Field(None, description="订单ID")
    order_status: Optional[int] = Field(None, description="订单状态")
    create_time: Optional[int] = Field(None, description="创建时间戳")
    pay_time: Optional[int] = Field(None, description="支付时间戳")
    amount: Optional[float] = Field(None, description="订单金额")
    customer_name: Optional[str] = Field(None, description="客户名称")


class DouyinCustomerInfo(BaseModel):
    """抖音客户信息"""
    open_id: Optional[str] = Field(None, description="客户open_id")
    nickname: Optional[str] = Field(None, description="客户昵称")
    avatar: Optional[str] = Field(None, description="客户头像")
    gender: Optional[int] = Field(None, description="性别 0-未知 1-男 2-女")
    city: Optional[str] = Field(None, description="城市")
    province: Optional[str] = Field(None, description="省份")


class DouyinMessageInfo(BaseModel):
    """抖音消息信息"""
    message_id: Optional[str] = Field(None, description="消息ID")
    message_type: Optional[int] = Field(None, description="消息类型")
    content: Optional[str] = Field(None, description="消息内容")
    send_time: Optional[int] = Field(None, description="发送时间戳")
    sender: Optional[str] = Field(None, description="发送者")


# ==================== 来客后台数据模型 ====================

class LifeDataLiveInfo(BaseModel):
    """来客后台直播数据"""
    task_id: Optional[str] = Field(None, description="任务ID")
    room_id: Optional[str] = Field(None, description="直播ID")
    room_title: Optional[str] = Field(None, description="直播间标题")
    unique_id: Optional[str] = Field(None, description="抖音号")
    room_type: Optional[int] = Field(None, description="直播类型(1商家自播/2达人一带一/3达人一带多)")
    room_cover_url: Optional[str] = Field(None, description="直播间封面图")
    live_start_ts: Optional[int] = Field(None, description="开播时间戳")
    live_start_str: Optional[str] = Field(None, description="开播时间")
    duration: Optional[int] = Field(None, description="直播时长(秒)")
    gmv: Optional[int] = Field(None, description="成交金额(分)")
    room_pay_cert_num_td: Optional[int] = Field(None, description="直播间成交券数")
    live_watch_uv_td: Optional[int] = Field(None, description="观看人数")
    room_gpm: Optional[float] = Field(None, description="千次观看成交金额")


class LifeDataPoiInfo(BaseModel):
    """来客后台门店/视频数据"""
    poi_id: Optional[str] = Field(None, description="门店ID")
    poi_id_str: Optional[str] = Field(None, description="门店ID字符串")
    life_account_id: Optional[int] = Field(None, description="商户ID")
    life_account_name: Optional[str] = Field(None, description="门店名称")
    province: Optional[str] = Field(None, description="所在省份")
    city: Optional[str] = Field(None, description="所在城市")
    district: Optional[str] = Field(None, description="所在行政区")
    area_account_name: Optional[str] = Field(None, description="所在区域")
    poi_cover: Optional[str] = Field(None, description="门店头图")
    video_cnt_1d: Optional[int] = Field(None, description="新发布门店关联视频数")
    video_play_cnt_1d: Optional[int] = Field(None, description="门店关联视频播放次数")
    verify_amount_1d: Optional[int] = Field(None, description="门店核销金额(分)")
    pay_gmv: Optional[int] = Field(None, description="门店页成交金额(分)")


class LifeDataSummary(BaseModel):
    """来客后台数据汇总"""
    live_duration_total: int = Field(0, description="总直播时长(秒)")
    gmv_total: int = Field(0, description="总成交金额(分)")
    video_count_total: int = Field(0, description="视频发布总数")
    verify_amount_total: int = Field(0, description="核销总金额(分)")
    live_excel_url: Optional[str] = Field(None, description="直播数据Excel下载链接")
    poi_excel_url: Optional[str] = Field(None, description="门店数据Excel下载链接")


class LifeDataCookieConfig(BaseModel):
    """来客后台Cookie配置"""
    cookie: str = Field(..., description="来客后台Cookie字符串")
    life_account_id: str = Field(..., description="来客商户账户ID")
    csrf_token: Optional[str] = Field(None, description="x-secsdk-csrf-token")


