# ModelHub

> One API for every model — Multi-Model Intelligent Orchestration Gateway

> 🌐 [中文](README.md) | **English**

A unified AI model access layer: a single API Key and a single Base URL to reach 25+ models across 11 providers — Volcengine Ark, DeepSeek, Zhipu GLM, Kimi, MiniMax, Bailian, and more. Fully compatible with **Claude Code**, **Codex**, and the **OpenAI SDK**.

## ✨ Core Features

| Feature | Description |
|---------|-------------|
| 🔌 Tri-protocol compatible | Full support for OpenAI Chat/Responses and Anthropic Messages |
| 🧭 Smart routing | Multi-channel failover + circuit breaker + health checks |
| 💰 Billing | Balance pre-check, token-level precise billing, atomic deduction, transaction logs (unified USD) |
| 🔑 Auth | API Key (SHA-256 hashed) + JWT + token-bucket rate limiting |
| 📊 Web console | Register/login, key management, provider management, model/balance/log dashboards, test chat |
| 🌐 Bilingual (zh/en) | Web console and README toggle between Chinese and English (remembered per browser) |
| 🚀 Plug & play | Claude Code / Codex — just set an env var |

## 🚀 Quick Start

```bash
# Option A: local
pip install -r requirements.txt
cp .env.example .env            # fill in provider keys and the credential encryption key
python scripts/init_db.py       # init DB + seed
cd frontend && npm i && npm run build && cd ..   # build the web console
uvicorn src.main:app --host 0.0.0.0 --port 8000

# Option B: Docker
cp .env.example .env
docker compose up -d --build    # http://localhost:8000
```

Open http://localhost:8000/login, register an account → create an API Key → start.

## 🔌 Client Integration

Model requests use the `vendor/model` format (e.g. `ark/glm-5.3`) or a global alias.

### Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_API_KEY=<gateway API key>
export ANTHROPIC_MODEL=ark/doubao-seed-2-1-turbo
```

### Codex CLI

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=<gateway API key>
export CODEX_MODEL=ark/doubao-seed-2-1-turbo
```

### OpenAI SDK

```python
from openai import OpenAI
client = OpenAI(api_key="<gateway key>", base_url="http://localhost:8000/v1")
resp = client.chat.completions.create(
    model="ark/glm-5.3",
    messages=[{"role": "user", "content": "hi"}],
)
```

## 🤖 Model Catalog (25+)

| Provider | Models |
|----------|--------|
| Ark · Coding Plan | doubao-seed-2-1-turbo · doubao-seed-2-0-pro · GLM-5.3 · GLM-5.2 · DeepSeek-V4 Pro/Flash · Kimi K2.7 Code · MiniMax M3 · Seedream 5.0 Pro (image) · Seedance 2.5 (video) |
| Bailian | DeepSeek-V4 Pro/Flash · GLM-5.2 · Qwen3.6 Flash + 7 total |
| DeepSeek official | V4 Pro · V4 Flash |
| Zhipu GLM | GLM-5.3 · GLM-5.2 · GLM-4 Flash (free) |
| Kimi (Moonshot) | K2.7 Code · K2.7 Code Highspeed · K3 |

> The registry lists 11 providers in total (also OpenAI / Anthropic / Grok / Gemini / Hunyuan) — enable on demand by adding a key.

## 📁 Project Structure

```
src/
├── main.py            # app entrypoint (lifespan auto-migrates schema)
├── config.py          # env config
├── database.py        # async engine + AsyncSessionLocal
├── db/models.py       # ORM (Model / Provider / User, …)
├── middleware/        # auth (API Key / JWT dual) · billing · rate_limit
├── providers/         # adapters (openai / anthropic / gemini / mock) + 11-provider registry
├── routers/           # chat · responses · anthropic · models · images · videos · auth · providers · dashboard · web
└── services/          # routing engine · model sync · credential encryption (AES-256-GCM) · health check
frontend/               # frontend (Vite + React 18 + Ant Design, zh/en)
scripts/               # init_db · migrate_db · migrate_providers · generate_encryption_key
tests/                 # 155 tests
```

## 🧪 Tests

```bash
pytest tests/ -v    # 155 passed
```

## 📄 Docs

- [Deployment & usage](docs/DEPLOYMENT.md)
- [Test report](docs/TEST_REPORT.md)
- [Product requirements](prd/PRD.md)

## 📄 License

MIT
