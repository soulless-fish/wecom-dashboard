"""巨量引擎商家填写表创建、发送准备、答案同步和月累计服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging
import os
from pathlib import Path
import secrets
from typing import Any, Iterable

import httpx
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database.models import JlyqMerchantDailyInput, SessionLocal
from app.services.token_manager import TokenManager

from .local_promotion_mapping import JlyqLocalAccountBinding, find_bindings_for_context


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORM_TEMPLATE_PATH = PROJECT_ROOT / "JLYQ" / "加微信量-手机回收量-手机销售量-抖音引流利润.xlsx"
DEFAULT_FORM_DESCRIPTION = (
    "请统一按照模板填写昨日数据。加微信量、手机回收量、手机销售量请填写非负整数；"
    "抖音引流利润填写本月截至当前的累计利润。"
)
DEFAULT_QUESTIONS = (
    "③加微信量:",
    "④手机回收量：",
    "⑤手机销售量：",
    "本月通过抖音引流的总利润（不用减投流本金）：",
)

_TEMPLATE_CACHE: "CollectionTemplate | None" = None
_TEMPLATE_MTIME: float | None = None
_WEDOC_BLOCKED_UNTIL: datetime | None = None
_WEDOC_BLOCKED_MESSAGE = ""


@dataclass(frozen=True)
class CollectionTemplate:
    """从本地 Excel 模板读取出的收集表说明和问题。"""

    description: str
    questions: tuple[str, str, str, str]


class WeComDocumentApiError(RuntimeError):
    """企业微信文档接口返回的业务错误。"""

    def __init__(self, errcode: int, errmsg: str):
        self.errcode = int(errcode or -1)
        self.errmsg = str(errmsg or "企业微信文档接口调用失败")
        super().__init__(f"企业微信文档接口错误 {self.errcode}: {self.errmsg}")


def load_collection_template(template_path: Path | None = None) -> CollectionTemplate:
    """读取用户编辑的 Excel 模板；文件不可用时使用同字段内置兜底。"""
    global _TEMPLATE_CACHE, _TEMPLATE_MTIME

    path = template_path or FORM_TEMPLATE_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        logger.warning("巨量引擎商家填写模板不存在，使用内置字段: %s", path)
        return CollectionTemplate(DEFAULT_FORM_DESCRIPTION, DEFAULT_QUESTIONS)

    if _TEMPLATE_CACHE is not None and _TEMPLATE_MTIME == mtime:
        return _TEMPLATE_CACHE

    workbook = None
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        worksheet = workbook.active
        description = str(worksheet["A1"].value or "").strip() or DEFAULT_FORM_DESCRIPTION
        questions = tuple(
            str(worksheet.cell(row=2, column=column).value or "").strip()
            for column in range(1, 5)
        )
        if len(questions) != 4 or any(not item for item in questions):
            raise ValueError("模板第2行 A2:D2 必须包含四个填写字段")
        template = CollectionTemplate(description, questions)
    except Exception as exc:
        logger.warning("读取巨量引擎商家填写模板失败，使用内置字段: %s", exc)
        template = CollectionTemplate(DEFAULT_FORM_DESCRIPTION, DEFAULT_QUESTIONS)
    finally:
        if workbook is not None:
            workbook.close()

    _TEMPLATE_CACHE = template
    _TEMPLATE_MTIME = mtime
    return template


def _question_item(question_id: int, title: str, integer_only: bool) -> dict[str, Any]:
    """生成企业微信收集表数字文本题。"""
    validation_detail = 11 if integer_only else 6
    text_setting: dict[str, Any] = {
        "validation_type": 1,
        "validation_detail": validation_detail,
    }
    if not integer_only:
        text_setting["number_min"] = 0
    return {
        "question_id": question_id,
        "title": title,
        "pos": question_id,
        "status": 1,
        "reply_type": 1,
        "must_reply": True,
        "note": "请填写非负整数" if integer_only else "请填写本月截至当前的累计利润，可填写小数",
        "question_extend_setting": {"text_setting": text_setting},
    }


async def _wedoc_request(
    client: httpx.AsyncClient,
    access_token: str,
    endpoint: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """调用企业微信文档接口并统一处理业务错误。"""
    response = await client.post(
        f"https://qyapi.weixin.qq.com/cgi-bin{endpoint}",
        params={"access_token": access_token},
        json=payload,
    )
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError("企业微信文档接口返回非 JSON 响应") from exc
    if int(result.get("errcode") or 0) != 0:
        raise WeComDocumentApiError(result.get("errcode") or -1, result.get("errmsg") or "")
    return result


async def create_wecom_collection_form(
    access_token: str,
    title: str,
    template: CollectionTemplate,
) -> dict[str, str]:
    """创建官方收集表、读取周期 ID 并生成分享链接。"""
    form_payload = {
        "form_info": {
            "form_title": title,
            "form_desc": template.description,
            "form_question": {
                "items": [
                    _question_item(1, template.questions[0], True),
                    _question_item(2, template.questions[1], True),
                    _question_item(3, template.questions[2], True),
                    _question_item(4, template.questions[3], False),
                ]
            },
            "form_setting": {
                "fill_out_auth": 0,
                "allow_multi_fill": False,
                "can_anonymous": False,
                "can_notify_submit": True,
            },
        }
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await _wedoc_request(client, access_token, "/wedoc/create_form", form_payload)
        formid = str(created.get("formid") or "").strip()
        if not formid:
            raise RuntimeError("企业微信创建收集表成功但未返回 formid")

        info = await _wedoc_request(
            client,
            access_token,
            "/wedoc/get_form_info",
            {"formid": formid},
        )
        repeated_ids = info.get("form_info", {}).get("repeated_id") or []
        repeated_id = str(repeated_ids[0] if repeated_ids else "")
        shared = await _wedoc_request(
            client,
            access_token,
            "/wedoc/doc_share",
            {"formid": formid},
        )
        share_url = str(shared.get("share_url") or "").strip()
        if not share_url:
            raise RuntimeError("企业微信收集表分享接口未返回链接")
        return {
            "formid": formid,
            "repeated_id": repeated_id,
            "share_url": share_url,
        }


def _select_binding(
    group_name: str,
    poi_name: str,
    binding_key: str,
) -> JlyqLocalAccountBinding:
    """确认请求中的账号绑定确实属于当前外部群或门店。"""
    bindings = find_bindings_for_context(group_name=group_name, poi_name=poi_name)
    binding = next((item for item in bindings if item.binding_key == binding_key), None)
    if binding is None:
        raise ValueError("当前外部群无权创建该巨量账号的填写表")
    return binding


def _public_form_url(settings: Settings, token: str) -> str:
    """生成随机令牌保护的公开填写页链接。"""
    base_url = str(settings.public_base_url or "http://localhost:5173").rstrip("/")
    return f"{base_url}/jlyq-form?token={token}"


def _card_image_url(settings: Settings) -> str:
    """返回企业微信 H5 卡片封面地址。"""
    base_url = str(settings.public_base_url or "http://localhost:5173").rstrip("/")
    return f"{base_url}/jlyq-form-card.svg"


def _permission_notice(record: JlyqMerchantDailyInput) -> str:
    """把文档权限错误转换为不含接口提示编号的业务说明。"""
    if record.channel != "web_fallback" or not record.permission_error:
        return ""
    if "48002" in record.permission_error:
        return "企业微信文档权限尚未开通，本次已自动使用安全网页填写表；开通后会自动切换为官方收集表。"
    return "企业微信官方收集表暂不可用，本次已自动使用安全网页填写表，商家提交和累计更新不受影响。"


def collection_record_payload(
    record: JlyqMerchantDailyInput,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """把填写记录转换为侧边栏可安全展示的结构。"""
    active_settings = settings or get_settings()
    return {
        "id": record.id,
        "channel": record.channel,
        "share_url": record.share_url or "",
        "form_title": record.form_title or "",
        "data_date": record.data_date.isoformat() if record.data_date else "",
        "status": record.status,
        "submit_count": int(record.submit_count or 0),
        "sent_at": record.sent_at.isoformat() if record.sent_at else "",
        "submitted_at": record.submitted_at.isoformat() if record.submitted_at else "",
        "last_sync_at": record.last_sync_at.isoformat() if record.last_sync_at else "",
        "permission_notice": _permission_notice(record),
        "card_image_url": _card_image_url(active_settings),
    }


async def prepare_collection_form(
    db: Session,
    settings: Settings,
    *,
    group_name: str,
    poi_name: str,
    chat_id: str,
    binding_key: str,
    account_id: str = "",
    account_name: str = "",
) -> dict[str, Any]:
    """优先创建官方收集表，权限不足时自动创建同字段安全网页填写表。"""
    global _WEDOC_BLOCKED_UNTIL, _WEDOC_BLOCKED_MESSAGE

    binding = _select_binding(group_name, poi_name, binding_key)
    data_date = date.today() - timedelta(days=1)
    existing = (
        db.query(JlyqMerchantDailyInput)
        .filter(
            JlyqMerchantDailyInput.binding_key == binding_key,
            JlyqMerchantDailyInput.data_date == data_date,
        )
        .first()
    )
    if existing is not None:
        return collection_record_payload(existing, settings)

    template = load_collection_template()
    display_account_name = str(account_name or binding.account_name or binding.store_name).strip()
    title = f"{data_date.month}月{data_date.day}日 {display_account_name} 经营数据填写"
    public_token = secrets.token_urlsafe(32)
    channel = "web_fallback"
    formid: str | None = None
    repeated_id = ""
    share_url = _public_form_url(settings, public_token)
    permission_error = ""

    can_probe_wedoc = not _WEDOC_BLOCKED_UNTIL or datetime.now() >= _WEDOC_BLOCKED_UNTIL
    if can_probe_wedoc:
        try:
            access_token = await TokenManager(settings).get_access_token()
            remote_form = await create_wecom_collection_form(access_token, title, template)
            channel = "wecom_form"
            formid = remote_form["formid"]
            repeated_id = remote_form["repeated_id"]
            share_url = remote_form["share_url"]
            _WEDOC_BLOCKED_UNTIL = None
            _WEDOC_BLOCKED_MESSAGE = ""
        except WeComDocumentApiError as exc:
            permission_error = str(exc)
            if exc.errcode == 48002:
                _WEDOC_BLOCKED_UNTIL = datetime.now() + timedelta(minutes=10)
                _WEDOC_BLOCKED_MESSAGE = permission_error
            logger.warning("企业微信官方收集表不可用，切换安全网页填写表: %s", exc)
        except Exception as exc:
            permission_error = str(exc)
            logger.warning("创建企业微信官方收集表失败，切换安全网页填写表: %s", exc)
    else:
        permission_error = _WEDOC_BLOCKED_MESSAGE or "企业微信文档接口权限暂不可用"

    record = JlyqMerchantDailyInput(
        binding_key=binding.binding_key,
        account_id=str(account_id or ""),
        account_name=display_account_name,
        store_name=binding.store_name,
        group_name=str(group_name or ""),
        chat_id=str(chat_id or ""),
        data_date=data_date,
        data_month=data_date.strftime("%Y-%m"),
        channel=channel,
        public_token=public_token,
        formid=formid,
        repeated_id=repeated_id,
        share_url=share_url,
        form_title=title,
        permission_error=permission_error,
        status="created",
        last_sync_at=datetime.now() if channel == "wecom_form" else None,
    )
    db.add(record)
    try:
        db.commit()
        db.refresh(record)
    except IntegrityError:
        db.rollback()
        record = (
            db.query(JlyqMerchantDailyInput)
            .filter(
                JlyqMerchantDailyInput.binding_key == binding_key,
                JlyqMerchantDailyInput.data_date == data_date,
            )
            .first()
        )
        if record is None:
            raise
    return collection_record_payload(record, settings)


def mark_collection_form_sent(db: Session, record_id: int) -> dict[str, Any]:
    """记录前端已成功把填写卡片发送到当前会话。"""
    record = db.query(JlyqMerchantDailyInput).filter(JlyqMerchantDailyInput.id == record_id).first()
    if record is None:
        raise ValueError("填写表记录不存在")
    record.status = "submitted" if record.submitted_at else "sent"
    record.sent_at = datetime.now()
    db.commit()
    db.refresh(record)
    return collection_record_payload(record)


def _nonnegative_int(value: Any, field_name: str) -> int:
    """校验商家填写的非负整数。"""
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    try:
        numeric = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name}必须是数字") from exc
    if numeric < 0 or numeric != numeric.to_integral_value():
        raise ValueError(f"{field_name}必须是非负整数")
    if numeric > 1_000_000:
        raise ValueError(f"{field_name}超过允许范围")
    return int(numeric)


def _nonnegative_money(value: Any, field_name: str) -> Decimal:
    """校验非负金额并保留两位小数。"""
    text = str(value if value is not None else "").replace(",", "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    try:
        numeric = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name}必须是数字") from exc
    if numeric < 0 or numeric > Decimal("1000000000"):
        raise ValueError(f"{field_name}超过允许范围")
    return numeric.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_collection_summary(
    db: Session,
    binding_key: str,
    data_date: date,
    data_month: str,
) -> dict[str, Any]:
    """返回当天填写值、三项月累计和最新月利润。"""
    daily_record = (
        db.query(JlyqMerchantDailyInput)
        .filter(
            JlyqMerchantDailyInput.binding_key == binding_key,
            JlyqMerchantDailyInput.data_date == data_date,
        )
        .first()
    )
    totals = (
        db.query(
            func.coalesce(func.sum(JlyqMerchantDailyInput.wechat_count), 0),
            func.coalesce(func.sum(JlyqMerchantDailyInput.recycle_count), 0),
            func.coalesce(func.sum(JlyqMerchantDailyInput.sales_count), 0),
        )
        .filter(
            JlyqMerchantDailyInput.binding_key == binding_key,
            JlyqMerchantDailyInput.data_month == data_month,
            JlyqMerchantDailyInput.status == "submitted",
        )
        .one()
    )
    latest_profit_record = (
        db.query(JlyqMerchantDailyInput)
        .filter(
            JlyqMerchantDailyInput.binding_key == binding_key,
            JlyqMerchantDailyInput.data_month == data_month,
            JlyqMerchantDailyInput.status == "submitted",
            JlyqMerchantDailyInput.profit_yuan.isnot(None),
        )
        .order_by(
            JlyqMerchantDailyInput.data_date.desc(),
            JlyqMerchantDailyInput.submitted_at.desc(),
        )
        .first()
    )
    return {
        "daily": {
            "wechat_count": daily_record.wechat_count if daily_record and daily_record.status == "submitted" else None,
            "recycle_count": daily_record.recycle_count if daily_record and daily_record.status == "submitted" else None,
            "sales_count": daily_record.sales_count if daily_record and daily_record.status == "submitted" else None,
            "profit_yuan": (
                f"{daily_record.profit_yuan:.2f}"
                if daily_record and daily_record.status == "submitted" and daily_record.profit_yuan is not None
                else ""
            ),
        },
        "month": {
            "wechat_count": int(totals[0] or 0),
            "recycle_count": int(totals[1] or 0),
            "sales_count": int(totals[2] or 0),
            "profit_yuan": (
                f"{latest_profit_record.profit_yuan:.2f}"
                if latest_profit_record and latest_profit_record.profit_yuan is not None
                else ""
            ),
        },
        "form": collection_record_payload(daily_record) if daily_record else None,
        "submitted": bool(daily_record and daily_record.status == "submitted"),
    }


def get_public_collection_form(db: Session, public_token: str) -> dict[str, Any]:
    """读取安全网页填写页所需的非敏感信息。"""
    record = (
        db.query(JlyqMerchantDailyInput)
        .filter(JlyqMerchantDailyInput.public_token == public_token)
        .first()
    )
    if record is None or record.channel != "web_fallback":
        raise ValueError("填写链接无效或已失效")
    template = load_collection_template()
    summary = build_collection_summary(db, record.binding_key, record.data_date, record.data_month)
    return {
        "form_title": record.form_title,
        "description": template.description,
        "questions": list(template.questions),
        "account_name": record.account_name,
        "store_name": record.store_name,
        "group_name": record.group_name,
        "data_date": record.data_date.isoformat(),
        "values": summary["daily"],
        "month": summary["month"],
        "submitted": summary["submitted"],
        "submitted_at": record.submitted_at.isoformat() if record.submitted_at else "",
    }


def submit_public_collection_form(
    db: Session,
    public_token: str,
    *,
    wechat_count: Any,
    recycle_count: Any,
    sales_count: Any,
    profit_yuan: Any,
) -> dict[str, Any]:
    """保存或覆盖当天商家填写值，并重新计算月累计。"""
    record = (
        db.query(JlyqMerchantDailyInput)
        .filter(JlyqMerchantDailyInput.public_token == public_token)
        .first()
    )
    if record is None or record.channel != "web_fallback":
        raise ValueError("填写链接无效或已失效")

    record.wechat_count = _nonnegative_int(wechat_count, "加微信量")
    record.recycle_count = _nonnegative_int(recycle_count, "手机回收量")
    record.sales_count = _nonnegative_int(sales_count, "手机销售量")
    record.profit_yuan = _nonnegative_money(profit_yuan, "抖音引流总利润")
    record.status = "submitted"
    record.submit_count = 1
    record.submitted_at = datetime.now()
    record.last_sync_at = datetime.now()
    record.sync_error = ""
    db.commit()
    db.refresh(record)
    return get_public_collection_form(db, public_token)


def parse_wecom_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """把企业微信收集表四道题的答案转换为业务字段。"""
    items = answer.get("reply", {}).get("items") or []
    replies = {
        int(item.get("question_id") or 0): item.get("text_reply")
        for item in items
        if isinstance(item, dict)
    }
    return {
        "wechat_count": _nonnegative_int(replies.get(1), "加微信量"),
        "recycle_count": _nonnegative_int(replies.get(2), "手机回收量"),
        "sales_count": _nonnegative_int(replies.get(3), "手机销售量"),
        "profit_yuan": _nonnegative_money(replies.get(4), "抖音引流总利润"),
    }


async def _sync_single_wecom_record(
    client: httpx.AsyncClient,
    access_token: str,
    record: JlyqMerchantDailyInput,
) -> bool:
    """同步一个企业微信收集表的最新有效答案。"""
    if not record.formid:
        raise ValueError("收集表记录缺少 formid")

    repeated_id = str(record.repeated_id or "")
    if not repeated_id:
        info = await _wedoc_request(
            client,
            access_token,
            "/wedoc/get_form_info",
            {"formid": record.formid},
        )
        repeated_ids = info.get("form_info", {}).get("repeated_id") or []
        repeated_id = str(repeated_ids[0] if repeated_ids else "")
        record.repeated_id = repeated_id
    if not repeated_id:
        raise ValueError("收集表尚未返回 repeated_id")

    start_day = min(record.created_at.date() if record.created_at else record.data_date, record.data_date)
    start_timestamp = int(datetime.combine(start_day, time.min).timestamp())
    end_timestamp = int(datetime.combine(date.today(), time.max).timestamp())
    statistic = await _wedoc_request(
        client,
        access_token,
        "/wedoc/get_form_statistic",
        {
            "repeated_id": repeated_id,
            "req_type": 2,
            "start_time": start_timestamp,
            "end_time": end_timestamp,
            "limit": 10000,
        },
    )
    submit_users = statistic.get("submit_users") or []
    answer_ids = [
        int(item.get("answer_id"))
        for item in submit_users
        if isinstance(item, dict) and item.get("answer_id") is not None
    ]
    record.submit_count = len(answer_ids)
    record.last_sync_at = datetime.now()
    record.sync_error = ""
    if not answer_ids:
        return False

    answers: list[dict[str, Any]] = []
    for offset in range(0, len(answer_ids), 100):
        response = await _wedoc_request(
            client,
            access_token,
            "/wedoc/get_form_answer",
            {"repeated_id": repeated_id, "answer_ids": answer_ids[offset:offset + 100]},
        )
        answers.extend(response.get("answer", {}).get("answer_list") or [])
    valid_answers = [
        item
        for item in answers
        if isinstance(item, dict) and int(item.get("answer_status") or 0) == 1
    ]
    if not valid_answers:
        return False
    latest = max(valid_answers, key=lambda item: int(item.get("mtime") or item.get("ctime") or 0))
    values = parse_wecom_answer(latest)
    record.wechat_count = values["wechat_count"]
    record.recycle_count = values["recycle_count"]
    record.sales_count = values["sales_count"]
    record.profit_yuan = values["profit_yuan"]
    record.answer_id = str(latest.get("answer_id") or "")
    record.answer_mtime = int(latest.get("mtime") or latest.get("ctime") or 0)
    record.status = "submitted"
    record.submitted_at = datetime.fromtimestamp(record.answer_mtime) if record.answer_mtime else datetime.now()
    return True


async def sync_wecom_collection_forms(
    settings: Settings | None = None,
    binding_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """同步最近四十天官方收集表答案，供接口和定时任务共同调用。"""
    active_settings = settings or get_settings()
    db = SessionLocal()
    try:
        query = db.query(JlyqMerchantDailyInput).filter(
            JlyqMerchantDailyInput.channel == "wecom_form",
            JlyqMerchantDailyInput.data_date >= date.today() - timedelta(days=40),
        )
        keys = [str(item) for item in (binding_keys or []) if str(item).strip()]
        if keys:
            query = query.filter(JlyqMerchantDailyInput.binding_key.in_(keys))
        records = query.order_by(JlyqMerchantDailyInput.data_date.asc()).all()
        if not records:
            return {"success": True, "checked": 0, "updated": 0, "failed": 0}

        access_token = await TokenManager(active_settings).get_access_token()
        checked = 0
        updated = 0
        failed = 0
        async with httpx.AsyncClient(timeout=30.0) as client:
            for record in records:
                checked += 1
                try:
                    if await _sync_single_wecom_record(client, access_token, record):
                        updated += 1
                except Exception as exc:
                    failed += 1
                    record.status = "sync_error" if record.status != "submitted" else record.status
                    record.sync_error = str(exc)[:1000]
                    record.last_sync_at = datetime.now()
                    logger.warning("同步企业微信收集表答案失败: record_id=%s %s", record.id, exc)
        db.commit()
        return {
            "success": failed == 0,
            "checked": checked,
            "updated": updated,
            "failed": failed,
        }
    finally:
        db.close()


def public_base_url_from_env() -> str:
    """返回部署脚本和测试可复用的公开域名。"""
    return os.environ.get("PUBLIC_BASE_URL", "http://localhost:5173").rstrip("/")
