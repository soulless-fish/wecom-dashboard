# AI门店实时数据接口文档

生成日期：2026-05-06

## 一、接口目的

本接口用于把企业微信侧边栏项目中的实时门店经营数据提供给外部群智能客服项目使用。

智能客服项目可以通过 `chat_id` 或 `group_name` 查询当前群对应的门店，并获得：

1. 群到门店的匹配结果
2. 门店基础信息
3. 门店经营指标
4. 视频数和上翻收益达标情况
5. 可直接拼入 AI 上下文的 `summary_text`

## 二、基础信息

| 项目 | 说明 |
|---|---|
| 线上域名 | `https://jxjfix.com` |
| API 前缀 | `/api/v1/ai` |
| 数据来源 | `life.douyin.com`、`www.life-data.cn` |
| 数据库表 | `store_performance` |
| 视频达标口径 | `video_cnt_1d >= 15` |
| 上翻达标口径 | `gmv_yuan >= 2000` |
| 内部令牌 | 如果后端配置了 `INTERNAL_API_TOKEN`，调用方必须带 `X-Internal-Token` 请求头；为空则不校验 |

## 三、接口一：统一 AI 门店上下文

### 请求

```http
POST /api/v1/ai/store-context
Content-Type: application/json
X-Internal-Token: 可选，按线上配置决定
```

```json
{
  "chat_id": "R_xxx",
  "group_name": "B+极修匠-太原(万象城店)",
  "topic": "video_progress",
  "data_month": "2026-05"
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `chat_id` | 否 | 企业微信外部群 ID。只传 `chat_id` 时，接口会尝试调用企业微信接口反查群名 |
| `group_name` | 否 | 企业微信外部群名。推荐调用方直接传入，稳定性最高 |
| `topic` | 否 | AI 当前关注主题，例如 `video_progress`、`upturn_progress` |
| `data_month` | 否 | 数据月份，格式 `YYYY-MM`；不传则取该门店最新月份数据 |

`chat_id` 和 `group_name` 至少应提供一个。实际对接建议优先传 `group_name`。

### 成功响应

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "found": true,
    "topic": "video_progress",
    "store": {
      "chat_id": "R_xxx",
      "group_name": "B+极修匠-太原(万象城店)",
      "store_id": "7481237681621207080",
      "poi_id": "7481237681621207080",
      "store_name": "极修匠手机维修(万象城店)",
      "poi_name": "极修匠手机维修(万象城店)",
      "store_score": 4.8,
      "match_method": "group_name_exact_mapping",
      "match_confidence": 0.98,
      "match_reason": "群名命中映射表，关键词 `万象城店` 对应 poi_id `7481237681621207080`"
    },
    "stores": [
      {
        "chat_id": "R_xxx",
        "group_name": "B+极修匠-太原(万象城店)",
        "store_id": "7481237681621207080",
        "poi_id": "7481237681621207080",
        "store_name": "极修匠手机维修(万象城店)",
        "poi_name": "极修匠手机维修(万象城店)",
        "store_score": 4.8,
        "match_method": "group_name_exact_mapping",
        "match_confidence": 0.98,
        "match_reason": "群名命中映射表，关键词 `万象城店` 对应 poi_id `7481237681621207080`"
      }
    ],
    "period": {
      "data_month": "2026-05",
      "data_start_day": "2026-05-01",
      "data_end_day": "2026-05-05",
      "data_start_day_num": 1,
      "data_end_day_num": 5
    },
    "metrics": {
      "upturn_revenue_yuan": 1280.5,
      "gmv_yuan": 1280.5,
      "live_duration_seconds": 123456,
      "live_duration_display": "1天10小时17分钟",
      "video_count": 14,
      "video_cnt_1d": 3,
      "verify_amount_realtime": 3972.95,
      "verify_amount_realtime_display": "¥3,972.95",
      "verify_cert_cnt_realtime": 56,
      "verify_amount": 69999.0,
      "verify_amount_display": "¥69,999.00",
      "verify_cert_cnt": 120
    },
    "targets": {
      "monthly_video_target": 15,
      "monthly_upturn_target": 2000.0,
      "video_done": 3,
      "video_remaining": 12,
      "video_met": false,
      "upturn_done": 1280.5,
      "upturn_remaining": 719.5,
      "upturn_met": false,
      "video_metric": "video_cnt_1d",
      "upturn_metric": "gmv_yuan"
    },
    "store_contexts": [
      {
        "store": {},
        "period": {},
        "metrics": {},
        "targets": {},
        "source": {}
      }
    ],
    "summary_text": "极修匠手机维修(万象城店): 本月视频 3 条，距离 15 条还差 12 条；上翻 1280.5 元，距离 2000.0 元还差 719.5 元。",
    "source": {
      "source_systems": ["life.douyin.com", "www.life-data.cn"],
      "fetched_at": "2026-05-06T15:10:00+08:00",
      "updated_at": "2026-05-06T11:22:30",
      "data_version": "2026-05:1-5:7481237681621207080",
      "statistics_rule": "video_done 使用侧边栏当前达标口径 video_cnt_1d；upturn_done 使用 gmv_yuan；verify_amount_realtime/verify_cert_cnt_realtime 为当月实时核销数据，verify_amount/verify_cert_cnt 为近30天核销数据。"
    },
    "warnings": []
  }
}
```

