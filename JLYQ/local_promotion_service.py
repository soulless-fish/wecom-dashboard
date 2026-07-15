"""
巨量引擎本地推数据同步和侧边栏展示服务。

本文件负责三件事：
1. 根据权限表找到外部群可查看的本地推账号。
2. 调用巨量引擎接口同步昨天和本月累计指标。
3. 生成侧边栏需要复制的文本模板。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import logging
import os
import re
from typing import Any, Iterable
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.database.models import DataSyncLog, JlyqLocalPromotionMetric, SessionLocal

from .local_promotion_mapping import (
    JlyqLocalAccountBinding,
    build_match_tokens,
    find_bindings_for_context,
    load_permission_bindings,
    make_account_key,
    normalize_text,
)
from .oauth_client import API_BASE, fetch_authorized_advertisers, get_json, refresh_access_token
from .token_storage import build_token_record, load_token_record, save_token_record
from .wecom_collection_service import build_collection_summary


logger = logging.getLogger(__name__)

REPORT_FIELD_ALIASES = {
    "spend_yuan": ["stat_cost", "cost", "spend", "spend_yuan", "total_cost"],
    "conversion_count": ["convert_cnt", "convert", "conversion", "conversion_count", "conversions"],
    "conversion_cost_yuan": [
        "convert_cost",
        "conversion_cost",
        "avg_convert_cost",
        "convert_cost_yuan",
        "conversion_cost_yuan",
    ],
}


@dataclass(frozen=True)
class JlyqDateRanges:
    """巨量侧边栏需要展示的两个日期窗口。"""

    yesterday: date
    month_start: date
    data_month: str


@dataclass(frozen=True)
class RemoteAccount:
    """远端巨量账户信息。"""

    account_id: str
    account_name: str
    account_type: str = "LOCAL"


@dataclass(frozen=True)
class MetricSnapshot:
    """一次报表窗口的指标快照。"""

    spend_yuan: Decimal
    conversion_count: int
    conversion_cost_yuan: Decimal
    balance_yuan: Decimal
    raw_report: dict[str, Any] | None = None
    raw_fund: dict[str, Any] | None = None


class JlyqAuthError(RuntimeError):
    """巨量引擎授权不可用。"""


@dataclass(frozen=True)
class AuthorizedAccountGroups:
    """OAuth 授权账户按后续接口用途拆分后的结果。"""

    all_account_ids: list[str]
    workbench_account_ids: list[str]
    life_account_ids: list[str]


def get_display_ranges(today: date | None = None) -> JlyqDateRanges:
    """返回昨天和本月1号到昨天的日期窗口。"""
    current_day = today or date.today()
    yesterday = current_day - timedelta(days=1)
    month_start = current_day.replace(day=1)
    if yesterday < month_start:
        month_start = yesterday.replace(day=1)
    return JlyqDateRanges(
        yesterday=yesterday,
        month_start=month_start,
        data_month=month_start.strftime("%Y-%m"),
    )


def _decimal(value: Any) -> Decimal:
    """把接口返回值转换为 Decimal。"""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    text = text.replace(",", "").replace("¥", "").replace("￥", "").replace("%", "")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _money(value: Any) -> Decimal:
    """金额统一保留两位小数。"""
    return _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _int_value(value: Any) -> int:
    """把接口返回值转换为整数。"""
    try:
        return int(_decimal(value).to_integral_value(rounding=ROUND_HALF_UP))
    except Exception:
        return 0


def _format_money(value: Any) -> str:
    """格式化金额，复制模板中不强制补无意义的 0。"""
    numeric_value = _money(value)
    text = f"{numeric_value:.2f}"
    if text.endswith("00"):
        return text[:-3]
    if text.endswith("0"):
        return text[:-1]
    return text


def _format_money_fixed(value: Any) -> str:
    """格式化补充展示金额，固定两位小数。"""
    return f"{_money(value):.2f}"


def _parse_iso_datetime(value: str) -> datetime | None:
    """解析 token 过期时间。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def get_valid_access_token() -> str:
    """读取可用 access_token，临近过期时自动刷新。"""
    record = load_token_record()
    access_token = str(record.get("access_token") or "")
    if not access_token:
        raise JlyqAuthError("巨量引擎尚未完成授权，无法同步本地推数据")

    expires_at = _parse_iso_datetime(str(record.get("access_token_expires_at") or ""))
    should_refresh = expires_at is not None and expires_at <= datetime.now(timezone.utc) + timedelta(minutes=10)
    refresh_token = str(record.get("refresh_token") or "")
    if not should_refresh or not refresh_token:
        return access_token

    refreshed = refresh_access_token(refresh_token)
    if refreshed.get("code") not in {0, "0", None}:
        raise JlyqAuthError(refreshed.get("message") or refreshed.get("msg") or "巨量引擎 token 刷新失败")

    new_record = build_token_record(refreshed, state=str(record.get("state") or ""))
    if not new_record.get("advertiser_ids"):
        new_record["advertiser_ids"] = record.get("advertiser_ids") or []
    save_token_record(new_record)
    return str(new_record.get("access_token") or access_token)


