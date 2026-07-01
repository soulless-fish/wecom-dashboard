# 极修匠企业微信侧边栏门店经营数据中台

这是一个面向连锁门店运营场景的企业微信侧边栏数据中台。员工在企业微信外部客户群中打开侧边栏后，系统会识别当前群聊对应的门店，并展示门店经营指标、门店视频数据、核销数据、抖音订单和凡科订单聚合结果。

## 项目能力

- 企业微信自建应用侧边栏接入：JS-SDK 签名、agentConfig、当前外部群识别和群信息读取。
- 群名到门店匹配：支持评级前缀、括号门店名、多门店群、超级门店后缀和 Excel 精确映射兜底。
- 抖音来客经营数据同步：门店上翻收益、直播时长、视频数量、评分、核销金额和核销券数。
- 抖音开放平台订单同步：订单分页拉取、券码核销状态解析、商品分类和门店维度聚合。
- 凡科商城订单同步：OAuth 授权、订单拉取、商品分类、手机号匹配门店和金额聚合。
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

frontend/
  src/
    api/           API 客户端
    utils/         企业微信 JS-SDK 初始化
    views/         侧边栏页面和首页
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
- MySQL：`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`。

## 安全说明

如果你基于此项目部署真实业务，请把所有密钥放在服务器环境变量或私有配置文件中，不要提交到 Git。Cookie 类配置建议使用短有效期凭据，并为生产服务单独设置最小权限账号。

## GitHub 上传方式

本目录已经整理成可公开上传的项目版本。上传前需要准备：

- GitHub 仓库地址，例如 `https://github.com/<username>/<repo>.git`。
- GitHub Personal Access Token，至少需要目标仓库的 `Contents: Read and write` 权限；如果使用经典 token，需要 `repo` 权限。
- 仓库可见性选择：公开或私有。
- Git 提交作者名称和邮箱。
- 是否需要开源许可证，例如 MIT、Apache-2.0，或不添加许可证。