说明：

1. `store`、`period`、`metrics`、`targets` 默认取第一个门店，便于单门店群直接使用。
2. 多门店群会在 `stores` 和 `store_contexts` 中返回全部门店。
3. `summary_text` 可直接拼入智能客服的 `faq_context`。

### 未命中响应

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "found": false,
    "reason": "未匹配到门店",
    "warnings": [
      "关键词 `万象城店` 未匹配到门店"
    ],
    "topic": "video_progress"
  }
}
```

调用方要求：

1. `found=false` 时不要编造门店数据。
2. 可以走智能客服原有知识库话术，但不能回答具体视频数、上翻收益、核销等实时事实。

## 四、接口二：只解析群到门店

### 请求

```http
POST /api/v1/ai/resolve-store
Content-Type: application/json
```

```json
{
  "chat_id": "R_xxx",
  "group_name": "B+极修匠-太原(万象城店)",
  "data_month": "2026-05"
}
```

### 响应

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "found": true,
    "store": {
      "chat_id": "R_xxx",
      "group_name": "B+极修匠-太原(万象城店)",
      "store_id": "7481237681621207080",
      "poi_id": "7481237681621207080",
      "store_name": "极修匠手机维修(万象城店)",
      "poi_name": "极修匠手机维修(万象城店)",
      "store_score": 4.8,
      "data_month": "2026-05",
      "data_start_day": 1,
      "data_end_day": 5,
      "match_method": "group_name_exact_mapping",
      "match_confidence": 0.98,
      "match_reason": "群名命中映射表，关键词 `万象城店` 对应 poi_id `7481237681621207080`"
    },
    "stores": [],
    "warnings": []
  }
}
```

用途：

1. 调用方只想先确认群对应哪个门店。
2. 后续再用 `poi_id` 调门店经营数据接口。

## 五、接口三：按门店 ID 查询经营上下文

### 请求

```http
GET /api/v1/ai/store-performance/{poi_id}?data_month=2026-05
```

### 响应

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "found": true,
    "store": {},
    "period": {},
    "metrics": {},
    "targets": {},
    "source": {}
  }
}
```

用途：

1. 调用方已经通过其他方式拿到了 `poi_id`。
2. 不需要再次做群名匹配。

## 六、匹配方法说明

| `match_method` | 含义 | 置信度 |
|---|---|---|
| `group_name_exact_mapping` | 命中 `测试文件/重复门店名_poi_id对照表_补全群名.xlsx` 精确映射 | `0.98` |
| `group_name_keyword` | 从群名括号中提取门店关键词后模糊匹配 | `0.82` |
| `group_name_keyword_rating_normalized` | 去掉 `A/B+/B-` 等评级前缀后匹配 | `0.78` |
| `poi_id_direct` | 调用方直接传入 `poi_id` | `1.0` |

## 七、智能客服推荐接法

智能客服项目建议接入顺序：

1. 在 `external_group_auto_reply.py::_resolve_ai_faq_context` 中调用 `POST /api/v1/ai/store-context`。
2. 如果 `found=true`，把 `summary_text` 和必要 JSON 字段拼进 `faq_context`。
3. 如果用户问“视频够不够”“上翻够不够”“还差多少”，优先使用 `targets` 字段。
4. 如果 `found=false`，不要回答实时经营数据，转为引导商务确认群名或门店。

## 八、上线文件

本次接口涉及代码文件：

1. `backend/app/routers/ai_context.py`
2. `backend/app/services/ai_store_context.py`
3. `backend/app/main.py`
4. `backend/app/config.py`

测试脚本：

1. `测试文件/测试代码/test_ai_context_helpers_20260506.py`
