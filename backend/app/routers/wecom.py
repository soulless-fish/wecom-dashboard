from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
import re
from app.services.wecom_client import WeComClient
from app.services.token_manager import TokenManager
from app.services.store_group_mapping import get_mapping_candidates
from app.services.douyin_computer_cleaning_sync import build_store_computer_cleaning_summary
from app.services.douyin_order_sync import build_store_douyin_order_summary
from app.services.douyin_poi_account_sync import build_store_poi_account_summary
from app.services.douyin_shop_business_status_sync import build_store_business_status_summary
from app.services.fanke_order_sync import build_store_fanke_purchase_summary
from app.services.store_phone_mapping import load_store_phone_mapping, get_store_phone_display, get_store_phone_row
from app.config import get_settings, Settings
from app.database.models import get_db, StorePerformance

router = APIRouter()


class MessageRequest(BaseModel):
    """发送消息请求"""
    chat_id: str
    content: str
    msg_type: str = "text"


class UserInfoResponse(BaseModel):
    """用户信息响应"""
    user_id: str
    name: str | None = None
    department: list[int] | None = None


@router.get("/wecom/user/info")
async def get_user_info(
    code: str,
    settings: Settings = Depends(get_settings)
):
    """
    获取当前用户身份
    通过OAuth2 code换取用户信息
    """
    if not settings.wecom_corp_id or not settings.wecom_secret:
        raise HTTPException(status_code=500, detail="企业微信配置未设置")

    token_manager = TokenManager(settings)
    access_token = await token_manager.get_access_token()

    client = WeComClient(access_token)
    user_info = await client.get_user_info(code)

    return user_info


@router.post("/wecom/message/send")
async def send_message(
    request: MessageRequest,
    settings: Settings = Depends(get_settings)
):
    """
    发送消息到群聊
    """
    if not settings.wecom_corp_id or not settings.wecom_secret:
        raise HTTPException(status_code=500, detail="企业微信配置未设置")

    token_manager = TokenManager(settings)
    access_token = await token_manager.get_access_token()

    client = WeComClient(access_token)
    result = await client.send_message(
        chat_id=request.chat_id,
        content=request.content,
        msg_type=request.msg_type
    )

    return result


@router.get("/wecom/group/info")
async def get_group_info(
    chat_id: str,
    settings: Settings = Depends(get_settings)
):
    """
    获取群聊信息
    注意：需要将自建应用配置到"客户联系"的"可调用应用"列表中
    """
    if not settings.wecom_corp_id or not settings.wecom_secret:
        raise HTTPException(status_code=500, detail="企业微信配置未设置")

    token_manager = TokenManager(settings)
    access_token = await token_manager.get_access_token()

    client = WeComClient(access_token)
    group_info = await client.get_group_chat(chat_id)

    return group_info


class MatchStoreRequest(BaseModel):
    """门店匹配请求"""
    group_name: str