def _json_param(value: Any) -> str:
    """把列表或字典参数编码成巨量接口常用的 JSON 字符串。"""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _get_ocean_json(path: str, access_token: str, params: dict[str, Any]) -> dict[str, Any]:
    """调用巨量 GET 接口。"""
    clean_params = {
        key: _json_param(value)
        for key, value in params.items()
        if value is not None and value != "" and value != []
    }
    query = urlencode(clean_params)
    url = f"{API_BASE}{path}"
    if query:
        url = f"{url}?{query}"
    return get_json(url, headers={"Access-Token": access_token})


def _response_success(response: dict[str, Any]) -> bool:
    """判断巨量接口响应是否成功。"""
    return response.get("code") in {0, "0", None}


def _extract_id_list(response: dict[str, Any]) -> list[str]:
    """从授权接口响应中提取账户ID列表。"""
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    candidates = [
        data.get("advertiser_ids") if isinstance(data, dict) else None,
        data.get("account_ids") if isinstance(data, dict) else None,
        data.get("ids") if isinstance(data, dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [str(item) for item in candidate if str(item or "").strip()]
    return []


def _extract_account_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    """从多种巨量账户接口响应结构中提取账号列表。"""
    data = response.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []

    for key in [
        "list",
        "accounts",
        "account_list",
        "advertisers",
        "advertiser_list",
        "local_advertisers",
        "local_advertiser_list",
        "adv_list",
        "data_list",
    ]:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    id_list = _extract_id_list(response)
    return [{"account_id": item, "account_name": ""} for item in id_list]


def _remote_account_from_item(item: dict[str, Any], account_type: str = "LOCAL") -> RemoteAccount | None:
    """把远端接口返回项整理成统一账号结构。"""
    account_id = str(
        item.get("account_id")
        or item.get("advertiser_id")
        or item.get("id")
        or item.get("local_account_id")
        or item.get("local_advertiser_id")
        or ""
    ).strip()
    account_name = str(
        item.get("account_name")
        or item.get("advertiser_name")
        or item.get("name")
        or item.get("local_account_name")
        or item.get("local_advertiser_name")
        or ""
    ).strip()
    if item.get("account_type"):
        account_type = str(item.get("account_type") or account_type)
    if not account_id and not account_name:
        return None
    return RemoteAccount(account_id=account_id, account_name=account_name, account_type=account_type)


def _dedupe_remote_accounts(accounts: Iterable[RemoteAccount]) -> list[RemoteAccount]:
    """远端账号按账号ID或账号名去重。"""
    result: list[RemoteAccount] = []
    seen: set[str] = set()
    for account in accounts:
        key = f"id:{account.account_id}" if account.account_id else f"name:{normalize_text(account.account_name)}"
        if key in seen:
            continue
        seen.add(key)
        result.append(account)
    return result


def fetch_advertiser_public_accounts(access_token: str, account_ids: list[str], account_type: str = "AD") -> list[RemoteAccount]:
    """查询授权账户公开信息，用于把账号ID补成账号名。"""
    if not account_ids:
        return []

    accounts: list[RemoteAccount] = []
    for index in range(0, len(account_ids), 100):
        chunk = account_ids[index:index + 100]
        numeric_ids = [int(item) for item in chunk if str(item).isdigit()]
        if not numeric_ids:
            continue
        try:
            response = _get_ocean_json(
                "/open_api/2/advertiser/public_info/",
                access_token,
                {"advertiser_ids": numeric_ids},
            )
            if not _response_success(response):
                logger.warning("巨量账户公开信息接口返回异常: %s", response)
                continue
            for item in _extract_account_items(response):
                account = _remote_account_from_item(item, account_type=account_type)
                if account:
                    accounts.append(account)
        except Exception as exc:
            logger.warning("查询巨量账户公开信息失败: %s", exc)
    return _dedupe_remote_accounts(accounts)


def fetch_local_life_accounts(access_token: str, manager_account_ids: list[str]) -> list[RemoteAccount]:
    """尝试通过来客账户查询关联的本地推账户。"""
    accounts: list[RemoteAccount] = []
    for manager_id in manager_account_ids:
        try:
            response = _get_ocean_json(
                "/open_api/v3.0/local/life/advertiser/list/",
                access_token,
                {"life_account_id": manager_id, "page": 1, "page_size": 100},
            )
            if not _response_success(response):
                logger.info("来客关联本地推账户接口未命中: life_account_id=%s response=%s", manager_id, response)
                continue
            for item in _extract_account_items(response):
                account = _remote_account_from_item(item, account_type="LOCAL")
                if account:
                    accounts.append(account)
        except Exception as exc:
            logger.info("查询来客关联本地推账户失败: life_account_id=%s error=%s", manager_id, exc)
    return _dedupe_remote_accounts(accounts)


def fetch_customer_center_local_accounts(access_token: str, workbench_account_ids: list[str]) -> list[RemoteAccount]:
    """通过巨量引擎工作台账号查询其管理的本地推账户。"""
    accounts: list[RemoteAccount] = []
    for account_id in workbench_account_ids:
        if not str(account_id).isdigit():
            continue
        page = 1
        while page <= 100:
            try:
                response = _get_ocean_json(
                    "/open_api/v3.0/customer_center/account/list/",
                    access_token,
                    {
                        "account_id": int(account_id),
                        "filter": {"account_type": "LOCAL"},
                        "page": page,
                        "page_size": 100,
                    },
                )
            except Exception as exc:
                logger.info("查询工作台本地推账户失败: account_id=%s error=%s", account_id, exc)
                break

            if not _response_success(response):
                logger.info("工作台本地推账户接口未命中: account_id=%s response=%s", account_id, response)
                break

            items = _extract_account_items(response)
            for item in items:
                account = _remote_account_from_item(item, account_type="LOCAL")
                if account:
                    accounts.append(account)

            page_info = response.get("data", {}).get("page_info") if isinstance(response.get("data"), dict) else {}
            total_page = int(page_info.get("total_page") or page_info.get("total_pages") or page)
            if not items or page >= total_page:
                break
            page += 1
    return _dedupe_remote_accounts(accounts)


def fetch_legacy_customer_center_local_accounts(access_token: str, workbench_account_ids: list[str]) -> list[RemoteAccount]:
    """通过旧版工作台客户列表接口查询本地推账户。"""
    accounts: list[RemoteAccount] = []
    for account_id in workbench_account_ids:
        if not str(account_id).isdigit():
            continue
        page = 1
        while page <= 100:
            try:
                response = _get_ocean_json(
                    "/open_api/2/customer_center/advertiser/list/",
                    access_token,
                    {
                        "cc_account_id": int(account_id),
                        "account_source": "LOCAL",
                        "page": page,
                        "page_size": 100,
                    },
                )
            except Exception as exc:
                logger.info("查询旧版工作台本地推账户失败: account_id=%s error=%s", account_id, exc)
                break

            if not _response_success(response):
                logger.info("旧版工作台本地推账户接口未命中: account_id=%s response=%s", account_id, response)
                break

            items = _extract_account_items(response)
            for item in items:
                account = _remote_account_from_item(item, account_type="LOCAL")
                if account:
                    accounts.append(account)

            page_info = response.get("data", {}).get("page_info") if isinstance(response.get("data"), dict) else {}
            total_page = int(page_info.get("total_page") or page)
            if not items or page >= total_page:
                break
            page += 1
    return _dedupe_remote_accounts(accounts)


def load_configured_life_account_ids() -> list[str]:
    """读取可选的来客账户ID配置，支持多个ID逗号分隔。"""
    raw_values = [
        os.environ.get("JLYQ_LIFE_ACCOUNT_IDS", ""),
        os.environ.get("JLYQ_LIFE_ACCOUNT_ID", ""),
        os.environ.get("LIFE_DATA_ACCOUNT_ID", ""),
    ]
    result: list[str] = []
    for raw_value in raw_values:
        for item in str(raw_value or "").replace("，", ",").split(","):
            value = item.strip()
            if value and value not in result:
                result.append(value)
    return result


def _append_unique(target: list[str], value: Any) -> None:
    """把非空账号ID追加到列表，并保持原始顺序去重。"""
    text = str(value or "").strip()
    if text and text not in target:
        target.append(text)


def classify_authorized_accounts(response: dict[str, Any], fallback_ids: list[str] | None = None) -> AuthorizedAccountGroups:
    """把 OAuth 授权账户拆分成工作台账号和来客账号。"""
    all_account_ids: list[str] = []
    workbench_account_ids: list[str] = []
    life_account_ids: list[str] = []

    for item in _extract_account_items(response):
        account_id = (
            item.get("account_id")
            or item.get("advertiser_id")
            or item.get("id")
            or item.get("local_account_id")
            or ""
        )
        role_text = " ".join([
            str(item.get("account_role") or ""),
            str(item.get("account_type") or ""),
            str(item.get("role") or ""),
        ]).upper()
        _append_unique(all_account_ids, account_id)
        if "CUSTOMER" in role_text:
            _append_unique(workbench_account_ids, account_id)
        if "LIFE" in role_text:
            _append_unique(life_account_ids, account_id)

    for account_id in fallback_ids or []:
        _append_unique(all_account_ids, account_id)

    if not workbench_account_ids and not life_account_ids:
        # 老接口有时只返回 ID 列表，没有角色字段，此时沿用旧逻辑做工作台兜底探测。
        workbench_account_ids = list(all_account_ids)

    return AuthorizedAccountGroups(
        all_account_ids=all_account_ids,
        workbench_account_ids=workbench_account_ids,
        life_account_ids=life_account_ids,
    )


def build_remote_account_catalog(access_token: str) -> list[RemoteAccount]:
    """构建可用远端账号目录，优先包含本地推账号。"""
    token_record = load_token_record()
    account_ids = [str(item) for item in token_record.get("advertiser_ids") or [] if str(item or "").strip()]
    workbench_account_ids: list[str] = []
    life_account_ids = load_configured_life_account_ids()

    try:
        authorized_response = fetch_authorized_advertisers(access_token)
        if _response_success(authorized_response):
            groups = classify_authorized_accounts(authorized_response, fallback_ids=account_ids)
            account_ids.extend(groups.all_account_ids)
            workbench_account_ids.extend(groups.workbench_account_ids)
            life_account_ids.extend(groups.life_account_ids)
    except Exception as exc:
        logger.warning("查询巨量授权账户失败: %s", exc)

    account_ids = list(dict.fromkeys(account_ids))
    workbench_account_ids = list(dict.fromkeys(workbench_account_ids or account_ids))
    life_account_ids = list(dict.fromkeys(life_account_ids))

    customer_center_accounts = fetch_customer_center_local_accounts(access_token, workbench_account_ids)
    legacy_customer_center_accounts = fetch_legacy_customer_center_local_accounts(access_token, workbench_account_ids)
    local_accounts = fetch_local_life_accounts(access_token, life_account_ids)
    local_account_ids = [account.account_id for account in local_accounts if account.account_id]
    local_public_accounts = fetch_advertiser_public_accounts(access_token, local_account_ids, account_type="LOCAL")
    public_accounts = fetch_advertiser_public_accounts(access_token, account_ids)

    # 来客接口只返回本地推账号ID，公开信息接口可补账号名，便于和权限表匹配。
    # 旧版工作台接口能返回当前账号管理的全量 LOCAL 账户，是本地推同步的主要兜底来源。
    return _dedupe_remote_accounts([
        *legacy_customer_center_accounts,
        *customer_center_accounts,
        *local_public_accounts,
        *local_accounts,
        *public_accounts,
    ])


def _account_name_match_tokens(value: str) -> set[str]:
    """生成巨量账号名匹配 token，兼容账号名尾部数字和括号门店名差异。"""
    text = normalize_text(value)
    if not text:
        return set()

    candidates = {text}
    without_tail_digits = re.sub(r"\d{3,8}$", "", text)
    if without_tail_digits:
        candidates.add(without_tail_digits)

    tokens: set[str] = set()
    for candidate in candidates:
        tokens.update(build_match_tokens(candidate))
    return {item for item in tokens if item}


def _match_remote_account(binding: JlyqLocalAccountBinding, accounts: list[RemoteAccount]) -> RemoteAccount | None:
    """按权限表账号名匹配远端账号。"""
    target_name = normalize_text(binding.account_name)
    if not target_name:
        return None

    # 权限表里部分账户名带有尾号备注；如果远端同时存在店铺名对应账号，优先使用店铺名账号。
    store_tokens: set[str] = set()
    store_tokens.update(_account_name_match_tokens(binding.store_name))
    store_tokens.update(_account_name_match_tokens(binding.douyin_store_name))
    store_tokens = {item for item in store_tokens if len(item) >= 3}
    if re.search(r"\d{3,8}$", target_name) and store_tokens:
        store_name = normalize_text(binding.store_name)
        if store_name:
            for account in accounts:
                if normalize_text(account.account_name) == store_name:
                    return account
        for account in accounts:
            account_tokens = _account_name_match_tokens(account.account_name)
            if store_tokens.intersection(account_tokens):
                return account

    exact_matches = [account for account in accounts if normalize_text(account.account_name) == target_name]
    if exact_matches:
        return exact_matches[0]

    for account in accounts:
        account_name = normalize_text(account.account_name)
        if not account_name:
            continue
        if target_name in account_name or account_name in target_name:
            return account

    target_tokens = _account_name_match_tokens(binding.account_name)
    if not target_tokens:
        return None
    for account in accounts:
        account_tokens = _account_name_match_tokens(account.account_name)
        if target_tokens.intersection(account_tokens):
            return account

    # 权限表部分行的账号名是业务备注，例如“琴江路店（只做回收）”；
    # 同一行的店铺名称或抖音来客店名更接近巨量本地推账号名，因此作为最后兜底。
    for account in accounts:
        account_tokens = _account_name_match_tokens(account.account_name)
        if store_tokens.intersection(account_tokens):
            return account
    return None


def _first_metric_value(metrics: dict[str, Any], aliases: list[str]) -> Decimal:
    """按别名顺序读取一个指标。"""
    for alias in aliases:
        if alias in metrics:
            return _decimal(metrics.get(alias))
    return Decimal("0")


def _collect_report_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    """提取报表行，兼容新版和旧版返回结构。"""
    data = response.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []

    for key in ["list", "rows", "reports", "report", "data", "items", "data_list"]:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _flatten_metrics(row: dict[str, Any]) -> dict[str, Any]:
    """把报表行里的 metrics 和顶层字段合并，便于按别名读取。"""
    metrics: dict[str, Any] = {}
    nested_metrics = row.get("metrics")
    if isinstance(nested_metrics, dict):
        metrics.update(nested_metrics)
    for key, value in row.items():
        if key not in {"metrics", "dimensions"}:
            metrics.setdefault(key, value)
    return metrics


def _metrics_from_report_response(response: dict[str, Any]) -> tuple[Decimal, int, Decimal]:
    """从报表响应中汇总消耗、转化数和转化成本。"""
    spend = Decimal("0")
    conversions = 0
    direct_conversion_cost_values: list[Decimal] = []

    for row in _collect_report_rows(response):
        metrics = _flatten_metrics(row)
        spend += _first_metric_value(metrics, REPORT_FIELD_ALIASES["spend_yuan"])
        conversions += _int_value(_first_metric_value(metrics, REPORT_FIELD_ALIASES["conversion_count"]))
        direct_cost = _first_metric_value(metrics, REPORT_FIELD_ALIASES["conversion_cost_yuan"])
        if direct_cost > 0:
            direct_conversion_cost_values.append(direct_cost)

    if conversions > 0:
        conversion_cost = spend / Decimal(conversions)
    elif direct_conversion_cost_values:
        conversion_cost = sum(direct_conversion_cost_values, Decimal("0")) / Decimal(len(direct_conversion_cost_values))
    else:
        conversion_cost = Decimal("0")

    return _money(spend), conversions, _money(conversion_cost)


def fetch_account_report_metrics(
    access_token: str,
    account_id: str,
    start_date: date,
    end_date: date,
) -> tuple[Decimal, int, Decimal, dict[str, Any]]:
    """查询一个账号在指定日期范围内的本地推投放指标。"""
    local_fields = ["stat_cost", "convert_cnt", "conversion_cost"]
    local_params = {
        "local_account_id": account_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "metrics": local_fields,
        "time_granularity": "TIME_GRANULARITY_TOTAL",
        "page": 1,
        "page_size": 1000,
    }
    local_response = _get_ocean_json("/open_api/v3.0/local/report/account/get/", access_token, local_params)
    if _response_success(local_response):
        spend, conversions, conversion_cost = _metrics_from_report_response(local_response)
        return spend, conversions, conversion_cost, local_response

    fields = ["cost", "stat_cost", "convert", "convert_cnt", "convert_cost", "conversion_cost"]
    old_params = {
        "advertiser_id": account_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "fields": fields,
        "time_granularity": "STAT_TIME_GRANULARITY_DAILY",
        "page": 1,
        "page_size": 1000,
    }
    old_response = _get_ocean_json("/open_api/2/report/advertiser/get/", access_token, old_params)
    if _response_success(old_response):
        spend, conversions, conversion_cost = _metrics_from_report_response(old_response)
        return spend, conversions, conversion_cost, old_response

    custom_params = {
        "advertiser_id": account_id,
        "data_topic": "BASIC_DATA",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "dimensions": ["stat_time_day"],
        "metrics": fields,
        "page": 1,
        "page_size": 1000,
    }
    custom_response = _get_ocean_json("/open_api/v3.0/report/custom/get/", access_token, custom_params)
    if not _response_success(custom_response):
        message = (
            custom_response.get("message")
            or custom_response.get("msg")
            or old_response.get("message")
            or old_response.get("msg")
            or local_response.get("message")
            or local_response.get("msg")
            or "巨量报表接口返回失败"
        )
        raise RuntimeError(str(message))

    spend, conversions, conversion_cost = _metrics_from_report_response(custom_response)
    return spend, conversions, conversion_cost, custom_response


def _balance_from_response(response: dict[str, Any], account_id: str, amount_scale: Decimal = Decimal("1")) -> Decimal:
    """从余额接口响应中提取指定账号余额。"""
    data = response.get("data")
    candidates: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for key in ["list", "accounts", "account_list", "funds", "fund_list"]:
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend([item for item in value if isinstance(item, dict)])
        candidates.append(data)
    elif isinstance(data, list):
        candidates.extend([item for item in data if isinstance(item, dict)])

    for item in candidates:
        item_id = str(item.get("account_id") or item.get("advertiser_id") or item.get("id") or "")
        if item_id and item_id != str(account_id):
            continue
        for key in ["balance", "total_balance", "account_balance", "cash_balance"]:
            if key in item:
                return _money(_decimal(item.get(key)) * amount_scale)
    return Decimal("0.00")


def fetch_account_balance(access_token: str, account_id: str, account_type: str = "LOCAL") -> tuple[Decimal, dict[str, Any]]:
    """查询一个本地推账号余额。"""
    fund_response = _get_ocean_json(
        "/open_api/v3.0/account/fund/get/",
        access_token,
        {"account_ids": [int(account_id)] if str(account_id).isdigit() else [account_id], "account_type": account_type or "LOCAL"},
    )
    if _response_success(fund_response):
        return _balance_from_response(fund_response, account_id, Decimal("0.01")), fund_response

    old_response = _get_ocean_json(
        "/open_api/2/advertiser/fund/get/",
        access_token,
        {"advertiser_id": account_id},
    )
    if not _response_success(old_response):
        message = old_response.get("message") or old_response.get("msg") or "巨量余额接口返回失败"
        raise RuntimeError(str(message))
    return _balance_from_response(old_response, account_id), old_response


def _create_metric_record(
    binding: JlyqLocalAccountBinding,
    account: RemoteAccount,
    range_type: str,
    start_date: date,
    end_date: date,
    data_month: str,
    snapshot: MetricSnapshot,
) -> JlyqLocalPromotionMetric:
    """生成待入库的指标记录。"""
    now = datetime.now()
    return JlyqLocalPromotionMetric(
        binding_key=binding.binding_key,
        store_name=binding.store_name,
        douyin_store_name=binding.douyin_store_name,
        account_source_column=binding.account_source_column,
        account_id=account.account_id,
        account_name=account.account_name or binding.account_name,
        account_key=make_account_key(account.account_id, account.account_name or binding.account_name),
        range_type=range_type,
        range_start_date=start_date,
        range_end_date=end_date,
        data_month=data_month,
        spend_yuan=snapshot.spend_yuan,
        conversion_count=snapshot.conversion_count,
        conversion_cost_yuan=snapshot.conversion_cost_yuan,
        balance_yuan=snapshot.balance_yuan,
        sync_status="success",
        error_message="",
        raw_report_json=json.dumps(snapshot.raw_report or {}, ensure_ascii=False)[:20000],
        raw_fund_json=json.dumps(snapshot.raw_fund or {}, ensure_ascii=False)[:20000],
        synced_at=now,
        created_at=now,
        updated_at=now,
    )


def sync_jlyq_local_promotion_metrics() -> dict[str, Any]:
    """同步巨量本地推昨天和本月累计数据，并覆盖当前月旧数据。"""
    ranges = get_display_ranges()
    db = SessionLocal()
    sync_log = DataSyncLog(
        sync_type="jlyq_local_promotion_sync",
        data_month=ranges.data_month,
        status="running",
        started_at=datetime.now(),
    )
    db.add(sync_log)
    db.commit()
    db.refresh(sync_log)

    try:
        access_token = get_valid_access_token()
        bindings = load_permission_bindings()
        remote_accounts = build_remote_account_catalog(access_token)

        prepared_records: list[JlyqLocalPromotionMetric] = []
        unresolved_bindings: list[dict[str, Any]] = []
        failed_bindings: list[dict[str, Any]] = []

        for binding in bindings:
            account = _match_remote_account(binding, remote_accounts)
            if account is None or not account.account_id:
                unresolved_bindings.append({
                    "row_number": binding.row_number,
                    "account_name": binding.account_name,
                    "store_name": binding.store_name,
                })
                continue

            try:
                balance_yuan, raw_fund = fetch_account_balance(access_token, account.account_id, account.account_type)
                yesterday_spend, yesterday_conversions, yesterday_conversion_cost, raw_yesterday = fetch_account_report_metrics(
                    access_token,
                    account.account_id,
                    ranges.yesterday,
                    ranges.yesterday,
                )
                month_spend, month_conversions, month_conversion_cost, raw_month = fetch_account_report_metrics(
                    access_token,
                    account.account_id,
                    ranges.month_start,
                    ranges.yesterday,
                )

                prepared_records.append(
                    _create_metric_record(
                        binding,
                        account,
                        "yesterday",
                        ranges.yesterday,
                        ranges.yesterday,
                        ranges.data_month,
                        MetricSnapshot(
                            spend_yuan=yesterday_spend,
                            conversion_count=yesterday_conversions,
                            conversion_cost_yuan=yesterday_conversion_cost,
                            balance_yuan=balance_yuan,
                            raw_report=raw_yesterday,
                            raw_fund=raw_fund,
                        ),
                    )
                )
                prepared_records.append(
                    _create_metric_record(
                        binding,
                        account,
                        "month_to_yesterday",
                        ranges.month_start,
                        ranges.yesterday,
                        ranges.data_month,
                        MetricSnapshot(
                            spend_yuan=month_spend,
                            conversion_count=month_conversions,
                            conversion_cost_yuan=month_conversion_cost,
                            balance_yuan=balance_yuan,
                            raw_report=raw_month,
                            raw_fund=raw_fund,
                        ),
                    )
                )
            except Exception as exc:
                logger.warning("同步巨量本地推账号失败: %s %s", binding.account_name, exc)
                failed_bindings.append({
                    "row_number": binding.row_number,
                    "account_name": binding.account_name,
                    "store_name": binding.store_name,
                    "error": str(exc),
                })

        if prepared_records:
            db.query(JlyqLocalPromotionMetric).filter(
                JlyqLocalPromotionMetric.data_month == ranges.data_month
            ).delete(synchronize_session=False)
            for record in prepared_records:
                db.add(record)
            db.commit()

        sync_log.status = "success" if prepared_records else "failed"
        sync_log.total_records = len(prepared_records)
        sync_log.error_message = "" if prepared_records else "未写入任何巨量本地推数据"
        sync_log.finished_at = datetime.now()
        db.commit()

        return {
            "success": bool(prepared_records),
            "data_month": ranges.data_month,
            "records": len(prepared_records),
            "binding_count": len(bindings),
            "remote_account_count": len(remote_accounts),
            "unresolved_count": len(unresolved_bindings),
            "failed_count": len(failed_bindings),
            "unresolved_bindings": unresolved_bindings[:30],
            "failed_bindings": failed_bindings[:30],
        }
    except Exception as exc:
        db.rollback()
        sync_log.status = "failed"
        sync_log.error_message = str(exc)
        sync_log.finished_at = datetime.now()
        db.add(sync_log)
        db.commit()
        logger.exception("巨量本地推同步失败: %s", exc)
        return {
            "success": False,
            "error": str(exc),
            "data_month": ranges.data_month,
            "records": 0,
        }
    finally:
        db.close()


def _empty_metric_payload(range_type: str, start_date: date, end_date: date) -> dict[str, Any]:
    """生成空指标结构，保证前端展示不报错。"""
    return {
        "range_type": range_type,
        "range_start_date": start_date.isoformat(),
        "range_end_date": end_date.isoformat(),
        "spend_yuan": "0.00",
        "conversion_count": 0,
        "conversion_cost_yuan": "0.00",
        "balance_yuan": "0.00",
        "synced_at": "",
        "has_data": False,
    }


def _metric_to_payload(metric: JlyqLocalPromotionMetric | None, range_type: str, start_date: date, end_date: date) -> dict[str, Any]:
    """把数据库指标转换为前端响应结构。"""
    if metric is None:
        return _empty_metric_payload(range_type, start_date, end_date)
    return {
        "range_type": metric.range_type,
        "range_start_date": metric.range_start_date.isoformat(),
        "range_end_date": metric.range_end_date.isoformat(),
        "spend_yuan": _format_money_fixed(metric.spend_yuan),
        "conversion_count": int(metric.conversion_count or 0),
        "conversion_cost_yuan": _format_money_fixed(metric.conversion_cost_yuan),
        "balance_yuan": _format_money_fixed(metric.balance_yuan),
        "synced_at": metric.synced_at.isoformat() if metric.synced_at else "",
        "has_data": True,
    }


def build_template_text(
    yesterday_metric: dict[str, Any],
    month_metric: dict[str, Any],
    display_date: date,
    collection_data: dict[str, Any] | None = None,
) -> str:
    """按用户要求生成可复制的投流/变现数据模板。"""
    date_text = f"{display_date.month}月{display_date.day}日"
    yesterday_spend = _format_money(yesterday_metric.get("spend_yuan"))
    month_spend = _format_money(month_metric.get("spend_yuan"))
    yesterday_conversions = int(yesterday_metric.get("conversion_count") or 0)
    month_conversions = int(month_metric.get("conversion_count") or 0)
    daily_input = (collection_data or {}).get("daily") or {}
    month_input = (collection_data or {}).get("month") or {}

    def optional_count(value: Any) -> str:
        """未填写的单日数据保持空白，已填写的零值正常展示。"""
        return "" if value is None or value == "" else str(int(value))

    daily_wechat = optional_count(daily_input.get("wechat_count"))
    daily_recycle = optional_count(daily_input.get("recycle_count"))
    daily_sales = optional_count(daily_input.get("sales_count"))
    month_wechat = int(month_input.get("wechat_count") or 0)
    month_recycle = int(month_input.get("recycle_count") or 0)
    month_sales = int(month_input.get("sales_count") or 0)
    month_profit = str(month_input.get("profit_yuan") or "")
    return "\n".join([
        "账号投流/变现数据",
        f"日期：{date_text}",
        f"①投流金额：{yesterday_spend}",
        f"月累计投流金额：{month_spend}",
        f"②私信数量：{yesterday_conversions}",
        f"月累计私信数量：{month_conversions}",
        f"③加微信量：{daily_wechat}",
        f"月加微信累计：{month_wechat}",
        f"④手机回收量：{daily_recycle}",
        f"月累计回收量：{month_recycle}",
        f"⑤手机销售量：{daily_sales}",
        f"月累计销售量：{month_sales}",
        f"本月通过抖音引流的总利润（不用减投流本金）：{month_profit}",
        "目前对超级门店全过程中有什么方面的难点：",
        "",
        "记录以便分析问题，请保证数据的准确记录",
    ])


def _query_metric(
    db: Session,
    binding_key: str,
    range_type: str,
    start_date: date,
    end_date: date,
) -> JlyqLocalPromotionMetric | None:
    """查询指定账号绑定和日期窗口的最新指标。"""
    return (
        db.query(JlyqLocalPromotionMetric)
        .filter(
            JlyqLocalPromotionMetric.binding_key == binding_key,
            JlyqLocalPromotionMetric.range_type == range_type,
            JlyqLocalPromotionMetric.range_start_date == start_date,
            JlyqLocalPromotionMetric.range_end_date == end_date,
        )
        .order_by(JlyqLocalPromotionMetric.synced_at.desc())
        .first()
    )


def get_sidebar_local_promotion_data(
    db: Session,
    group_name: str = "",
    poi_name: str = "",
    extra_store_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    """返回侧边栏巨量引擎页面所需的数据。"""
    ranges = get_display_ranges()
    bindings = find_bindings_for_context(
        group_name=group_name,
        poi_name=poi_name,
        extra_store_names=extra_store_names,
    )

    if not bindings:
        return {
            "authorized": False,
            "message": "当前外部群未配置巨量引擎权限",
            "group_name": group_name,
            "poi_name": poi_name,
            "accounts": [],
            "date": ranges.yesterday.isoformat(),
            "data_month": ranges.data_month,
        }

    accounts: list[dict[str, Any]] = []
    for binding in bindings:
        yesterday_record = _query_metric(db, binding.binding_key, "yesterday", ranges.yesterday, ranges.yesterday)
        month_record = _query_metric(
            db,
            binding.binding_key,
            "month_to_yesterday",
            ranges.month_start,
            ranges.yesterday,
        )
        yesterday_payload = _metric_to_payload(yesterday_record, "yesterday", ranges.yesterday, ranges.yesterday)
        month_payload = _metric_to_payload(
            month_record,
            "month_to_yesterday",
            ranges.month_start,
            ranges.yesterday,
        )
        account_id = ""
        if yesterday_record and yesterday_record.account_id:
            account_id = yesterday_record.account_id
        elif month_record and month_record.account_id:
            account_id = month_record.account_id
        account_name = (
            yesterday_record.account_name
            if yesterday_record and yesterday_record.account_name
            else month_record.account_name
            if month_record and month_record.account_name
            else binding.account_name
        )
        collection_data = build_collection_summary(
            db,
            binding.binding_key,
            ranges.yesterday,
            ranges.data_month,
        )
        accounts.append({
            "binding_key": binding.binding_key,
            "account_id": account_id,
            "account_name": account_name,
            "store_name": binding.store_name,
            "douyin_store_name": binding.douyin_store_name,
            "account_source_column": binding.account_source_column,
            "yesterday": yesterday_payload,
            "month": month_payload,
            "template_text": build_template_text(
                yesterday_payload,
                month_payload,
                ranges.yesterday,
                collection_data,
            ),
            "collection": collection_data,
            "supplemental": {
                "day_balance_yuan": yesterday_payload["balance_yuan"],
                "month_balance_yuan": month_payload["balance_yuan"],
                "day_conversion_cost_yuan": yesterday_payload["conversion_cost_yuan"],
                "month_conversion_cost_yuan": month_payload["conversion_cost_yuan"],
            },
            "has_data": bool(yesterday_payload.get("has_data") or month_payload.get("has_data")),
        })

    latest_sync = (
        db.query(JlyqLocalPromotionMetric.synced_at)
        .filter(JlyqLocalPromotionMetric.data_month == ranges.data_month)
        .order_by(JlyqLocalPromotionMetric.synced_at.desc())
        .first()
    )

    return {
        "authorized": True,
        "message": "success",
        "group_name": group_name,
        "poi_name": poi_name,
        "accounts": accounts,
        "date": ranges.yesterday.isoformat(),
        "data_month": ranges.data_month,
        "month_start_date": ranges.month_start.isoformat(),
        "month_end_date": ranges.yesterday.isoformat(),
        "last_sync_at": latest_sync[0].isoformat() if latest_sync and latest_sync[0] else "",
    }
