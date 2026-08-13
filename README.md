# 模枢 ModelHub

> One API for every model —— 多模型智能编排网关

一个统一的 AI 模型访问层：单一 API Key、单一 Base URL，接入火山方舟、DeepSeek、GLM 智谱等多家供应商的 15+ 个模型，完全兼容 **Claude Code**、**Codex**、**OpenAI SDK**。

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🔌 三协议兼容 | OpenAI Chat/Responses、Anthropic Messages 三套协议全支持 |
| 🧭 智能路由 | 多通道故障自动转移 + 熔断器 + 健康检查 |
| 💰 计费系统 | 余额预检、Token 级精确计费、原子扣费、交易流水 |
| 🔑 认证体系 | API Key（SHA-256 哈希存储）+ JWT + 令牌桶限流 |
| 📊 Web 控制台 | 注册登录、Key 管理、余额/日志/模型面板 |
| 🚀 即插即用 | Claude Code / Codex 改个环境变量即可接入 |

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置（复制模板填入 API Key）
cp .env.example .env

# 3. 初始化数据库 + 种子模型
python scripts/init_db.py

# 4. 启动
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000/login 注册账号 → 创建 API Key → 开始使用。

## 🔌 客户端接入

### Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_API_KEY=<网关 API Key>
export ANTHROPIC_MODEL=ark-doubao-seed-2-1-pro
```

### Codex CLI

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=<网关 API Key>
export CODEX_MODEL=ark-doubao-seed-2-1-pro
```

### OpenAI SDK

```python
from openai import OpenAI
client = OpenAI(api_key="<网关 Key>", base_url="http://localhost:8000/v1")
```

## 🤖 模型目录

| 分组 | 模型 |
|------|------|
| 方舟·Coding Plan | MiniMax M3 · Kimi K2.7 Code · ARK Auto · Seed 2.1 Turbo · 2.0 Lite · GLM-5.2 · DS V4 Pro/Flash |
| 方舟·旗舰 | Seed 2.1 Pro · Seed Evolving |
| DeepSeek 官方 | V4 Flash · V4 Pro |
| GLM 智谱 | 4 Flash（免费）· 5.2 · 5.1 |

## 📁 项目结构

```
src/
├── main.py            # 应用入口
├── middleware/        # 认证 / 计费 / 限流
├── providers/         # 供应商适配器（OpenAI兼容复用）
├── routers/           # chat / images / responses / messages / models / auth
└── services/          # 路由引擎 / 健康检查
scripts/init_db.py     # 数据库初始化 + 种子数据
web/                   # Web 控制台
tests/                 # 37 项测试
```

## 🧪 测试

```bash
pytest tests/ -v    # 37 passed
```

## 📄 文档

- [部署与使用文档](docs/DEPLOYMENT.md)
- [测试报告](docs/TEST_REPORT.md)
- [产品需求文档](prd/PRD.md)

## 📄 License

MIT
