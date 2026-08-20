# CLAUDE.md

本文件为 Claude Code 在此仓库中工作时的核心指引。

## 项目概览

模枢 ModelHub —— 多模型智能编排网关（复刻 freemodel.dev 自建）。统一 API Key + Base URL，接入火山方舟、DeepSeek、GLM 智谱 3 家供应商的 15+ 模型，兼容 Claude Code、Codex、OpenAI SDK。

- **技术栈**: FastAPI + SQLAlchemy(SQLite) + httpx；前端 Vite + React 18 + TS + Ant Design 5（中英双语）；164 项 pytest 全通过
- **三协议**: OpenAI `chat/completions`、OpenAI `responses`（Codex）、Anthropic `messages`（Claude Code）

## 常用命令

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000   # 启动（项目根目录；DEBUG=true 时 /docs 有 Swagger）
python scripts/init_db.py                         # 初始化表结构 + 缺省种子（ensure-exists，不覆盖界面配置；--reset 重建）
python scripts/migrate_providers.py               # .env 供应商 Key → DB（--overwrite 强制；--reencrypt 重加密）
python scripts/generate_encryption_key.py         # 生成凭证加密密钥（占位值自愈）
python scripts/migrate_db.py                      # 结构迁移（guarded ALTER，幂等；服务启动自动执行）
pytest tests/ -q                                  # 跑测试
cd frontend && npm run dev                        # 前端开发模式（5173，/v1 代理到 8000）
cd frontend && npm run build                      # 构建前端 -> frontend/dist（本地跑 Web 控制台需先 build）
```

- 服务日志在 `/tmp/gateway.log`
- 供应商 API Key 存数据库 `providers.credentials_enc`（AES-256-GCM 加密，`gcm:v1:` 前缀），**界面统一管理**；.env 只保留非敏感配置
- 凭证加密密钥 `CREDENTIALS_ENCRYPTION_KEY` 在 `.env`（占位值会导致加密写库失败 fail-fast）

## 架构

```
src/
├── main.py             # 应用入口，挂载全部 router（lifespan 自动跑结构迁移）+ SPA fallback serve frontend/dist
├── config.py           # 环境配置（DEBUG、凭证加密密钥等）
├── database.py         # 异步 engine + AsyncSessionLocal（测试通过 monkeypatch 此模块隔离 DB）
├── db/
│   └── models.py       # SQLAlchemy ORM（Model/Provider/User 等全部表）
├── middleware/         # auth（API Key SHA-256 / JWT 双认证，首用户自动 admin）/ billing / rate_limit
├── providers/          # provider_registry.py（11 家供应商注册表，单一数据源）+ 适配器（openai/anthropic/gemini/mock）
├── routers/            # chat / responses / anthropic / models / images / videos / auth / dashboard / web
│                       #   + providers.py（供应商管理：CRUD/同步/级联删除，admin 权限）
└── services/           # router.py（智能路由）/ model_sync.py（拉取模型 4 种解析器 + upsert）/ crypto.py（AES-256-GCM 凭证）
frontend/               # 前端工程（Vite + React 18 + TS + AntD 5 + react-i18next 中英双语；面板：概览/API Keys/模型/日志/测试对话/接入文档/供应商 admin）
scripts/                # init_db.py（种子）/ migrate_db.py（加列）/ migrate_providers.py（.env Key 迁移）/ generate_encryption_key.py
```

## 供应商管理（界面）

- 11 家注册表：DeepSeek/方舟 Coding Plan/混元/百炼/Kimi/智谱/MiniMax/OpenAI/Claude/Grok/Gemini（`src/providers/provider_registry.py`）
- `model_source='api'` 的添加 Key 后自动 GET /models 拉取（DeepSeek/Kimi/MiniMax/OpenAI/Claude/Grok/Gemini）；`'static'` 用内置清单（方舟/混元/百炼/智谱）
- 删除供应商级联清理其独占模型与通道；共有模型保留
- 价格来源标注：`price_source` = official（官方定价/官方文档价）| default（网关默认 2/8）| manual

## 关键规则

1. **改模型/供应商后**：跑 `python scripts/init_db.py` 同步种子数据（幂等），再 `pytest tests/ -q` 验证
2. **.env 格式参考 `.env.example`**，真实 Key 绝不提交入库
3. **模型键 = 厂商/模型（唯一路由键）**：`models` 表复合主键 `(model, vendor)`，同一模型多厂商是不同记录；客户端请求必须用 `厂商/模型`（如 `deepseek/deepseek-v4-pro`）或全局唯一别名（`models.alias`），不再支持裸模型名兜底
   - 查询方法：`Model.get_by_model_and_vendor(db, model, vendor)` / `Model.get_by_alias(db, alias)`，别名全局唯一、无需 vendor
   - 路由层 `router.resolve_model_id` 只做键解析；`Model.resolve_or_default` 负责官方模型名兜底到 `default_claude_model`（默认 `glm/glm-4-flash`）
   - `models.alias` 在 `GET /v1/models` 中作为独立条目输出，`alias_for` 指向真实 `厂商/模型`
4. **方舟双通道**：Coding Plan 套餐通道 `/api/coding/v1` 支持大部分模型；旗舰（Seed 2.1 Pro/Evolving）只能走 `/api/v3` 按量
5. **供应商适配优先复用 `openai_provider`**，新供应商若协议兼容 OpenAI 无需新增 provider
6. **计费一律 USD**：价格原始币种存 `models.price_currency`（CNY/USD），`middleware/billing.py` 的 `_to_usd()` 负责换算后扣费，严禁直接用 CNY 金额计费
7. **重启网关前先确认 8000 端口无残留旧 uvicorn**：旧进程跑旧代码但数据库已被新迁移改列名（`models.id -> models.model`）时会报 `no such column: models.model`；`ss -ltnp | grep :8000` 查到后先 kill 再启动
8. **改前端（frontend/）后**：本地跑需 `cd frontend && npm run build`（serve 的是 dist 产物）；Docker 部署直接 `docker compose up -d --build`（镜像内自动构建前端）；前端开发迭代用 `npm run dev`（5173 端口热更）

## 模型表结构（2026-08-15 重构后）

- `ModelCatalog` 已改名 `Model`，主键列由 `id` 改名为 `model`，与 `vendor` 组成复合主键
- `ModelAlias` 表已删除，别名收敛为 `models.alias` 单列（全局唯一）
- ORM 文件：`src/db/models.py`；模型列表 API：`src/routers/models.py`
- 迁移脚本 `scripts/migrate_db.py` 已支持 `models.id -> models.model` 改名、`models.alias` 加列、`model_aliases` 表删除；服务 lifespan 启动时自动执行

## 外部依赖（不在仓库内，改坏需知）

- 网关下游：Codex CLI 通过 cc-switch 连本地网关（`~/.codex/config.toml` + `model-catalog.local.json`），详见 Claude Code 记忆 `codex-gateway-setup`
- Docker 部署：`Dockerfile`（多阶段：node 前端 build → python 运行时）+ `docker-compose.yml` 在根目录；部署细节见 `docs/DEPLOYMENT.md`

## 文档

- `prd/PRD.md` — 产品需求；`docs/DEPLOYMENT.md` — 部署；`docs/TEST_REPORT.md` — 测试报告；`docs/CODE_REVIEW.md` — 评审记录
