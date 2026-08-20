# 模枢 ModelHub

> One API for every model - 多模型智能编排网关

> 🌐 **中文** | [English](README_EN.md)

一个统一的 AI 模型访问层：单一 API Key、单一 Base URL，接入火山方舟、DeepSeek、GLM 智谱、Kimi、MiniMax、百炼等 11 家供应商的 25+ 个模型，完全兼容 **Claude Code**、**Codex**、**OpenAI SDK**。

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🔌 三协议兼容 | OpenAI Chat/Responses、Anthropic Messages 三套协议全支持 |
| 🧭 智能路由 | 多通道故障自动转移 + 熔断器 + 健康检查 |
| 💰 计费系统 | 余额预检、Token 级精确计费、原子扣费、交易流水（统一 USD 计费） |
| 🔑 认证体系 | API Key（SHA-256 哈希）+ JWT + 令牌桶限流 |
| 📊 Web 控制台 | 注册登录、Key 管理、供应商管理、模型/余额/日志面板、测试对话 |
| 🌐 中英双语 | Web 控制台与 README 支持中英文切换（浏览器记忆） |
| 🚀 即插即用 | Claude Code / Codex 改个环境变量即可接入 |

## 🚀 快速开始

```bash
# 方式一：本地运行
pip install -r requirements.txt
cp .env.example .env            # 填入供应商 Key 与凭证加密密钥
python scripts/init_db.py       # 初始化数据库 + 种子
cd frontend && npm i && npm run build && cd ..   # 构建前端（Web 控制台）
uvicorn src.main:app --host 0.0.0.0 --port 8000

# 方式二：Docker
cp .env.example .env
docker compose up -d --build    # http://localhost:8000
```

访问 http://localhost:8000/login 注册账号 -> 创建 API Key -> 开始使用。

## 🔌 客户端接入

模型请求统一用 `厂商/模型` 格式（如 `ark/glm-5.3`）或全局别名。

### Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_API_KEY=<网关 API Key>
export ANTHROPIC_MODEL=ark/doubao-seed-2-1-turbo
```

### Codex CLI

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=<网关 API Key>
export CODEX_MODEL=ark/doubao-seed-2-1-turbo
```

### OpenAI SDK

```python
from openai import OpenAI
client = OpenAI(api_key="<网关 Key>", base_url="http://localhost:8000/v1")
resp = client.chat.completions.create(
    model="ark/glm-5.3",
    messages=[{"role": "user", "content": "hi"}],
)
```

## 🤖 模型目录（25+）

| 供应商 | 模型 |
|--------|------|
| 方舟 Ark · Coding Plan | doubao-seed-2-1-turbo · doubao-seed-2-0-pro · GLM-5.3 · GLM-5.2 · DeepSeek-V4 Pro/Flash · Kimi K2.7 Code · MiniMax M3 · Seedream 5.0 Pro（图）· Seedance 2.5（视频） |
| 百炼 Bailian | DeepSeek-V4 Pro/Flash · GLM-5.2 · Qwen3.6 Flash 等 7 个 |
| DeepSeek 官方 | V4 Pro · V4 Flash |
| GLM 智谱 | GLM-5.3 · GLM-5.2 · GLM-4 Flash（免费） |
| Kimi 月之暗面 | K2.7 Code · K2.7 Code Highspeed · K3 |

> 注册表共 11 家供应商（另含 OpenAI / Anthropic / Grok / Gemini / 混元），添加 Key 后按需开通。

## 📁 项目结构

```
src/
├── main.py            # 应用入口（lifespan 自动结构迁移）
├── config.py          # 环境配置
├── database.py        # 异步 engine + AsyncSessionLocal
├── db/models.py       # ORM（Model / Provider / User 等）
├── middleware/        # auth（API Key / JWT 双认证）· billing · rate_limit
├── providers/         # 适配器（openai / anthropic / gemini / mock）+ 11 家注册表
├── routers/           # chat · responses · anthropic · models · images · videos · auth · providers · dashboard · web
└── services/          # 路由引擎 · 模型同步 · 凭证加密（AES-256-GCM）· 健康检查
frontend/               # 前端工程（Vite + React 18 + Ant Design 5，中英双语）
scripts/               # init_db · migrate_db · migrate_providers · generate_encryption_key
tests/                 # 155 项测试
```

## 🧪 测试

```bash
pytest tests/ -v    # 155 passed
```

## 📄 文档

- [部署与使用文档](docs/DEPLOYMENT.md)
- [测试报告](docs/TEST_REPORT.md)
- [产品需求文档](prd/PRD.md)

## 📄 License

MIT
