"""
WeCom 外部群组 -> 抖音 POI 映射。

此功能用于消除重复的 POI 名称（相同的 poi_name 但不同的 poi_id）：

建议先从 XLSX 映射文件中查找精确的 group_name -> poi_id 对应关系，然后再根据 poi_id 查询数据库。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoreGroupMappingRow:
    poi_name: str
    poi_id: str
    group_name: str


_CACHE: Optional[Dict[str, List[StoreGroupMappingRow]]] = None
_CACHE_MTIME: Optional[float] = None


def _normalize_group_name(name: str) -> str:
    """归一化群名，减少空格、括号、评级前缀和营销后缀带来的差异。"""
    name = (name or "").strip()
    name = name.replace("（", "(").replace("）", ")")
    # 去掉全部空白字符，包含全角空格。
    name = re.sub(r"\s+", "", name)
    # 外部群评级会随月份调整，映射表需要在评级变化后仍能命中。
    if re.match(r"^[A-Za-z][+\-]?(?=极修匠)", name):
        name = re.sub(r"^[A-Za-z][+\-]?(?=极修匠)", "", name, count=1)
    # “超级门店”是企微群名里的营销后缀，不属于门店名，去掉后可兼容新增后缀的群。
    name = name.replace("超级门店", "")
    return name


def default_mapping_file() -> Path:
    """
    默认映射文件位置：<repo_root>/测试文件/重复门店名_poi_id对照表_补全群名.xlsx

    调用方可以通过公共接口参数传入自定义路径。
    """
    return (
        Path(__file__).resolve().parents[3]
        / "测试文件"
        / "重复门店名_poi_id对照表_补全群名.xlsx"
    )


def load_group_store_mapping(mapping_file: Optional[Path] = None) -> Dict[str, List[StoreGroupMappingRow]]:
    """
    加载 group_name -> 候选 POI 的映射，一个群名可以对应多行门店。

    只加载“群名”非空的行，并按文件修改时间自动刷新缓存。
    """
    global _CACHE, _CACHE_MTIME

    path = mapping_file or default_mapping_file()
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
        logger.warning("openpyxl is not installed; cannot load mapping file: %s", path)
        return {}

    mapping: Dict[str, List[StoreGroupMappingRow]] = {}
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
            str(v).strip(): i for i, v in enumerate(header) if v is not None and str(v).strip()
        }

        poi_name_idx = header_map.get("门店名称(poi_name)", 0)
        poi_id_idx = header_map.get("门店ID(poi_id)", 1)
        group_name_idx = header_map.get("群名", 2)

        for row in it:
            if not row or all(v is None for v in row):
                continue

            poi_name = str(row[poi_name_idx] or "").strip() if poi_name_idx < len(row) else ""
            poi_id = str(row[poi_id_idx] or "").strip() if poi_id_idx < len(row) else ""
            group_name = str(row[group_name_idx] or "").strip() if group_name_idx < len(row) else ""

            if not poi_id or not group_name:
                continue

            key = _normalize_group_name(group_name)
            if not key:
                continue

            mapping.setdefault(key, []).append(
                StoreGroupMappingRow(poi_name=poi_name, poi_id=poi_id, group_name=group_name)
            )
    finally:
        try:
            wb.close()  # type: ignore[name-defined]
        except Exception:
            pass

    _CACHE = mapping
    _CACHE_MTIME = mtime
    return mapping


def get_mapping_candidates(group_name: str, mapping_file: Optional[Path] = None) -> List[StoreGroupMappingRow]:
    """返回指定群名在映射表中的候选门店，数量可能为 0 到多行。"""
    mapping = load_group_store_mapping(mapping_file=mapping_file)
    return mapping.get(_normalize_group_name(group_name), [])
