# CLAUDE.md

本文件为 Claude Code 在此仓库中工作时的核心指引。

## 项目概览

模枢 ModelHub —— 多模型智能编排网关（复刻 freemodel.dev 自建）。统一 API Key + Base URL，接入火山方舟、DeepSeek、GLM 智谱 3 家供应商的 15+ 模型，兼容 Claude Code、Codex、OpenAI SDK。

- **技术栈**: FastAPI + SQLAlchemy(SQLite) + httpx；37 项 pytest 全通过
- **三协议**: OpenAI `chat/completions`、OpenAI `responses`（Codex）、Anthropic `messages`（Claude Code）

## 常用命令

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000   # 启动（项目根目录；DEBUG=true 时 /docs 有 Swagger）
python scripts/init_db.py                         # 初始化数据库 + 种子模型（幂等；--reset 重建）
pytest tests/ -q                                  # 跑测试
```

- 服务日志在 `/tmp/gateway.log`
- 供应商 API Key 在项目 `.env`（已 gitignore，不入库），改 Key 无需动数据库

## 架构

```
src/
├── main.py             # 应用入口，挂载全部 router
├── config.py           # 环境配置（DEBUG 等）
├── middleware/         # auth（API Key SHA-256 校验）/ billing（Token 级计费、原子扣费）/ rate_limit（令牌桶）
├── providers/          # 供应商适配器：openai_provider（方舟/DeepSeek/GLM 等 OpenAI 兼容通道复用）
│                       #   + anthropic_provider（协议转换）+ gemini/mock
├── routers/            # chat / responses / anthropic / models_list / images / videos / auth / dashboard / web
└── services/           # router.py（智能路由：多通道故障转移 + 熔断器 + 健康检查）
web/                    # Web 控制台（注册登录、Key 管理、余额/日志/模型面板）
scripts/init_db.py      # 建表 + 种子 15 个模型（分组：方舟 Coding Plan / 方舟旗舰 / DeepSeek / GLM）
```

## 关键规则

1. **改模型/供应商后**：跑 `python scripts/init_db.py` 同步种子数据（幂等），再 `pytest tests/ -q` 验证
2. **.env 格式参考 `.env.example`**，真实 Key 绝不提交入库
3. **模型 ID 即路由键**：客户端指定的模型名经 `services/router.py` 的 `resolve_or_default` 解析，未知模型名兜底到默认 Claude 模型
4. **方舟双通道**：Coding Plan 套餐通道 `/api/coding/v1` 支持大部分模型；旗舰（Seed 2.1 Pro/Evolving）只能走 `/api/v3` 按量
5. **供应商适配优先复用 `openai_provider`**，新供应商若协议兼容 OpenAI 无需新增 provider

## 外部依赖（不在仓库内，改坏需知）

- 网关下游：Codex CLI 通过 cc-switch 连本地网关（`~/.codex/config.toml` + `model-catalog.local.json`），详见 Claude Code 记忆 `codex-gateway-setup`
- Docker 部署：`Dockerfile` + `docker-compose.yml` 在根目录；部署细节见 `docs/DEPLOYMENT.md`

## 文档

- `prd/PRD.md` — 产品需求；`docs/DEPLOYMENT.md` — 部署；`docs/TEST_REPORT.md` — 测试报告；`docs/CODE_REVIEW.md` — 评审记录