@router.post("/wecom/match-store")
async def match_store_by_group_name(
    request: MatchStoreRequest,
    db: Session = Depends(get_db)
):
    """
    根据群名匹配门店（支持多门店）

    优先使用"重复门店名_poi_id对照表_补全群名.xlsx"做精确匹配（群名 -> poi_id），
    解决同名门店（同poi_name不同poi_id）导致的误匹配问题；若未命中对照表，再降级为括号关键词匹配。

    支持多门店场景：群名中包含多个门店名（用顿号、逗号分隔），如：
    "极修匠-日照（浮来春公馆、B+百货大楼店）超级门店"

    返回格式：
    - 单门店: {"code": 0, "data": {...}}  (向后兼容)
    - 多门店: {"code": 0, "data": {...}, "stores": [{...}, {...}]}
    """
    group_name = request.group_name
    phone_mapping = load_store_phone_mapping()

    def _build_store_payload(
        store: StorePerformance,
        display_poi_id: str | None = None,
        douyin_poi_id: str | None = None,
    ) -> dict:
        source_poi_id = str(store.poi_id or "")
        output_poi_id = str(display_poi_id or source_poi_id)
        order_poi_id = str(douyin_poi_id or output_poi_id or source_poi_id)
        phone_row = get_store_phone_row(output_poi_id, mapping=phone_mapping)
        if phone_row is None and output_poi_id != source_poi_id:
            # 映射表可能指定官方订单使用的新 poi_id，而门店电话表仍保留业绩数据源里的旧 poi_id。
            phone_row = get_store_phone_row(source_poi_id, mapping=phone_mapping)
        fanke_summary = build_store_fanke_purchase_summary(
            db,
            store_phones=phone_row.phones if phone_row else [],
        )
        douyin_order_summary = build_store_douyin_order_summary(db, order_poi_id)
        douyin_poi_account_summary = build_store_poi_account_summary(db, order_poi_id)
        computer_cleaning_summary = build_store_computer_cleaning_summary(db, order_poi_id)
        business_status_summary = build_store_business_status_summary(db, order_poi_id, store.poi_name)
        payload = {
            "poi_id": output_poi_id,
            "poi_name": store.poi_name,
            "store_phone": phone_row.display_phone if phone_row else get_store_phone_display(output_poi_id, mapping=phone_mapping),
            "gmv_yuan": store.gmv_yuan,
            "live_duration_formatted": store.live_duration_formatted,
            "video_count": store.video_count,
            "video_cnt_1d": store.video_cnt_1d,
            "poi_score": float(store.poi_score) if store.poi_score is not None else None,
            "verify_amount_realtime": store.verify_amount_realtime,
            "verify_cert_cnt_realtime": store.verify_cert_cnt_realtime,
            "verify_amount": store.verify_amount,
            "verify_cert_cnt": store.verify_cert_cnt,
            "data_month": store.data_month,
            "data_start_day": store.data_start_day,
            "data_end_day": store.data_end_day,
        }
        payload.update(business_status_summary)
        payload.update(douyin_poi_account_summary)
        payload.update(computer_cleaning_summary)
        payload.update(douyin_order_summary)
        payload.update(fanke_summary)
        return payload

    def _remove_rating_prefix(keyword: str) -> str:
        """
        去掉门店名前面的评级标识
        评级格式：A、A+、A-、B、B+、B-、C、C+、C-、D、D+、D-、S、S+、S- 等
        例如：B+百货大楼店 -> 百货大楼店
              B-帝标大厦店 -> 帝标大厦店
        """
        # 匹配开头的评级：单个字母 + 可选的加号或减号
        return re.sub(r'^[A-Za-z][+\-]?', '', keyword).strip()

    def _remove_super_store_suffix(keyword: str) -> str:
        """去掉企微群名里附加的“超级门店”后缀。"""
        return (keyword or "").replace("超级门店", "").strip()

    def _build_keyword_aliases(keyword: str) -> list[str]:
        """生成门店关键词别名，兼容评级、超级门店后缀和常见简称差异。"""
        aliases = []

        def add_alias(value: str) -> None:
            clean_value = (value or "").strip()
            if clean_value and clean_value not in aliases:
                aliases.append(clean_value)

        add_alias(keyword)
        for item in list(aliases):
            add_alias(_remove_rating_prefix(item))
        for item in list(aliases):
            add_alias(_remove_super_store_suffix(item))
            add_alias(_remove_rating_prefix(_remove_super_store_suffix(item)))

        # 有些商务群名会把门店简称加上“园区”等位置说明，实际 POI 名称仍是原门店名。
        # 有些门店在群名里带“店”，但抖音 POI 名称没有“店”字。
        for item in list(aliases):
            if "园区" in item:
                add_alias(item.replace("园区", ""))
            if item.endswith("店") and len(item) > 1:
                add_alias(item[:-1])

        return aliases

    def _extract_store_keywords(name: str) -> list[str]:
        """提取群名中括号内的门店关键词，支持多个括号、顿号、逗号和空白分隔。"""
        all_brackets = re.findall(r'[（(]([^)）]+)[)）]', name)
        keywords = []
        for bracket_content in all_brackets:
            # 商务已统一要求多门店群名使用空格等常规分隔符，评级里的 B+ 不应再被加号规则拆开。
            parts = re.split(r'[、,，\s]+', bracket_content)
            keywords.extend([kw.strip() for kw in parts if kw.strip()])
        return keywords

    def _poi_name_matches_keyword(poi_name: str, keyword: str) -> bool:
        if not poi_name or not keyword:
            return False
        return any(item and item in poi_name for item in _build_keyword_aliases(keyword))

    def _get_store_by_poi_id(poi_id: str) -> StorePerformance | None:
        if not poi_id:
            return None
        return (
            db.query(StorePerformance)
            .filter(StorePerformance.poi_id == poi_id)
            .order_by(StorePerformance.data_month.desc())
            .first()
        )

    def _get_store_by_mapping_poi_name(candidate, used_poi_ids: set[str]) -> StorePerformance | None:
        """映射表 poi_id 暂无业绩行时，按映射表门店名找同名业绩行作为数据来源。"""
        if not candidate.poi_name:
            return None
        query = db.query(StorePerformance).filter(StorePerformance.poi_name == candidate.poi_name)
        if used_poi_ids:
            query = query.filter(~StorePerformance.poi_id.in_(list(used_poi_ids)))
        return query.order_by(StorePerformance.data_month.desc()).first()

    def _pick_mapping_candidate(candidates, keyword: str, used_poi_ids: set[str]):
        remaining = [c for c in candidates if c.poi_id and c.poi_id not in used_poi_ids]
        if not remaining:
            return None
        if len(candidates) == 1 and len(remaining) == 1:
            # 单门店精确映射已经由映射表指定 poi_id，即使群名简称和实际 POI 名不同也直接采用。
            return remaining[0]

        matched = [c for c in remaining if _poi_name_matches_keyword(c.poi_name or "", keyword)]
        if not matched:
            return None
        if len(matched) == 1:
            return matched[0]

        matched.sort(key=lambda c: len(c.poi_name or ""))
        return matched[0]

    def _query_matches_by_keyword(keyword: str, used_poi_ids: set[str]):
        query = db.query(StorePerformance).filter(StorePerformance.poi_name.contains(keyword))
        if used_poi_ids:
            query = query.filter(~StorePerformance.poi_id.in_(list(used_poi_ids)))
        return query.order_by(StorePerformance.data_month.desc()).all()

    def _query_matches_by_keyword_aliases(keyword: str, used_poi_ids: set[str]):
        """依次用关键词别名查询，返回第一个命中的结果和实际查询词。"""
        for keyword_alias in _build_keyword_aliases(keyword):
            matches = _query_matches_by_keyword(keyword_alias, used_poi_ids)
            if matches:
                return matches, keyword_alias
        return [], keyword

    # 1) 提取群名中的门店关键词，并加载精确映射（群名 -> poi_id）
    store_keywords = _extract_store_keywords(group_name)
    mapping_candidates = get_mapping_candidates(group_name)

    if not store_keywords and not mapping_candidates:
        return {"code": -1, "message": "群名中未找到括号内容"}

    # 2) 先读精确映射（群名 -> poi_id），再补充关键词匹配
    matched_stores = []
    matched_poi_ids = set()
    not_found_keywords = []
    missing_mapping_poi_ids = []

    def _pick_best_match(matches, kw):
        """从多个匹配结果中按相关性选最佳：精确括号匹配优先，poi_name更短的优先"""
        seen_poi_ids = set()
        unique = []
        for m in matches:
            if m.poi_id not in seen_poi_ids:
                seen_poi_ids.add(m.poi_id)
                unique.append(m)
        if len(unique) == 1:
            return unique[0]

        def score(s):
            name = s.poi_name or ""
            bracket = re.search(r'[（(]([^)）]+)[)）]', name)
            bracket_content = bracket.group(1) if bracket else ""
            exact = (bracket_content == kw)
            # 群名包含极修匠时，优先选择极修匠门店，避免同名商圈/地址误命中其他行业门店。
            brand_mismatch = 1 if "极修匠" in group_name and "极修匠" not in name else 0
            return (brand_mismatch, 0 if exact else 1, len(name))

        unique.sort(key=score)
        return unique[0]

    for keyword in store_keywords:
        mapping_candidate = _pick_mapping_candidate(mapping_candidates, keyword, matched_poi_ids)
        if mapping_candidate is not None:
            store = _get_store_by_poi_id(mapping_candidate.poi_id)
            if store and store.poi_id not in matched_poi_ids:
                matched_stores.append(_build_store_payload(store))
                matched_poi_ids.add(store.poi_id)
                continue
            fallback_store = _get_store_by_mapping_poi_name(mapping_candidate, matched_poi_ids)
            if fallback_store:
                matched_stores.append(
                    _build_store_payload(
                        fallback_store,
                        display_poi_id=mapping_candidate.poi_id,
                        douyin_poi_id=mapping_candidate.poi_id,
                    )
                )
                matched_poi_ids.add(mapping_candidate.poi_id)
                matched_poi_ids.add(fallback_store.poi_id)
                continue
            missing_mapping_poi_ids.append(mapping_candidate.poi_id)

        # 查询所有匹配的门店，按相关性选最佳
        matches, search_kw = _query_matches_by_keyword_aliases(keyword, matched_poi_ids)

        if matches:
            store = _pick_best_match(matches, search_kw)
            if store.poi_id not in matched_poi_ids:
                matched_stores.append(_build_store_payload(store))
                matched_poi_ids.add(store.poi_id)
        else:
            not_found_keywords.append(keyword)

    # 4) 若映射表还有未消耗的门店，则按表内顺序补充
    for candidate in mapping_candidates:
        if not candidate.poi_id or candidate.poi_id in matched_poi_ids:
            continue
        store = _get_store_by_poi_id(candidate.poi_id)
        if store:
            matched_stores.append(_build_store_payload(store))
            matched_poi_ids.add(store.poi_id)
            continue
        fallback_store = _get_store_by_mapping_poi_name(candidate, matched_poi_ids)
        if fallback_store:
            matched_stores.append(
                _build_store_payload(
                    fallback_store,
                    display_poi_id=candidate.poi_id,
                    douyin_poi_id=candidate.poi_id,
                )
            )
            matched_poi_ids.add(candidate.poi_id)
            matched_poi_ids.add(fallback_store.poi_id)
        else:
            missing_mapping_poi_ids.append(candidate.poi_id)

    # 5) 返回结果
    if not matched_stores:
        if missing_mapping_poi_ids:
            return {"code": -1, "message": f"对照表匹配到门店ID，但数据库中未找到数据: {', '.join(sorted(set(missing_mapping_poi_ids)))}"}
        return {"code": -1, "message": f"未找到匹配的门店: {', '.join(not_found_keywords)}"}

    # 返回结果（向后兼容：data为第一个门店，stores为所有门店）
    result = {
        "code": 0,
        "data": matched_stores[0],  # 向后兼容
        "stores": matched_stores,   # 多门店数组
    }

    # 如果有部分门店未找到，添加提示
    if not_found_keywords:
        result["warning"] = f"部分门店未找到: {', '.join(not_found_keywords)}"
    if missing_mapping_poi_ids:
        warning = f"对照表中的部分门店ID在数据库中不存在: {', '.join(sorted(set(missing_mapping_poi_ids)))}"
        if result.get("warning"):
            result["warning"] = f"{result['warning']}；{warning}"
        else:
            result["warning"] = warning

    return result


@router.get("/wecom/external-groups")
async def get_external_groups(
    settings: Settings = Depends(get_settings)
):
    """
    获取客户群列表
    注意：需要将自建应用配置到"客户联系"的"可调用应用"列表中
    """
    if not settings.wecom_corp_id or not settings.wecom_secret:
        raise HTTPException(status_code=500, detail="企业微信配置未设置")

    token_manager = TokenManager(settings)
    access_token = await token_manager.get_access_token()

    client = WeComClient(access_token)
    result = await client.get_external_group_list()

    return {
        "code": 0,
        "data": result
    }
