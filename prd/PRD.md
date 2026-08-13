# 模枢 ModelHub（多模型智能编排网关） PRD

> **文档版本**: v1.0  
> **创建日期**: 2026-08-13  
> **状态**: 草稿

---

## 目录

1. [项目概述](#1-项目概述)
2. [功能模块清单](#2-功能模块清单)
3. [API 接口规范](#3-api-接口规范)
4. [路由策略设计](#4-路由策略设计)
5. [供应商管理](#5-供应商管理)
6. [认证与安全](#6-认证与安全)
7. [计费系统设计](#7-计费系统设计)
8. [数据模型](#8-数据模型)
9. [技术栈选型](#9-技术栈选型)
10. [非功能性需求](#10-非功能性需求)
11. [交付范围](#11-交付范围)

---

## 1. 项目概述

### 1.1 产品定位

**模枢 ModelHub（多模型智能编排网关）（Multi-Model Intelligent Orchestration Gateway）** 是一个统一的 AI 模型访问层，通过单一 OpenAI 兼容接口聚合来自多家供应商的文本、图像、视频生成模型。用户只需一个 API Key 和一个 Base URL，即可无缝调用市场上主流的所有 AI 模型，无需为每家供应商单独维护账号、密钥和集成代码。

### 1.2 核心价值

| 价值点 | 描述 |
|--------|------|
| 统一接口 | 100% OpenAI API 兼容，零改造成本接入现有项目 |
| 模型丰富 | 覆盖 GPT、Claude、Gemini、Grok 等主流模型及图像/视频生成 |
| 智能路由 | 自动选择最优上游通道，支持故障转移与负载均衡 |
| 简单计费 | 预付费余额制，按实际消耗扣费，充值即用 |
| 低维护成本 | 无需管理多个 API Key，统一日志与用量追踪 |

### 1.3 目标用户

- **独立开发者**：快速原型验证，不想为每家模型供应商单独申请账号
- **中小型 SaaS 团队**：需要多模型能力但希望统一管理成本与配额
- **AI 应用集成商**：构建面向终端用户的 AI 功能，需要稳定可靠的模型调用层
- **研究人员与学生**：低成本、灵活访问多种模型进行对比实验

---

## 2. 功能模块清单

### 2.1 用户注册与账户管理

| ID | 用户故事 | 优先级 |
|----|----------|--------|
| F-001 | 作为新用户，我可以通过邮箱注册账号，以便开始使用服务 | P0 |
| F-002 | 作为用户，我可以通过邮箱+密码登录控制台 | P0 |
| F-003 | 作为用户，我可以重置密码 | P1 |
| F-004 | 作为用户，我可以查看和修改账户信息（邮箱、显示名） | P1 |

### 2.2 API Key 管理

| ID | 用户故事 | 优先级 |
|----|----------|--------|
| F-010 | 作为用户，我可以创建一个或多个 API Key，以便在不同项目中使用 | P0 |
| F-011 | 作为用户，我可以为每个 API Key 设置自定义名称，便于识别用途 | P0 |
| F-012 | 作为用户，我可以查看所有 API Key 的列表（Key 值仅在创建时完整显示，之后脱敏） | P0 |
| F-013 | 作为用户，我可以随时撤销（删除）任意 API Key，立即生效 | P0 |
| F-014 | 作为用户，我可以为 API Key 设置使用限额（月度 token 上限或消费金额上限） | P1 |

### 2.3 模型目录

| ID | 用户故事 | 优先级 |
|----|----------|--------|
| F-020 | 作为用户，我可以通过 `GET /v1/models` 获取当前所有可用模型列表 | P0 |
| F-021 | 作为用户，我可以在控制台查看模型目录，包括模型名、类型、单价 | P0 |
| F-022 | 作为管理员，我可以上下架模型，更新模型的路由配置和定价 | P0 |

### 2.4 文本对话（LLM）

| ID | 用户故事 | 优先级 |
|----|----------|--------|
| F-030 | 作为用户，我可以调用 `POST /v1/chat/completions`，传入模型名和消息，获取文本回复 | P0 |
| F-031 | 作为用户，我可以使用流式输出（stream: true），获得逐 token 的实时响应 | P0 |
| F-032 | 作为用户，我可以传入 system prompt、多轮对话历史，获得上下文感知的回复 | P0 |
| F-033 | 作为用户，我可以指定 temperature、max_tokens 等生成参数 | P1 |

### 2.5 图像生成

| ID | 用户故事 | 优先级 |
|----|----------|--------|
| F-040 | 作为用户，我可以调用 `POST /v1/images/generations`，传入提示词，生成图像 | P0 |
| F-041 | 作为用户，我可以指定图像尺寸（如 1024x1024）、数量、质量等参数 | P1 |
| F-042 | 作为用户，我可以获得图像的 URL 或 base64 编码结果 | P0 |

### 2.6 视频生成

| ID | 用户故事 | 优先级 |
|----|----------|--------|
| F-050 | 作为用户，我可以发起视频生成请求，获取任务 ID | P1 |
| F-051 | 作为用户，我可以轮询任务状态，在任务完成后获取视频 URL | P1 |

### 2.7 余额与计费

| ID | 用户故事 | 优先级 |
|----|----------|--------|
| F-060 | 作为用户，我可以查看账户当前余额 | P0 |
| F-061 | 作为用户，我可以通过支付（$5/$20/$50/$100 套餐）充值余额 | P0 |
| F-062 | 作为用户，我可以查看充值记录和消费明细 | P0 |
| F-063 | 当余额不足时，请求应被拒绝并返回清晰的错误信息 | P0 |
| F-064 | 作为用户，我可以查看每次请求的 token 用量和扣费金额 | P1 |

### 2.8 请求日志

| ID | 用户故事 | 优先级 |
|----|----------|--------|
| F-070 | 作为用户，我可以在控制台查看历史请求记录（时间、模型、token、费用、状态） | P0 |
| F-071 | 作为用户，我可以按时间范围筛选日志 | P1 |
| F-072 | 作为用户，我可以查看单条请求的详情（请求体、响应摘要、延迟） | P1 |

### 2.9 管理后台（Admin）

| ID | 用户故事 | 优先级 |
|----|----------|--------|
| F-080 | 作为管理员，我可以管理上游供应商配置（密钥、权重、启用/禁用） | P0 |
| F-081 | 作为管理员，我可以查看全局请求统计和收入数据 | P1 |
| F-082 | 作为管理员，我可以手动调整用户余额 | P1 |

---

## 3. API 接口规范

### 3.1 基础约定

- **Base URL**: `https://<your-domain>/v1`
- **认证**: 所有接口需在 Header 中携带 `Authorization: Bearer <api_key>`
- **Content-Type**: `application/json`
- **字符编码**: UTF-8
- **时间格式**: Unix timestamp（秒）

### 3.2 通用错误响应格式

所有错误均返回如下结构，与 OpenAI 规范完全一致：

```json
{
  "error": {
    "message": "错误描述",
    "type": "error_type",
    "code": "error_code",
    "param": null
  }
}
```

**错误码对照表：**

| HTTP Status | type | code | 场景 |
|-------------|------|------|------|
| 400 | invalid_request_error | invalid_model | 模型不存在或不可用 |
| 400 | invalid_request_error | invalid_request | 请求体格式错误 |
| 401 | authentication_error | invalid_api_key | API Key 无效或已撤销 |
| 402 | billing_error | insufficient_balance | 余额不足 |
| 429 | rate_limit_error | rate_limit_exceeded | 触发限流 |
| 500 | api_error | upstream_error | 上游供应商返回错误 |
| 503 | api_error | no_available_provider | 所有上游通道均不可用 |

### 3.3 GET /v1/models

**描述**: 获取当前所有可用模型列表。

**请求示例**:
```bash
GET /v1/models
Authorization: Bearer sk-xxx
```

**响应示例（200）**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-4o",
      "object": "model",
      "created": 1715367049,
      "owned_by": "openai",
      "meta": {
        "type": "llm",
        "input_price_per_1m_tokens": 5.00,
        "output_price_per_1m_tokens": 15.00,
        "context_window": 128000,
        "supports_streaming": true
      }
    },
    {
      "id": "claude-opus-5",
      "object": "model",
      "created": 1715367049,
      "owned_by": "anthropic",
      "meta": {
        "type": "llm",
        "input_price_per_1m_tokens": 15.00,
        "output_price_per_1m_tokens": 75.00,
        "context_window": 1000000,
        "supports_streaming": true
      }
    },
    {
      "id": "dall-e-3",
      "object": "model",
      "created": 1715367049,
      "owned_by": "openai",
      "meta": {
        "type": "image",
        "price_per_image": 0.04
      }
    }
  ]
}
```

### 3.4 POST /v1/chat/completions

**描述**: 文本对话，兼容 OpenAI Chat Completions API。

**请求体**:
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "temperature": 0.7,
  "max_tokens": 1024,
  "stream": false
}
```

**必填字段**:
- `model` (string): 模型 ID，需在 `/v1/models` 列表中存在
- `messages` (array): 对话历史，至少包含一条 user 消息

**可选字段**:
- `temperature` (float, 0-2, 默认 1.0)
- `max_tokens` (int, 默认由模型决定)
- `stream` (bool, 默认 false)
- `top_p` (float, 0-1, 默认 1.0)
- `stop` (string|array, 停止词)
- `presence_penalty` (float, -2.0 to 2.0)
- `frequency_penalty` (float, -2.0 to 2.0)

**响应示例（非流式，200）**:
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1720000000,
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 10,
    "total_tokens": 30
  }
}
```

**流式响应示例（stream: true）**:
```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"!"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### 3.5 POST /v1/images/generations

**描述**: 图像生成，兼容 OpenAI Images API。

**请求体**:
```json
{
  "model": "dall-e-3",
  "prompt": "A futuristic cityscape at sunset, digital art",
  "n": 1,
  "size": "1024x1024",
  "quality": "standard",
  "response_format": "url"
}
```

**必填字段**:
- `model` (string): 图像模型 ID
- `prompt` (string): 图像描述提示词，最大 4000 字符

**可选字段**:
- `n` (int, 1-4, 默认 1): 生成数量
- `size` (string, 默认 "1024x1024"): 支持 "256x256"、"512x512"、"1024x1024"、"1792x1024"、"1024x1792"
- `quality` (string, "standard"|"hd", 默认 "standard")
- `response_format` (string, "url"|"b64_json", 默认 "url")

**响应示例（200）**:
```json
{
  "created": 1720000000,
  "data": [
    {
      "url": "https://cdn.example.com/images/generated/abc123.png",
      "revised_prompt": "A futuristic cityscape at sunset with flying vehicles and neon lights, digital art style"
    }
  ]
}
```

### 3.6 视频生成接口（异步任务）

由于视频生成耗时较长（通常 30 秒至数分钟），采用异步轮询模式。

**发起生成 — POST /v1/videos/generations**:
```json
{
  "model": "veo-2",
  "prompt": "A cat playing piano in a jazz club",
  "duration": 5,
  "resolution": "720p"
}
```

**响应（202 Accepted）**:
```json
{
  "task_id": "vtask-xyz789",
  "status": "pending",
  "created": 1720000000,
  "estimated_seconds": 120
}
```

**查询状态 — GET /v1/videos/tasks/{task_id}**:
```json
{
  "task_id": "vtask-xyz789",
  "status": "succeeded",
  "created": 1720000000,
  "completed": 1720000125,
  "result": {
    "url": "https://cdn.example.com/videos/vtask-xyz789.mp4",
    "duration": 5,
    "resolution": "720p"
  },
  "usage": {
    "billed_seconds": 5
  }
}
```

`status` 枚举值：`pending` | `processing` | `succeeded` | `failed`

### 3.7 账户余额接口

**GET /v1/dashboard/balance**:
```json
{
  "balance_usd": 18.50,
  "currency": "USD",
  "updated_at": 1720000000
}
```

---

## 4. 路由策略设计

### 4.1 路由模型

每个对外暴露的"模型名"（如 `gpt-4o`）背后维护一组"路由通道（Route Channel）"，每个通道对应一个具体的上游供应商配置。

```
模型名（对外）→ 路由组（RouteGroup）→ 多个通道（Channel）
                                       ├── 通道A: OpenAI 官方 / 权重60
                                       ├── 通道B: Azure OpenAI / 权重30
                                       └── 通道C: 备用供应商 / 权重10
```

### 4.2 路由选择策略

系统支持以下三种路由策略，可按模型配置：

| 策略 | 说明 | 适用场景 |
|------|------|----------|
| `weighted_random` | 按权重随机选择通道（默认） | 流量分发、成本优化 |
| `priority` | 按优先级顺序，高优先级不可用时降级 | 主备容灾 |
| `lowest_latency` | 选择近期 P95 延迟最低的通道 | 延迟敏感场景 |

### 4.3 故障转移机制

1. **健康状态维护**: 后台每 30 秒对每个通道发送轻量 ping 请求，更新其健康状态
2. **实时降级**: 请求发出后，若上游在 30 秒内无响应或返回 5xx，标记该通道为"临时不可用"，冷却期 60 秒
3. **自动重试**: 单次请求失败后，自动选择同一路由组内的下一个可用通道重试，最多重试 2 次
4. **全通道不可用**: 若路由组内所有通道均不可用，返回 503 错误

### 4.4 路由元数据

系统在响应 Header 中附加路由调试信息（生产环境可关闭）：

```
X-Gateway-Model: gpt-4o
X-Gateway-Provider: openai-official
X-Gateway-Latency: 843ms
X-Gateway-Request-ID: req_abc123xyz
```

### 4.5 模型别名机制

支持为模型配置别名，例如将 `gpt-4-turbo` 路由至 `gpt-4o` 的通道组，确保旧版模型名向后兼容。

---

## 5. 供应商管理

### 5.1 已支持供应商列表

| 供应商 | 支持模型类型 | 认证方式 |
|--------|------------|----------|
| OpenAI | LLM、图像 | API Key |
| Anthropic | LLM | API Key |
| Google AI | LLM、视频 | API Key / OAuth |
| xAI | LLM、图像 | API Key |
| ByteDance | 视频 | AK/SK |
| Azure OpenAI | LLM | API Key + Endpoint |

### 5.2 供应商接入规范

每个供应商在数据库中维护一条 `Provider` 记录，包含：

- `name`: 供应商唯一标识（如 `openai-official`）
- `base_url`: 上游 API Base URL
- `auth_type`: `bearer` | `ak_sk`
- `credentials`: 加密存储的认证凭证（JSON，含 api_key 等字段）
- `timeout_ms`: 请求超时时间（默认 30000）
- `is_active`: 是否启用

新增供应商仅需在管理后台填写上述字段，系统自动接入路由体系，无需改代码。

### 5.3 健康检查

- **主动检查**: 每 30 秒向供应商发送模型列表或轻量推理请求，记录响应时间与成功率
- **被动检查**: 线上请求失败时实时更新通道错误率
- **熔断规则**:
  - 60 秒滑动窗口内错误率 > 50% → 触发熔断，停止路由至该通道，进入 Open 状态
  - Open 状态持续 60 秒后自动进入 Half-Open 状态（允许少量探测请求）
  - 探测请求成功率 > 80% → 恢复为 Closed 状态（正常路由）
  - 探测请求失败 → 重新进入 Open 状态，等待时间翻倍（指数退避，最大 10 分钟）

### 5.4 供应商凭证安全

- 凭证存储时使用 AES-256-GCM 加密，密钥通过环境变量注入，不入库
- 日志中所有凭证字段自动脱敏
- 支持凭证轮换：新凭证配置生效后旧凭证立即失效

---

## 6. 认证与安全

### 6.1 API Key 规范

- 格式：`sk-` 前缀 + 48 位 Base58 随机字符，例如 `sk-AbCdEfGh1234...`
- 存储：数据库仅存储 SHA-256 哈希值，原始 Key 仅在创建时返回一次
- 索引：通过 Key 的前 8 位（`key_prefix`）加速查询，避免全表扫描哈希

### 6.2 认证流程

```
请求 → 提取 Bearer Token → 查 Redis 缓存（TTL 60s）
      ↓ 缓存未命中
      查 PostgreSQL（key_prefix + SHA256 比对）
      ↓ 命中
      加载用户余额与限流配置 → 写入 Redis 缓存
      ↓ 未命中
      返回 401
```

### 6.3 Rate Limiting

采用令牌桶算法，配置层级如下：

| 层级 | 维度 | 默认限制 | 可配置 |
|------|------|----------|--------|
| 全局 | 全服务 | 10,000 RPM | 是 |
| 用户 | per user | 300 RPM / 100,000 TPM | 是 |
| API Key | per key | 继承用户配置 | 是 |
| 模型 | per model | 由供应商限额决定 | 是 |

触发限流返回 HTTP 429，Header 包含：
```
X-RateLimit-Limit: 300
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1720000060
Retry-After: 43
```

### 6.4 安全防护

- **输入校验**: 所有请求体严格按 JSON Schema 校验，拒绝未知字段注入
- **请求体大小限制**: 单次请求最大 4MB
- **HTTPS 强制**: 生产环境仅允许 TLS 1.2+，HTTP 请求自动重定向
- **CORS**: 默认拒绝，控制台域名白名单
- **Secret 不回显**: 日志、响应中不包含任何密钥信息
- **IP 封禁**: 短时间内连续认证失败（>10次/分钟）的 IP 自动封禁 10 分钟

---

## 7. 计费系统设计

### 7.1 计费模型

采用**预付费余额制**：用户充值后获得美元余额，每次 API 调用实时扣减。

### 7.2 定价规则

**LLM 文本模型**（按 token 计费）：

| 计费项 | 单位 | 示例（gpt-4o） |
|--------|------|----------------|
| 输入 token | 每 1M tokens | $5.00 |
| 输出 token | 每 1M tokens | $15.00 |

实际扣费公式：
```
扣费金额 = (prompt_tokens / 1,000,000) × input_price
         + (completion_tokens / 1,000,000) × output_price
```

**图像生成模型**（按张计费）：

| 模型 | 规格 | 单价 |
|------|------|------|
| dall-e-3 | 1024x1024 standard | $0.040/张 |
| dall-e-3 | 1024x1024 hd | $0.080/张 |
| gpt-image-2 | 1024x1024 | $0.020/张 |

**视频生成模型**（按秒计费）：

| 模型 | 单价 |
|------|------|
| veo-2 | $0.50/秒 |
| seedance | $0.20/秒 |

### 7.3 充值套餐

| 套餐 | 充值金额 | 到账余额 |
|------|----------|----------|
| 基础 | $5 | $5.00 |
| 标准 | $20 | $20.00 |
| 专业 | $50 | $50.00 |
| 企业 | $100 | $100.00 |

> 设计决策：MVP 阶段不提供充值折扣，保持定价简单；后续可引入充值赠额活动。

### 7.4 扣费时序

```
请求到达 → 认证通过 → 预检余额（余额 ≥ 0.001 USD）
↓ 余额充足
转发上游请求 → 获取响应（含 usage）
↓
计算本次费用 → 原子扣减余额（Redis DECRBY）
↓
写入消费记录（PostgreSQL，异步写入）
↓
返回响应给用户
```

**余额预检规则**: 若账户余额 < $0.001，拒绝请求（402）；不做预扣，实际扣费在响应获取后执行。

**异常处理**: 若上游返回错误或请求超时，不扣费；若响应已部分返回（流式），按已消耗 token 计费。

### 7.5 余额一致性保障

- 余额主存储在 PostgreSQL，Redis 作为缓存层（TTL 30s）
- 扣费操作使用 PostgreSQL 事务 + `SELECT FOR UPDATE` 防并发超扣
- 每分钟定时任务校验 Redis 缓存与 DB 值的一致性，不一致时以 DB 为准刷新缓存

### 7.6 支付集成

- MVP 阶段接入 **Stripe Checkout**，支持信用卡支付
- Webhook 接收 `checkout.session.completed` 事件后更新余额
- 支付记录保留至少 7 年（合规要求）

---

## 8. 数据模型

### 8.1 User（用户）

```sql
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100),
    is_admin    BOOLEAN DEFAULT FALSE,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 8.2 ApiKey（API 密钥）

```sql
CREATE TABLE api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        VARCHAR(100) NOT NULL,
    key_prefix  CHAR(8) NOT NULL,          -- sk-AbCdEf（前8位，用于快速查询）
    key_hash    CHAR(64) NOT NULL UNIQUE,  -- SHA-256 哈希
    is_active   BOOLEAN DEFAULT TRUE,
    monthly_limit_usd NUMERIC(10, 6),     -- NULL 表示无限制
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX idx_api_keys_prefix ON api_keys(key_prefix);
```

### 8.3 Balance（账户余额）

```sql
CREATE TABLE balances (
    user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    amount_usd  NUMERIC(12, 6) NOT NULL DEFAULT 0.000000,
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 8.4 Transaction（充值与消费记录）

```sql
CREATE TABLE transactions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    type        VARCHAR(20) NOT NULL,  -- 'topup' | 'usage'
    amount_usd  NUMERIC(12, 6) NOT NULL,  -- 正数=充值，负数=消费
    balance_after NUMERIC(12, 6) NOT NULL,
    description TEXT,
    request_log_id UUID,              -- 关联请求日志（消费类型）
    stripe_payment_id VARCHAR(255),   -- 关联支付记录（充值类型）
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_transactions_user_id ON transactions(user_id, created_at DESC);
```

### 8.5 RequestLog（请求日志）

```sql
CREATE TABLE request_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    api_key_id      UUID REFERENCES api_keys(id),
    request_id      VARCHAR(64) NOT NULL UNIQUE,  -- X-Gateway-Request-ID
    model           VARCHAR(100) NOT NULL,
    provider        VARCHAR(100),
    request_type    VARCHAR(20) NOT NULL,  -- 'chat' | 'image' | 'video'
    status          VARCHAR(20) NOT NULL,  -- 'success' | 'error'
    status_code     INT,
    prompt_tokens   INT,
    completion_tokens INT,
    total_tokens    INT,
    cost_usd        NUMERIC(12, 6),
    latency_ms      INT,
    error_code      VARCHAR(100),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_request_logs_user ON request_logs(user_id, created_at DESC);
```

### 8.6 Model（模型目录）

```sql
CREATE TABLE models (
    id              VARCHAR(100) PRIMARY KEY,  -- 如 'gpt-4o'
    display_name    VARCHAR(200),
    owned_by        VARCHAR(100),
    model_type      VARCHAR(20) NOT NULL,  -- 'llm' | 'image' | 'video'
    input_price     NUMERIC(10, 6),   -- 每 1M tokens（LLM）
    output_price    NUMERIC(10, 6),   -- 每 1M tokens（LLM）
    unit_price      NUMERIC(10, 6),   -- 每次/每秒（图像/视频）
    context_window  INT,
    is_active       BOOLEAN DEFAULT TRUE,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 8.7 Provider（供应商）

```sql
CREATE TABLE providers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) UNIQUE NOT NULL,
    base_url        VARCHAR(500) NOT NULL,
    auth_type       VARCHAR(20) NOT NULL,  -- 'bearer' | 'ak_sk'
    credentials_enc TEXT NOT NULL,         -- AES-256-GCM 加密存储
    timeout_ms      INT DEFAULT 30000,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 8.8 RouteChannel（路由通道）

```sql
CREATE TABLE route_channels (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id        VARCHAR(100) NOT NULL REFERENCES models(id),
    provider_id     UUID NOT NULL REFERENCES providers(id),
    upstream_model  VARCHAR(200) NOT NULL,  -- 上游实际模型名
    weight          INT DEFAULT 100,        -- 权重（用于 weighted_random）
    priority        INT DEFAULT 0,          -- 优先级（用于 priority 策略）
    strategy        VARCHAR(30) DEFAULT 'weighted_random',
    is_active       BOOLEAN DEFAULT TRUE,
    health_status   VARCHAR(20) DEFAULT 'healthy',  -- 'healthy'|'degraded'|'down'
    last_checked_at TIMESTAMP WITH TIME ZONE,
    metadata        JSONB DEFAULT '{}'
);
CREATE INDEX idx_route_channels_model ON route_channels(model_id, is_active);
```

---

## 9. 技术栈选型

### 9.1 整体架构

```
┌─────────────────────────────────────────────┐
│               客户端 / SDK                   │
└─────────────────┬───────────────────────────┘
                  │ HTTPS
┌─────────────────▼───────────────────────────┐
│          Nginx / Caddy（反向代理）            │
│      TLS 终止、静态文件、CORS 预处理          │
└─────────────────┬───────────────────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│         FastAPI 网关服务（Python）            │
│  认证 → 限流 → 路由选择 → 转发 → 计费扣减     │
└──────┬──────────────────────────┬───────────┘
       │                          │
┌──────▼──────┐            ┌──────▼──────┐
│    Redis    │            │ PostgreSQL  │
│ 缓存/限流    │            │  主数据库    │
└─────────────┘            └─────────────┘
```

### 9.2 技术选型明细

| 层级 | 技术 | 版本 | 选型理由 |
|------|------|------|----------|
| Web 框架 | FastAPI | ≥0.115 | 原生异步、自动 OpenAPI 文档、高性能 |
| ASGI 服务器 | Uvicorn + Gunicorn | 最新稳定版 | 生产级多进程管理 |
| ORM | SQLAlchemy (async) | ≥2.0 | 成熟稳定，支持异步 |
| 数据库 | PostgreSQL | ≥16 | 事务可靠、JSONB 支持 |
| 缓存/限流 | Redis | ≥7.0 | 高性能、原子操作适合计费扣减 |
| HTTP 客户端 | httpx | ≥0.27 | 原生 async、流式支持完善 |
| 任务队列 | Redis + asyncio | — | MVP 简化，无需引入 Celery |
| 支付 | Stripe Python SDK | 最新稳定版 | 成熟的支付集成 |
| 配置管理 | Pydantic Settings | ≥2.0 | 类型安全的环境变量管理 |
| 日志 | structlog | 最新稳定版 | 结构化日志，便于查询 |
| 监控 | Prometheus + Grafana | — | 标准可观测性方案 |
| 容器 | Docker + Docker Compose | — | 开发与部署一致性 |
| 反向代理 | Caddy | ≥2.8 | 自动 HTTPS、配置简洁 |

### 9.3 项目目录结构

```
gateway/
├── app/
│   ├── main.py                 # FastAPI 应用入口
│   ├── config.py               # 环境变量配置（Pydantic Settings）
│   ├── database.py             # 数据库连接与会话管理
│   ├── redis_client.py         # Redis 连接管理
│   ├── models/                 # SQLAlchemy ORM 模型
│   │   ├── user.py
│   │   ├── api_key.py
│   │   ├── balance.py
│   │   ├── transaction.py
│   │   ├── request_log.py
│   │   ├── model_catalog.py
│   │   ├── provider.py
│   │   └── route_channel.py
│   ├── schemas/                # Pydantic 请求/响应 Schema
│   │   ├── chat.py
│   │   ├── images.py
│   │   ├── videos.py
│   │   └── dashboard.py
│   ├── routers/                # API 路由
│   │   ├── v1/
│   │   │   ├── chat.py
│   │   │   ├── images.py
│   │   │   ├── videos.py
│   │   │   ├── models.py
│   │   │   └── dashboard.py
│   │   └── admin/
│   │       ├── providers.py
│   │       └── models.py
│   ├── services/               # 核心业务逻辑
│   │   ├── auth.py             # 认证与 API Key 验证
│   │   ├── router.py           # 路由选择与故障转移
│   │   ├── billing.py          # 计费扣减
│   │   ├── upstream.py         # 上游请求转发
│   │   ├── rate_limiter.py     # 限流
│   │   └── health_checker.py   # 供应商健康检查
│   └── middleware/
│       ├── auth_middleware.py
│       └── logging_middleware.py
├── migrations/                 # Alembic 数据库迁移
├── tests/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## 10. 非功能性需求

### 10.1 性能指标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 网关自身延迟 | P99 < 50ms | 不含上游响应时间 |
| 认证耗时 | P99 < 10ms | Redis 命中时 |
| 并发请求数 | ≥ 500 QPS | 单节点目标 |
| 流式首 token 延迟 | 不引入额外延迟 | 透明代理 |
| 数据库连接池 | 最小10，最大50 | per 进程 |

### 10.2 可用性指标

| 指标 | 目标值 |
|------|--------|
| 服务可用性 (SLA) | 99.9%（月度停机 < 43分钟） |
| 故障恢复时间 (RTO) | < 5分钟 |
| 数据持久化 | RPO = 0（事务日志实时同步） |
| 多副本 | 生产环境至少 2 个网关实例 |

### 10.3 可观测性

**Metrics（Prometheus）**:
- `gateway_requests_total{model, provider, status}` — 请求总量
- `gateway_request_duration_seconds{model, provider}` — 请求延迟分布
- `gateway_upstream_errors_total{provider, error_type}` — 上游错误计数
- `gateway_active_connections` — 当前活跃连接数
- `gateway_balance_operations_total{type}` — 余额操作计数

**Logging（structlog，JSON 格式）**:
- 每次请求记录：request_id、user_id、model、provider、status_code、latency_ms、cost_usd
- 错误日志含完整 stack trace
- 日志保留 90 天

**Tracing**: 可选接入 OpenTelemetry，追踪完整请求链路

**告警规则**:
- 上游错误率 > 10%（5分钟窗口）→ Slack 告警
- 服务 P99 延迟 > 5s → PagerDuty
- 熔断器触发 → 立即告警

### 10.4 安全合规

- 所有传输使用 TLS 1.2+
- 数据库静态加密（PostgreSQL 字段级加密用于凭证）
- 定期安全扫描（依赖漏洞 + SAST）
- GDPR 合规：支持用户数据删除请求（账户注销后 30 天内清除 PII）
- 支付数据不落本地存储（由 Stripe 持有），本地只存 Payment Intent ID

---

## 11. 交付范围

### 11.1 MVP 范围（本次交付）

以下功能在 MVP 版本中交付：

**后端服务（FastAPI）**:
- [x] 用户注册 / 登录（邮箱+密码）
- [x] API Key 创建、列表、撤销
- [x] `GET /v1/models` — 模型列表接口
- [x] `POST /v1/chat/completions` — LLM 对话（含流式）
- [x] `POST /v1/images/generations` — 图像生成
- [x] 多通道路由（weighted_random 策略）
- [x] 基础故障转移（单次重试）
- [x] 预付费余额系统（Redis + PostgreSQL 双写）
- [x] Stripe 充值（$5/$20/$50/$100 套餐）
- [x] 请求日志记录（异步写入）
- [x] Rate Limiting（用户级 RPM 限制）
- [x] 管理后台：供应商配置、模型上下架

**已接入供应商（MVP）**:
- [x] OpenAI（GPT-4o、GPT-4o-mini、DALL-E-3）
- [x] Anthropic（Claude Opus 5、Claude Sonnet 5、Haiku 4.5）
- [x] Google AI（Gemini 2.5 Pro、Gemini 2.5 Flash）

**前端控制台（最小可用）**:
- [x] 登录 / 注册页
- [x] API Key 管理页
- [x] 余额展示与充值页
- [x] 请求日志列表页（分页）

### 11.2 路线图（MVP 之后）

| 优先级 | 功能 | 预估周期 |
|--------|------|----------|
| P1 | 视频生成接口（异步任务轮询） | Sprint 2 |
| P1 | 更多供应商接入（xAI、Azure OpenAI、ByteDance） | Sprint 2 |
| P1 | lowest_latency 路由策略 | Sprint 2 |
| P1 | API Key 月度限额设置 | Sprint 3 |
| P2 | 控制台用量图表（按日/模型统计） | Sprint 3 |
| P2 | 余额预警邮件通知 | Sprint 3 |
| P2 | Team / Organization 多账户管理 | Sprint 4 |
| P2 | OpenAI 兼容 Function Calling 完整支持 | Sprint 4 |
| P3 | 自定义路由规则（用户层面指定供应商偏好） | TBD |
| P3 | 用量导出（CSV） | TBD |
| P3 | 企业 SLA 与专属通道 | TBD |

### 11.3 不在交付范围内

以下内容明确不在本次或近期交付范围：

- 移动端 App
- 多语言国际化（i18n）
- 自助发票开具
- 供应商 Prompt 缓存（Provider-side caching）
- 模型微调（Fine-tuning）接口

---

*文档结束*

*最后更新：2026-08-13*


---

## 9. 技术栈选型

### 9.1 核心技术栈

| 层次 | 技术 | 版本 | 选型理由 |
|------|------|------|---------|
| Web 框架 | FastAPI | 0.111+ | 原生异步、自动 OpenAPI 文档、Pydantic 集成 |
| ASGI 服务器 | Uvicorn | 0.30+ | 高性能、支持 HTTP/1.1 与 WebSocket |
| 数据库 | SQLite（开发）/ PostgreSQL（生产） | 3.45 / 16 | 开发零依赖，生产级并发与事务支持 |
| ORM | SQLAlchemy | 2.0+ | 异步支持（AsyncSession），成熟生态 |
| 缓存 | 内存字典（开发）/ Redis（生产） | — / 7.x | 认证 Token 缓存、限流计数器 |
| HTTP 客户端 | httpx | 0.27+ | 原生异步、支持 SSE 流式转发 |
| 数据验证 | Pydantic | 2.x | 请求/响应 Schema 强校验 |
| 日志 | loguru | 0.7+ | 结构化日志，JSON 输出便于采集 |
| 依赖注入 | FastAPI DI | — | 内置，无需额外框架 |
| 测试框架 | pytest + pytest-asyncio + httpx | — | 异步测试完整支持 |

### 9.2 目录结构

```
AgentTeam/
├── src/
│   ├── main.py              # 应用入口，路由挂载，中间件注册
│   ├── config.py            # 配置管理（环境变量 + 默认值）
│   ├── database.py          # 数据库连接与 Session 工厂
│   ├── models.py            # SQLAlchemy ORM 数据模型
│   ├── routers/
│   │   ├── chat.py          # POST /v1/chat/completions
│   │   ├── images.py        # POST /v1/images/generations
│   │   ├── models_list.py   # GET /v1/models
│   │   ├── videos.py        # POST /v1/videos/generations（P1）
│   │   └── dashboard.py     # GET /v1/dashboard/balance
│   ├── providers/
│   │   ├── base.py          # 供应商抽象基类
│   │   ├── openai_provider.py
│   │   ├── anthropic_provider.py
│   │   └── gemini_provider.py
│   ├── middleware/
│   │   ├── auth.py          # Bearer Token 认证
│   │   ├── billing.py       # 余额预检与扣费
│   │   └── rate_limit.py    # 限流中间件
│   └── services/
│       ├── router.py        # 路由引擎（通道选择 + 故障转移）
│       └── health.py        # 供应商健康检查后台任务
├── tests/
│   ├── conftest.py
│   ├── test_api_compatibility.py
│   ├── test_routing.py
│   ├── test_failover.py
│   ├── test_auth.py
│   ├── test_billing.py
│   └── test_load.py
├── docs/
│   ├── DEPLOYMENT.md
│   ├── TEST_REPORT.md
│   └── CODE_REVIEW.md
├── requirements.txt
├── .env.example
└── prd/
    ├── PRD.md
    └── PRD_REVIEW.md
```

### 9.3 关键依赖版本锁定

```
fastapi==0.111.0
uvicorn[standard]==0.30.1
sqlalchemy==2.0.31
aiosqlite==0.20.0          # SQLite 异步驱动
httpx==0.27.0
pydantic==2.7.4
pydantic-settings==2.3.1
loguru==0.7.2
pytest==8.2.2
pytest-asyncio==0.23.7
cryptography==42.0.8       # AES-256-GCM 凭证加密
```

---

## 10. 非功能性需求

### 10.1 性能指标

| 指标 | MVP 目标 | 生产目标 |
|------|---------|---------|
| 网关自身增加延迟（P99） | < 50ms | < 20ms |
| 并发连接数 | 100 | 5,000 |
| 非流式响应 P95 延迟 | < 上游 + 100ms | < 上游 + 30ms |
| 流式首 Token 延迟（TTFT） | < 上游 + 80ms | < 上游 + 30ms |
| 吞吐量（RPS） | 50 | 2,000 |

### 10.2 可用性

- 上游单通道故障不影响服务可用性（多通道 Fallback）
- 应用层故障自动重启（Supervisor / systemd）
- 目标可用性：MVP 阶段 99.5%，生产 99.9%

### 10.3 安全性

- 所有供应商凭证 AES-256-GCM 加密存储
- API Key 仅存哈希值，原文不持久化
- 日志中所有敏感字段（api_key, credentials）自动脱敏
- 请求体大小上限 4MB，防止 OOM 攻击
- 生产强制 TLS 1.2+

### 10.4 可观测性

- **日志**：结构化 JSON 日志，包含 request_id、user_id、model、latency、cost
- **指标**：记录每个路由通道的成功率、延迟分布、调用量
- **链路追踪**：每请求生成唯一 `X-Gateway-Request-ID`，贯穿完整调用链
- **健康检查端点**：`GET /health` 返回服务状态与各供应商连通性

### 10.5 可维护性

- 新增供应商无需改代码，仅需实现 `BaseProvider` 接口并注册
- 新增模型仅需在数据库中插入 `models` + `route_channels` 记录
- 所有配置通过环境变量管理，支持 `.env` 文件

---

## 11. 交付范围

### 11.1 MVP 交付范围（本次交付）

以下功能在本次交付中完整实现：

| 模块 | 功能点 |
|------|--------|
| API 接口 | `POST /v1/chat/completions`（流式 + 非流式）|
| API 接口 | `GET /v1/models` 模型列表 |
| API 接口 | `POST /v1/images/generations` 图像生成 |
| API 接口 | `GET /v1/dashboard/balance` 余额查询 |
| 路由引擎 | weighted_random 策略、priority 策略 |
| 路由引擎 | 故障转移（最多重试 2 次）、熔断器 |
| 供应商适配 | OpenAI（LLM + Image）、Anthropic（LLM）、Google Gemini（LLM）|
| 认证 | Bearer Token 验证，API Key CRUD |
| 计费 | 余额预检、Token 计费扣减、消费记录 |
| 限流 | per-user RPM 限流（内存令牌桶）|
| 数据存储 | SQLite（开发环境），可切换 PostgreSQL |
| 测试 | 单元测试 + 集成测试，覆盖率 ≥ 70% |
| 文档 | 部署文档、API 使用示例 |

### 11.2 路线图（不在本次交付）

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 视频生成接口 | P1 | 异步任务轮询，接入 Veo / Seedance |
| 管理后台 Web UI | P1 | 供应商管理、用量统计可视化 |
| Stripe 支付充值 | P1 | 接入 Stripe Checkout，自动更新余额 |
| Redis 缓存层 | P1 | 替换内存缓存，支持水平扩展 |
| xAI / Azure OpenAI 适配 | P2 | 扩展供应商覆盖 |
| 用户注册/登录 Web UI | P2 | 完整的 Web 控制台 |
| 请求日志查询接口 | P2 | `GET /v1/dashboard/logs` |
| Prometheus 指标导出 | P2 | 生产监控接入 |
| 多区域部署 | P3 | 就近接入，降低延迟 |

### 11.3 验收标准

1. `POST /v1/chat/completions` 可使用 OpenAI Python SDK 直接调用（修改 base_url 和 api_key 即可）
2. 上游供应商故障时，系统自动切换备用通道，用户无感知
3. 无效 API Key 返回标准 401 错误结构
4. 余额不足时返回标准 402 错误结构
5. 所有测试用例通过（pytest），核心路径覆盖率 ≥ 70%
6. 服务可通过 `uvicorn src.main:app` 在本地启动并正常响应请求

---

> **文档状态更新**: 2026-08-13 — 全章节完成，共 11 章，提交审核
