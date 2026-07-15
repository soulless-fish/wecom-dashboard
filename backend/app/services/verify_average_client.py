"""
生意经经营概览客户端。

该模块只读取经营概览中的核销总金额和门店数，按“核销金额 ÷ 门店数”计算
侧边栏需要的平均核销金额。请求使用项目已有的生意经 Cookie，不保存或输出
Cookie 内容。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from time import time
from typing import Any, Optional
from urllib.parse import unquote

import httpx


class VerifyAverageError(Exception):
    """平均核销金额抓取基础异常。"""


class VerifyAverageAuthError(VerifyAverageError):
    """生意经登录态失效异常。"""


class VerifyAverageClient:
    """读取生意经经营概览并计算两个日期口径的平均核销金额。"""

    BASE_URL = "https://www.life-data.cn"
    DITO_API = "/api/dito/query"
    GROUP_ID = "1793470223284235"

    def __init__(
        self,
        cookie: str,
        life_account_id: str,
        csrf_token: Optional[str] = None,
        group_id: Optional[str] = None,
    ):
        """初始化客户端，复用现有生意经登录态配置。"""
        self.cookie = cookie
        self.life_account_id = life_account_id
        self.csrf_token = csrf_token or ""
        self.group_id = str(group_id or self.GROUP_ID)

    def _headers(self) -> dict[str, str]:
        """构造与生意经经营概览页面一致的请求头。"""
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "content-type": "application/json",
            "cookie": self.cookie,
            "life-account-id": self.life_account_id,
            "root-life-account-id": self.life_account_id,
            "origin": self.BASE_URL,
            "referer": f"{self.BASE_URL}/trade/overview?groupid={self.group_id}",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
            ),
            "ac-tag": "ka_50h",
            "agw-js-conv": "str",
            "rpc-persist-life-merchant-role": "473489608",
            "rpc-persist-life-merchant-switch-role": "1",
            "sec-fetch-site": "same-origin",
        }
        if self.csrf_token:
            headers["x-secsdk-csrf-token"] = self.csrf_token
        return headers

    def _cookie_expired(self) -> bool:
        """根据 Cookie 中的 sid_guard 提供诊断性到期判断，不作为唯一鉴权依据。"""
        cookie_values: dict[str, str] = {}
        for item in self.cookie.split(";"):
            if "=" not in item:
                continue
            name, value = item.strip().split("=", 1)
            cookie_values.setdefault(name.strip(), value.strip())

        sid_guard = unquote(cookie_values.get("sid_guard", ""))
        parts = sid_guard.split("|")
        if len(parts) < 2 or not parts[1].isdigit():
            # 某些登录态不带 sid_guard，交给远端接口继续判断。
            return False
        return int(parts[1]) <= int(time())

    def _payload(self, start_date: str, end_date: str, date_type: str) -> dict[str, Any]:
        """构造经营概览 DITO 请求，取到核销金额和门店数两个原始指标。"""
        return {
            "biz_params": {
                "path": "/dito/pc/business/page",
                "query": {"groupid": self.group_id},
                "first_render": False,
                "common_params": {
                    "is_single": 0,
                    "start_date": start_date,
                    "end_date": end_date,
                    "date_type": date_type,
                },
                "module_params": {
                    "CoreIndicatorAndTrend": {"business_tab": "all"},
                    "BusinessOverviewTab": {},
                    "IndicatorLayout": {},
                },
            },
            "dito_params": {},
        }

    @staticmethod
    def _to_decimal(value: Any) -> Optional[Decimal]:
        """把接口中的数字、带逗号数字或金额文本转换为 Decimal。"""
        if value is None or value == "":
            return None
        try:
            text = str(value).replace(",", "").replace("¥", "").strip()
            return Decimal(text)
        except (InvalidOperation, ValueError, TypeError):
            return None

    @classmethod
    def _find_overview_row(cls, payload: Any) -> Optional[dict[str, Any]]:
        """递归查找经营概览返回的 Overview 首行，兼容页面布局层级变化。"""
        if isinstance(payload, dict):
            overview = payload.get("Overview")
            if isinstance(overview, dict):
                rows = overview.get("data")
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict) and (
                            "verify_gmv" in row or "verify_amount_1d" in row
                        ):
                            return row
            for value in payload.values():
                found = cls._find_overview_row(value)
                if found:
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = cls._find_overview_row(value)
                if found:
                    return found
        return None

    @classmethod
    def _period_result(
        cls,
        payload: dict[str, Any],
        *,
        label: str,
        start_date: str,
        end_date: str,
        date_type: str,
    ) -> dict[str, Any]:
        """从接口响应计算单个日期口径的平均核销金额。"""
        row = cls._find_overview_row(payload)
        if not row:
            raise VerifyAverageError(f"{label}经营概览未返回核销数据")

        # verify_gmv 和 poi_num 均为生意经接口原始口径，金额单位是分。
        amount_fen = cls._to_decimal(
            row.get("verify_gmv", row.get("verify_amount_1d"))
        )
        store_count = cls._to_decimal(
            row.get("poi_num", row.get("store_num", row.get("store_count")))
        )
        if amount_fen is None or store_count is None or store_count <= 0:
            raise VerifyAverageError(f"{label}经营概览缺少有效核销金额或门店数")

        average_yuan = (amount_fen / store_count / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return {
            "label": label,
            "date_type": date_type,
            "start_date": start_date,
            "end_date": end_date,
            "verify_amount_fen": int(amount_fen),
            "store_count": int(store_count),
            "average_verify_amount_yuan": float(average_yuan),
            "average_verify_amount_display": f"¥{average_yuan:,.2f}",
        }

    async def _fetch_period(
        self,
        client: httpx.AsyncClient,
        *,
        label: str,
        start_date: str,
        end_date: str,
        date_type: str,
    ) -> dict[str, Any]:
        """请求一个日期口径并返回计算后的平均值。"""
        response = await client.post(
            f"{self.BASE_URL}{self.DITO_API}",
            headers=self._headers(),
            json=self._payload(start_date, end_date, date_type),
        )
        if response.status_code in (401, 403):
            raise VerifyAverageAuthError("生意经 Cookie 已失效")
        try:
            response.raise_for_status()
            if not response.content.strip():
                # 生意经在登录态失效时可能返回 HTTP 200 空响应，不能再当作正常 JSON 处理。
                raise VerifyAverageAuthError("生意经登录态已过期，请更新来客后台Cookie")
            payload = response.json()
        except VerifyAverageAuthError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise VerifyAverageError(f"{label}经营概览请求失败") from exc

        if payload.get("code") in (401, 403):
            raise VerifyAverageAuthError("生意经 Cookie 已失效")
        if payload.get("code") != 0:
            raise VerifyAverageError(f"{label}经营概览返回异常")
        return self._period_result(
            payload,
            label=label,
            start_date=start_date,
            end_date=end_date,
            date_type=date_type,
        )

    @staticmethod
    def build_periods(today: Optional[date] = None) -> list[dict[str, str]]:
        """按项目“统计到昨天”的规则生成自然月和近30天日期范围。"""
        current_day = today or datetime.now().date()
        end_day = current_day - timedelta(days=1)
        month_start = end_day.replace(day=1)
        thirty_day_start = end_day - timedelta(days=29)
        return [
            {
                "key": "current_month",
                "label": "当月",
                "date_type": "nature_month",
                "start_date": month_start.isoformat(),
                "end_date": end_day.isoformat(),
            },
            {
                "key": "last_thirty_days",
                "label": "近30天",
                "date_type": "last_thirty_days",
                "start_date": thirty_day_start.isoformat(),
                "end_date": end_day.isoformat(),
            },
        ]

    async def fetch_average_metrics(self, today: Optional[date] = None) -> dict[str, Any]:
        """抓取两个口径并返回侧边栏可直接展示的平均核销金额。"""
        # 不把 sid_guard 当作唯一有效期判断：同一 Cookie 文本可能同时含有不同域的会话，
        # 以实际经营概览响应为准，避免把仍可用的新会话误判为过期。
        periods = self.build_periods(today)
        result: dict[str, Any] = {
            "group_id": self.group_id,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
            for period in periods:
                result[period["key"]] = await self._fetch_period(
                    client,
                    label=period["label"],
                    start_date=period["start_date"],
                    end_date=period["end_date"],
                    date_type=period["date_type"],
                )
        return result
