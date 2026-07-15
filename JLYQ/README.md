# 巨量引擎本地推接入说明

## 回调地址

建议在巨量营销应用后台填写：

```text
https://your-domain.example/api/v1/jlyq/oauth/callback
```

判断结果：

```text
1. 不需要另建子域名，可以使用主项目域名承载。
2. 不复用企业微信回调地址，企业微信已有 /callback/command 和 /callback/data。
3. 不复用凡科回调地址，凡科已有 /api/v1/fanke/oauth/callback。
4. 巨量引擎使用独立路径 /api/v1/jlyq/oauth/callback，便于后续 token、账户、报表数据独立维护。
```

## 当前代码

```text
oauth_router.py：FastAPI 路由，提供 OAuth 回调、HEAD 探测、授权状态查询。
oauth_client.py：巨量引擎 OAuth 客户端，支持 auth_code 换 token、refresh_token 刷新、查询授权账户。
token_storage.py：巨量引擎授权结果存储，文件保存在 JLYQ 目录内。
local_promotion_mapping.py：读取业务侧门店和本地推账户绑定表。
local_promotion_service.py：同步消耗、转化、转化成本和账户余额。
local_promotion_router.py：提供同步、状态、侧边栏数据和商家填写接口。
wecom_collection_service.py：创建企业微信收集表，失败时回退安全网页，并计算月累计。
```

## 环境变量

真实授权前需要在服务器配置：

```text
JLYQ_APP_ID=巨量营销应用ID
JLYQ_APP_SECRET=
JLYQ_CALLBACK_URL=https://your-domain.example/api/v1/jlyq/oauth/callback
```

如果暂未配置 `JLYQ_APP_ID` 和 `JLYQ_APP_SECRET`，回调地址仍可返回 200，但收到 `auth_code` 后不会换取 token。

门店权限表和商家填写模板属于业务数据，公开仓库不会提供。部署时需要按代码中的列名约定自行创建，并通过环境变量配置真实回调地址。
