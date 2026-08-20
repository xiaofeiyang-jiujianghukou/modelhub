# 模枢 ModelHub（多模型智能编排网关） - 部署与使用文档

> **版本**: 1.0.0  
> **更新日期**: 2026-08-20  
> **状态**: ✅ 164 项测试全部通过；前端已迁移 frontend/（Vite + React + Ant Design）

---

## 快速开始

### 前置要求

- Python 3.11+
- pip 或 poetry
- Node.js 18+（构建 Web 控制台前端；纯 API 使用或 Docker 部署可跳过）

### 1. 安装依赖

```bash
cd /home/xiaofeiyang/AIWorkSpace/ModelHub
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件，配置上游供应商 API Key
```

**必需配置项：**
- `OPENAI_API_KEY`: OpenAI API Key
- `ANTHROPIC_API_KEY`: Anthropic API Key（可选）
- `GEMINI_API_KEY`: Google Gemini API Key（可选）
- `SECRET_KEY`: JWT 签名密钥（生产环境必须修改）

### 3. 初始化数据库

```bash
python -c "import asyncio; from src.database import init_db; asyncio.run(init_db())"
```

### 4. 构建前端（Web 控制台；Docker 部署无需此步，镜像内自动构建）

```bash
cd frontend
npm install --registry=https://registry.npmmirror.com
npm run build        # -> frontend/dist，由 FastAPI SPA fallback serve
cd ..
```

### 5. 启动服务

```bash
# 开发模式（自动重载）
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# 生产模式
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
```

服务启动后，访问：
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

---

## API 使用指南

### 认证方式

所有 `/v1/*` 接口使用 Bearer Token 认证：

```bash
Authorization: Bearer sk-your-api-key-here
```

### 创建 API Key

（注：MVP 版本可通过数据库直接插入，后续版本将提供管理界面）

```sql
INSERT INTO api_keys (id, user_id, name, key_prefix, key_hash, is_active)
VALUES (
    'key-id',
    'user-id',
    'My API Key',
    'sk-prefi',
    'sha256-hash-of-full-key',
    true
);
```

### 文本对话

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 图像生成

```bash
curl http://localhost:8000/v1/images/generations \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "dall-e-3",
    "prompt": "A futuristic cityscape at sunset",
    "n": 1,
    "size": "1024x1024"
  }'
```

### 查询模型列表

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-your-api-key"
```

### 查询余额

```bash
curl http://localhost:8000/v1/dashboard/balance \
  -H "Authorization: Bearer sk-your-api-key"
```

---

## Claude Code 接入（Anthropic 协议）

网关实现了 **Anthropic Messages API 兼容层**（`POST /v1/messages`，支持流式 SSE）。

```bash
# 环境变量配置
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_API_KEY=<网关 API Key>       # 网关创建的 sk- 开头 Key
export ANTHROPIC_MODEL=doubao-seed-2-0-pro     # 指定网关模型
export ANTHROPIC_SMALL_FAST_MODEL=doubao-1-5-pro
```

说明：
- `claude-*` 开头的模型名会自动映射到网关默认模型（`doubao-seed-2-0-pro`，可在 `.env` 的 `DEFAULT_CLAUDE_MODEL` 修改）
- 认证支持 `x-api-key` 或 `Authorization: Bearer`
- 计费/日志/限流与 OpenAI 接口一致

## Codex CLI 接入（OpenAI Responses 协议）

网关实现了 **OpenAI Responses API 兼容层**（`POST /v1/responses`，支持流式 SSE），同时 `POST /v1/chat/completions` 完全兼容。

```bash
# Codex CLI 环境变量
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=<网关 API Key>
export CODEX_MODEL=doubao-seed-2-0-pro
```

## OpenAI SDK 兼容

本网关 100% 兼容 OpenAI SDK，仅需修改 `base_url`：

### Python

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-your-api-key",
    base_url="http://localhost:8000/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
```

### JavaScript

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  apiKey: 'sk-your-api-key',
  baseURL: 'http://localhost:8000/v1',
});

const response = await client.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Hello!' }],
});
console.log(response.choices[0].message.content);
```

---

## 生产部署建议

### 使用 PostgreSQL

1. 安装 PostgreSQL 驱动：
```bash
pip install asyncpg
```

2. 修改 `.env`：
```
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname
```

### 使用 Redis 缓存

1. 安装 Redis 客户端：
```bash
pip install redis
```

2. 修改 `.env`：
```
REDIS_URL=redis://localhost:6379/0
```

### 使用 systemd

创建 `/etc/systemd/system/modelhub.service`：

```ini
[Unit]
Description=ModelHub 多模型智能编排网关
After=network.target

[Service]
Type=exec
User=www-data
WorkingDirectory=/opt/modelhub
Environment="PATH=/opt/modelhub/venv/bin"
ExecStart=/opt/modelhub/venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always

[Install]
WantedBy=multi-user.target
```

启用服务：
```bash
sudo systemctl enable modelhub
sudo systemctl start modelhub
```

### 使用 Docker

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Web 控制台

浏览器访问：
- **控制台**: http://localhost:8000/dashboard （登录后管理 Key、查看余额/模型/日志、测试对话）
- **登录/注册**: http://localhost:8000/login

### Docker Compose 部署

```bash
# 基础部署（SQLite 数据卷）
docker compose up -d --build

# 完整部署（+ PostgreSQL + Redis）
docker compose --profile full up -d --build
```

---

## 故障排查

### 服务无法启动

1. 检查端口占用：
```bash
lsof -i :8000
```

2. 查看日志：
```bash
# 开发模式下日志会直接输出到终端
# 生产模式检查 systemd 日志
sudo journalctl -u modelhub -f
```

### 数据库连接失败

- SQLite：检查文件权限
- PostgreSQL：验证连接字符串和网络可达性

### 上游请求超时

- 检查网络连接
- 增加 `UPSTREAM_TIMEOUT_SECONDS` 配置值

---

## 目录结构

```
AgentTeam/
├── src/
│   ├── main.py              # 应用入口
│   ├── config.py            # 配置管理
│   ├── database.py          # 数据库连接
│   ├── models.py            # ORM 数据模型
│   ├── middleware/
│   │   ├── auth.py          # 认证中间件
│   │   ├── billing.py       # 计费中间件
│   │   └── rate_limit.py    # 限流中间件
│   ├── providers/
│   │   ├── base.py          # 供应商基类
│   │   ├── openai_provider.py
│   │   ├── anthropic_provider.py
│   │   └── gemini_provider.py
│   ├── db/
│   │   └── models.py      # SQLAlchemy ORM
│   ├── routers/
│   │   ├── chat.py          # /v1/chat/completions
│   │   ├── images.py        # /v1/images/generations
│   │   ├── models.py       # /v1/models
│   │   └── dashboard.py     # /v1/dashboard/balance
│   └── services/
│       ├── router.py        # 路由服务
│       └── health.py        # 健康检查
├── tests/                   # 测试用例
├── docs/                    # 文档
├── prd/                     # 产品需求文档
├── requirements.txt         # 依赖清单
├── .env.example             # 环境变量模板
└── gateway.db               # SQLite 数据库（开发环境）
```

---

## 技术支持

- GitHub Issues: [项目地址]
- 文档: [在线文档]
