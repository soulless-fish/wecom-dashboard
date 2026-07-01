"""
门店电话映射服务。

从项目根目录的 Excel 文件读取门店电话，按 poi_id 建立映射，并基于文件修改时间做缓存。
只要 Excel 文件没有变更，就不会重复解析；文件被替换后会自动重新加载。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorePhoneMappingRow:
    poi_name: str
    poi_id: str
    phones: Tuple[str, ...]
    display_phone: str


_CACHE: Optional[Dict[str, StorePhoneMappingRow]] = None
_CACHE_MTIME: Optional[float] = None


def default_phone_file() -> Path:
    """返回默认门店电话表路径：<repo_root>/门店记录表.xlsx。"""
    return Path(__file__).resolve().parents[3] / "门店记录表.xlsx"


def _normalize_value(value) -> str:
    text = str(value or "").strip()
    if not text or text in {"None", "null"}:
        return ""
    # Excel 数值列可能被读成 12345678901.0，这里统一收敛成纯数字字符串。
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def _normalize_phone_value(value) -> str:
    """规整电话字段，并过滤 Excel 中的短占位值。"""
    phone = _normalize_value(value)
    if not phone:
        return ""

    # 门店记录表中大量空电话被写成了单独的 7，这类短数字不能当作门店电话。
    digit_count = len(re.sub(r"\D+", "", phone))
    if digit_count < 7:
        return ""
    return phone


def _collect_row_phones(row, phone_indexes) -> Tuple[str, ...]:
    phones = []
    for idx in phone_indexes:
        if idx >= len(row):
            continue
        phone = _normalize_phone_value(row[idx])
        if phone and phone not in phones:
            phones.append(phone)
    return tuple(phones)


def load_store_phone_mapping(phone_file: Optional[Path] = None) -> Dict[str, StorePhoneMappingRow]:
    """从 Excel 读取 poi_id -> 门店电话映射，并按文件修改时间自动刷新缓存。"""
    global _CACHE, _CACHE_MTIME

    path = phone_file or default_phone_file()
    if not path.exists():
        return {}

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}

    if _CACHE is not None and _CACHE_MTIME == mtime:
        return _CACHE

    try:
        import openpyxl
    except ImportError:
        logger.warning("openpyxl is not installed; cannot load phone mapping file: %s", path)
        return {}

    mapping: Dict[str, StorePhoneMappingRow] = {}
    wb = None
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        if not header:
            _CACHE = {}
            _CACHE_MTIME = mtime
            return _CACHE

        header_map = {
            str(value).strip(): index
            for index, value in enumerate(header)
            if value is not None and str(value).strip()
        }

        poi_name_idx = header_map.get("门店名称", 0)
        poi_id_idx = header_map.get("门店ID", 1)
        phone_indexes = [
            index for name, index in header_map.items()
            if name.startswith("营业电话")
        ]

        for row in it:
            if not row or all(value is None for value in row):
                continue

            poi_id = _normalize_value(row[poi_id_idx] if poi_id_idx < len(row) else "")
            if not poi_id:
                continue

            poi_name = _normalize_value(row[poi_name_idx] if poi_name_idx < len(row) else "")
            phones = _collect_row_phones(row, phone_indexes)

            existing = mapping.get(poi_id)
            if existing:
                merged_phones = list(existing.phones)
                for phone in phones:
                    if phone not in merged_phones:
                        merged_phones.append(phone)
                phones = tuple(merged_phones)
                poi_name = poi_name or existing.poi_name

            mapping[poi_id] = StorePhoneMappingRow(
                poi_name=poi_name,
                poi_id=poi_id,
                phones=phones,
                display_phone=";".join(phones) if phones else "无",
            )
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass

    _CACHE = mapping
    _CACHE_MTIME = mtime
    return mapping


def get_store_phone_row(
    poi_id: Optional[str],
    mapping: Optional[Dict[str, StorePhoneMappingRow]] = None,
    phone_file: Optional[Path] = None,
) -> Optional[StorePhoneMappingRow]:
    """按 poi_id 返回门店电话映射行。"""
    normalized_poi_id = _normalize_value(poi_id)
    if not normalized_poi_id:
        return None
    resolved_mapping = mapping if mapping is not None else load_store_phone_mapping(phone_file=phone_file)
    return resolved_mapping.get(normalized_poi_id)


def get_store_phone_display(
    poi_id: Optional[str],
    mapping: Optional[Dict[str, StorePhoneMappingRow]] = None,
    phone_file: Optional[Path] = None,
    default: str = "无",
) -> str:
    """返回用分号拼接的门店电话；没有有效电话时返回默认文案。"""
    row = get_store_phone_row(poi_id=poi_id, mapping=mapping, phone_file=phone_file)
    if not row:
        return default
    return row.display_phone or default
