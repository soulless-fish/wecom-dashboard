# 极修匠企业微信侧边栏门店经营数据中台

这是一个面向连锁门店运营场景的企业微信侧边栏数据中台。员工在企业微信外部客户群中打开侧边栏后，系统会识别当前群聊对应的门店，并展示门店经营指标、营业状态、团购商品、抖音账号、订单核销、凡科采购和巨量本地推数据。

## 项目能力

- 企业微信自建应用侧边栏接入：JS-SDK 签名、agentConfig、当前外部群识别和群信息读取。
- 群名到门店匹配：支持评级前缀、括号门店名、多门店群、超级门店后缀和 Excel 精确映射兜底。
- 抖音来客经营数据同步：门店上翻收益、直播时长、视频数量、评分、核销金额和核销券数。
- 抖音开放平台订单同步：订单分页拉取、券码核销状态解析、商品分类和门店维度聚合。
- 凡科商城订单同步：OAuth 授权、订单拉取、商品分类、手机号匹配门店和金额聚合。
- 团购链接和营业状态：按商品 ID 同步门店团购商品，并展示正常营业、暂停营业或即将开业状态。
- 抖音账号管理：展示子机构经营号、商家职人号和个人职人号。
- 巨量引擎本地推：同步消耗、转化、转化成本和余额，并支持企业微信收集表与安全网页双通道填报。
- 全国平均核销：按当月和近 30 天口径计算全门店核销基准。
- AI 上下文接口：把门店身份、经营指标和同步状态整理成可供智能客服调用的数据接口。

## 技术栈

- 后端：FastAPI、SQLAlchemy、MySQL、APScheduler、httpx、openpyxl。
- 前端：Vue 3、Vite、Axios、企业微信 JS-SDK。
- 部署：Nginx、Uvicorn、systemd、MySQL。

## 目录结构

```text
backend/
  app/
    core/          企业微信回调加解密等基础能力
    database/      SQLAlchemy 数据模型
    models/        请求和响应数据结构
    routers/       FastAPI 路由
    services/      企业微信、抖音、凡科、门店匹配和同步服务
  requirements.txt
  .env.example

JLYQ/
  巨量引擎 OAuth、本地推同步、企业微信收集表和安全填写页服务

frontend/
  src/
    api/           API 客户端
    utils/         企业微信 JS-SDK 初始化
    views/         侧边栏、团购链接、抖音号和巨量引擎页面
  package.json

docs/
  项目成果报告.md
  AI门店实时数据接口文档.md
```

## 本仓库不包含的内容

公开版本已经移除或不纳入以下内容：

- 生产 `.env`、Cookie、企业微信 Secret、抖音开放平台 Secret、凡科 Secret。
- 企业微信 CorpID、AgentId 等真实租户配置。
- 服务器 SSH 私钥、部署备份、运行日志。
- 操作日志、测试文件、浏览器调试缓存、本地虚拟环境、node_modules。
- 业务 Excel 数据表和真实订单明细数据。
- 招商培训 H5 页面、报名接口和报名数据。

## 本地启动

后端：

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## 配置说明

复制 `backend/.env.example` 为 `backend/.env` 后再填写真实配置。公开仓库中的所有敏感字段都保持为空，不能直接用于生产。

核心配置项包括：

- 企业微信：`WECOM_CORP_ID`、`WECOM_AGENT_ID`、`WECOM_SECRET`、`WECOM_EXTERNAL_CONTACT_SECRET`。
- 抖音开放平台：`DOUYIN_CLIENT_KEY`、`DOUYIN_CLIENT_SECRET`、`DOUYIN_ACCOUNT_ID`。
- 抖音来客 / life-data：`LIFE_DATA_COOKIE`、`LIFE_DATA_ACCOUNT_ID`、`LIFE_DATA_CSRF_TOKEN`。
- 凡科商城：`FANKE_CLIENT_ID`、`FANKE_CLIENT_SECRET`、`FANKE_RETURN_URL`。
- 巨量引擎：`JLYQ_APP_ID`、`JLYQ_APP_SECRET`、`JLYQ_CALLBACK_URL`、`JLYQ_LIFE_ACCOUNT_IDS`。
- MySQL：`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`。

巨量引擎门店权限表和商家填写模板属于业务数据，不在公开仓库中。启用对应功能时，需要按 `JLYQ/local_promotion_mapping.py` 和 `JLYQ/wecom_collection_service.py` 中的字段约定自行准备 Excel 文件。

## 安全说明

如果你基于此项目部署真实业务，请把所有密钥放在服务器环境变量或私有配置文件中，不要提交到 Git。Cookie 类配置建议使用短有效期凭据，并为生产服务单独设置最小权限账号。

## GitHub 上传方式

本目录已经整理成可公开上传的项目版本。上传前需要准备：

- GitHub 仓库地址，例如 `https://github.com/<username>/<repo>.git`。
- GitHub Personal Access Token，至少需要目标仓库的 `Contents: Read and write` 权限；如果使用经典 token，需要 `repo` 权限。
- 仓库可见性选择：公开或私有。
- Git 提交作者名称和邮箱。
- 是否需要开源许可证，例如 MIT、Apache-2.0，或不添加许可证。
