"""
巨量引擎本地推权限表解析。

权限来源为 JLYQ/极修匠店铺信息汇总.xlsx。表里没有直接的企微群ID，
因此这里把店铺名称、抖音来客店名和巨量账号名称拆成稳定绑定，再用群名
或侧边栏已匹配到的门店名做归一化匹配。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
from typing import Iterable


logger = logging.getLogger(__name__)

MODULE_ROOT = Path(__file__).resolve().parent
PERMISSION_FILE = MODULE_ROOT / "极修匠店铺信息汇总.xlsx"


@dataclass(frozen=True)
class JlyqLocalAccountBinding:
    """权限表中一条门店和一个巨量本地推子账户的绑定。"""

    row_number: int
    store_name: str
    douyin_store_name: str
    account_name: str
    account_source_column: str
    binding_key: str


_CACHE: list[JlyqLocalAccountBinding] | None = None
_CACHE_MTIME: float | None = None


def normalize_text(value: str) -> str:
    """归一化名称，兼容全半角括号、评级前缀和营销后缀。"""
    text = str(value or "").strip()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^[A-Za-z][+\-]?(?=极修匠)", "", text, count=1)
    text = text.replace("超级门店", "")
    return text


def _short_store_token(value: str) -> str:
    """提取门店短名称，用于匹配群名括号里的门店关键词。"""
    text = normalize_text(value)
    matches = re.findall(r"\(([^()]+)\)", text)
    if matches:
        text = matches[-1]

    replacements = [
        "极修匠",
        "手机回收",
        "手机维修中心",
        "手机维修",
        "手机快修",
        "科技",
        "数码",
        "通讯",
        "回收",
        "维修",
        "中心",
    ]
    for item in replacements:
        text = text.replace(item, "")

    text = re.sub(r"^[A-Za-z][+\-]?", "", text)
    if text.endswith("店") and len(text) > 1:
        text = text[:-1]
    return text.strip()


def build_match_tokens(*values: str) -> set[str]:
    """为一个名称集合生成可匹配的全名和短名 token。"""
    tokens: set[str] = set()
    for value in values:
        text = normalize_text(value)
        if not text:
            continue
        tokens.add(text)
        short_token = _short_store_token(text)
        if short_token:
            tokens.add(short_token)
        for match in re.findall(r"\(([^()]+)\)", text):
            match_text = normalize_text(match)
            if match_text:
                tokens.add(match_text)
                if match_text.endswith("店") and len(match_text) > 1:
                    tokens.add(match_text[:-1])
    return {item for item in tokens if item}


def make_binding_key(store_name: str, account_name: str, source_column: str) -> str:
    """生成稳定绑定键，避免同名账号跨门店时互相覆盖。"""
    source = f"{normalize_text(store_name)}|{normalize_text(account_name)}|{source_column}"
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
    return f"jlyq_{digest}"


def make_account_key(account_id: str, account_name: str) -> str:
    """生成远端账号匹配键，有账号ID时优先使用账号ID。"""
    account_id = str(account_id or "").strip()
    if account_id:
        return f"id:{account_id}"
    return f"name:{normalize_text(account_name)}"


def _unique_bindings(bindings: Iterable[JlyqLocalAccountBinding]) -> list[JlyqLocalAccountBinding]:
    """按绑定键去重，并保持 Excel 原始顺序。"""
    result: list[JlyqLocalAccountBinding] = []
    seen: set[str] = set()
    for binding in bindings:
        if binding.binding_key in seen:
            continue
        seen.add(binding.binding_key)
        result.append(binding)
    return result


def load_permission_bindings(permission_file: Path | None = None) -> list[JlyqLocalAccountBinding]:
    """读取权限表，把每个门店拆成一个或多个巨量本地推账号绑定。"""
    global _CACHE, _CACHE_MTIME

    path = permission_file or PERMISSION_FILE
    if not path.exists():
        logger.warning("巨量引擎权限表不存在: %s", path)
        return []

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []

    if _CACHE is not None and _CACHE_MTIME == mtime:
        return _CACHE

    try:
        import openpyxl
    except ImportError:
        logger.warning("openpyxl 未安装，无法读取巨量引擎权限表: %s", path)
        return []

    bindings: list[JlyqLocalAccountBinding] = []
    wb = None
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            _CACHE = []
            _CACHE_MTIME = mtime
            return _CACHE

        header_map = {
            str(value).strip(): index
            for index, value in enumerate(header)
            if value is not None and str(value).strip()
        }
        store_idx = header_map.get("店铺名称", 0)
        account_idx_list = [
            ("账户名称2", header_map.get("账户名称2", 1)),
            ("账户名称3", header_map.get("账户名称3", 2)),
        ]
        douyin_store_idx = header_map.get("抖音来客店名", 3)

        for row_number, row in enumerate(rows, start=2):
            if not row or all(value is None or str(value).strip() == "" for value in row):
                continue

            store_name = str(row[store_idx] or "").strip() if store_idx < len(row) else ""
            douyin_store_name = (
                str(row[douyin_store_idx] or "").strip()
                if douyin_store_idx < len(row)
                else ""
            )
            if not store_name and not douyin_store_name:
                continue

            account_values: list[tuple[str, str]] = []
            for column_name, column_index in account_idx_list:
                account_name = str(row[column_index] or "").strip() if column_index < len(row) else ""
                if account_name:
                    account_values.append((column_name, account_name))

            if not account_values and store_name:
                account_values.append(("店铺名称", store_name))

            for source_column, account_name in account_values:
                binding_key = make_binding_key(store_name, account_name, source_column)
                bindings.append(
                    JlyqLocalAccountBinding(
                        row_number=row_number,
                        store_name=store_name,
                        douyin_store_name=douyin_store_name,
                        account_name=account_name,
                        account_source_column=source_column,
                        binding_key=binding_key,
                    )
                )
    except Exception as exc:
        logger.exception("读取巨量引擎权限表失败: %s", exc)
        return []
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass

    _CACHE = _unique_bindings(bindings)
    _CACHE_MTIME = mtime
    return _CACHE


def find_bindings_for_context(
    group_name: str = "",
    poi_name: str = "",
    extra_store_names: Iterable[str] | None = None,
    permission_file: Path | None = None,
) -> list[JlyqLocalAccountBinding]:
    """按企微群名和门店名查找可展示的巨量本地推账号。"""
    query_tokens = build_match_tokens(group_name, poi_name, *(extra_store_names or []))
    if not query_tokens:
        return []

    matched: list[JlyqLocalAccountBinding] = []
    for binding in load_permission_bindings(permission_file=permission_file):
        row_tokens = build_match_tokens(binding.store_name, binding.douyin_store_name)
        if not row_tokens:
            continue

        has_match = False
        for query_token in query_tokens:
            for row_token in row_tokens:
                if query_token == row_token:
                    has_match = True
                    break
                if len(query_token) >= 3 and query_token in row_token:
                    has_match = True
                    break
                if len(row_token) >= 3 and row_token in query_token:
                    has_match = True
                    break
            if has_match:
                break

        if has_match:
            matched.append(binding)

    return _unique_bindings(matched)
