# PRD 审核报告

> **审核人**: Reviewer  
> **审核日期**: 2026-08-13  
> **审核对象**: `/home/xiaofeiyang/AIWorkSpace/ModelHub/prd/PRD.md`  
> **文档版本**: v1.0（草稿）  

---

## 总体结论

✅ **PRD 审核通过（附条件）**

文档整体产品思路清晰，功能覆盖较为完整，技术选型合理，接口设计与 OpenAI 规范兼容性良好。但存在 **1 个结构性严重问题** 和若干需要在开发启动前澄清的问题。开发工程师在开始实现前，应优先阅读本审核报告中标注的"实现前必须澄清"事项，并以本报告的建议作为实现依据。

---

## 一、严重问题（Critical）

### C-1：第 9、10、11 章重复出现，内容相互矛盾

**位置**: PRD.md 第 749-839 行 vs 第 960-1129 行

文档中第 9、10、11 章各出现**两次**，且两版本在以下关键技术决策上相互冲突：

| 决策点 | 第一版（第 9 章，行~749） | 第二版（行~960，当前正确版） |
|--------|--------------------------|------------------------------|
| 数据库 | PostgreSQL 16（强制） | SQLite（开发）/ PostgreSQL（生产） |
| 缓存 | Redis 7.0（强制） | 内存字典（开发）/ Redis（生产） |
| 日志库 | structlog | loguru |
| Stripe 支付 | MVP 范围内 | 路线图（不在本次交付） |
| 前端控制台 | MVP 范围内 | 未提及 |
| 目录结构 | gateway/ 下 | AgentTeam/src/ 下 |

**结论**: 以**第二版（行 960 起）** 为准，即采用 SQLite/PostgreSQL 双模式、loguru 日志、Stripe 延后的方案。第一版内容应删除，以消除歧义。

**开发注意**: 目录结构以第二版 `AgentTeam/src/` 为准；依赖版本以第二版锁定版本（行 1025-1037）为准。

---

## 二、重大问题（Major）

### M-1：第 3 章缺少认证与 API Key 管理的 HTTP 接口规范

第 2 章功能清单中定义了用户注册（F-001）、登录（F-002）、API Key 创建/列表/撤销（F-010~F-013），但第 3 章 API 接口规范中**完全没有对应的端点**定义。

**缺失端点**（开发实现时应遵循以下规范）：

```
POST /v1/auth/register     注册（email + password）
POST /v1/auth/login        登录（返回 JWT access_token）
POST /v1/auth/logout       登出（使 token 失效）

GET    /v1/dashboard/keys           列出当前用户所有 API Key
POST   /v1/dashboard/keys           创建 API Key
DELETE /v1/dashboard/keys/{key_id}  撤销 API Key
```

**认证说明**（需在 PRD 中澄清，开发按此实现）：
- `/v1/auth/*` 接口不需要 Bearer API Key，使用 email/password
- `/v1/dashboard/*` 接口使用 JWT Token（登录后颁发），不是 API Key
- `/v1/chat/*`、`/v1/images/*`、`/v1/models` 使用 `sk-` 格式的 API Key

### M-2：计费扣减写路径存在歧义

**位置**: 第 7.4 节 vs 第 7.5 节

- 7.4 节扣费时序图中写："原子扣减余额（**Redis DECRBY**）"
- 7.5 节写："余额主存储在 **PostgreSQL**，扣费操作使用 PostgreSQL 事务 + `SELECT FOR UPDATE`"

两处描述构成矛盾：Redis DECRBY 是原子操作但非持久化；PostgreSQL SELECT FOR UPDATE 是持久化但非最快路径。

**建议实现方案**（以此为准）：
1. 扣费时先通过 PostgreSQL `SELECT FOR UPDATE` 执行原子扣减并写入 transaction 记录（保证一致性和持久化）
2. 扣费成功后异步刷新 Redis 缓存（TTL 30s）
3. 余额预检（判断 ≥ $0.001）可读 Redis 缓存加速，但正式扣减必须走 PostgreSQL

### M-3：数据模型缺少模型别名表

**位置**: 第 4.5 节提及"支持为模型配置别名"，但第 8 章数据模型中没有对应表。

**建议新增**：

```sql
CREATE TABLE model_aliases (
    alias       VARCHAR(100) PRIMARY KEY,  -- 如 'gpt-4-turbo'
    model_id    VARCHAR(100) NOT NULL REFERENCES models(id),
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### M-4：数据模型缺少视频任务表

**位置**: 第 3.6 节定义了异步视频生成任务（GET /v1/videos/tasks/{task_id}），但第 8 章无对应表。

**建议新增**：

```sql
CREATE TABLE video_tasks (
    id              VARCHAR(64) PRIMARY KEY,  -- vtask-xxx
    user_id         UUID NOT NULL REFERENCES users(id),
    api_key_id      UUID REFERENCES api_keys(id),
    model           VARCHAR(100) NOT NULL,
    prompt          TEXT NOT NULL,
    status          VARCHAR(20) DEFAULT 'pending',  -- pending|processing|succeeded|failed
    result_url      VARCHAR(1000),
    duration_seconds INT,
    billed_seconds  INT,
    cost_usd        NUMERIC(12, 6),
    error_message   TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at    TIMESTAMP WITH TIME ZONE
);
```

注意：视频生成为 P1 功能，MVP 不交付，但数据模型可提前建好。

### M-5：route_channels.strategy 字段位置设计不合理

**位置**: 第 8.8 节

`strategy` 字段定义在 `route_channels` 表（per 通道），但路由策略在逻辑上应该是 per 模型（即同一模型的所有通道共享同一策略）。同一模型下通道 A 用 `weighted_random`、通道 B 用 `priority` 在实现上语义不清。

**建议**: 将 `strategy` 字段移至 `models` 表，或新增 `route_groups` 表：

```sql
ALTER TABLE models ADD COLUMN route_strategy VARCHAR(30) DEFAULT 'weighted_random';
```

并从 `route_channels` 表中删除 `strategy` 字段。

---

## 三、次要问题（Minor）

### m-1：Redis 缓存 TTL 不一致

- 第 6.2 节认证缓存 TTL：60 秒
- 第 7.5 节余额缓存 TTL：30 秒

TTL 设置本身合理，但建议在文档中统一说明各缓存 key 的 TTL 策略，便于开发直接引用。

### m-2：RouteChannel.health_status 枚举值与熔断器状态不完整

**位置**: 第 8.8 节与第 5.3 节

第 5.3 节熔断器有三个状态：`Closed`（正常）、`Open`（熔断）、`Half-Open`（探测）。
第 8.8 节 `health_status` 枚举为 `healthy | degraded | down`，与熔断状态不对应，实现时会产生歧义。

**建议**将 `health_status` 统一为：`healthy | degraded | circuit_open | circuit_half_open`

### m-3：RequestLog 表缺少图像/视频计费字段

`request_logs` 表中仅有 `prompt_tokens`、`completion_tokens`、`total_tokens`，对图像生成（按张计费）和视频生成（按秒计费）缺乏对应字段。

**建议新增**：

```sql
ALTER TABLE request_logs ADD COLUMN image_count INT;         -- 图像生成张数
ALTER TABLE request_logs ADD COLUMN video_seconds NUMERIC;   -- 视频生成秒数
```

### m-4：注册流程未说明邮箱验证

F-001 用户注册仅提及"邮箱注册"，未说明是否需要邮箱验证（发送确认邮件）。建议明确：MVP 阶段免邮件验证，直接注册成功即可用；后续版本可补充邮箱验证。

### m-5：管理员认证机制未说明

第 2.9 节描述了管理后台功能（F-080~F-082），但未说明管理员如何认证（是否独立端点、是否需要特殊 Token、`is_admin` 字段如何校验）。建议说明：admin 接口通过 JWT 认证 + `is_admin=true` 校验实现，不需要单独认证体系。

---

## 四、改进建议

以下为正向建议，不影响审核通过，但建议在实现时或后续版本中采纳：

1. **Stripe Webhook 端点**: 建议在 API 规范中补充 `POST /webhooks/stripe` 的说明（即使 MVP 不实现，留作占位有助于 QA 编写测试骨架）。
2. **健康检查探针**: 第 10.4 节提到 `GET /health`，建议在 API 规范中补充其响应格式，便于运维和测试使用。
3. **密码强度校验**: 第 6 章安全设计中未提及注册密码强度要求，建议至少规定最小长度（8 位）和复杂度要求。
4. **流式响应计费精度**: 第 7.4 节说明流式情况下"按已消耗 token 计费"，建议明确：若上游在流式传输中途断开，token 计数以响应体中已接收的 `usage` 字段为准；若无 `usage` 字段，按估算值（prompt 长度）计费，并记录 `estimated=true` 标记。

---

## 五、各章节评分

| 章节 | 完整性 | 清晰度 | 可实现性 | 问题级别 |
|------|--------|--------|----------|---------|
| 1. 项目概述 | 优 | 优 | — | 无 |
| 2. 功能清单 | 良 | 优 | 良 | Minor（m-4） |
| 3. API 接口规范 | 中 | 优 | 中 | Major（M-1）|
| 4. 路由策略 | 良 | 优 | 良 | Major（M-3, M-5） |
| 5. 供应商管理 | 优 | 优 | 优 | Minor（m-2） |
| 6. 认证与安全 | 良 | 优 | 良 | Minor（m-1, m-5） |
| 7. 计费系统 | 良 | 良 | 中 | Major（M-2） |
| 8. 数据模型 | 中 | 优 | 中 | Major（M-3, M-4, M-5）+ Minor（m-3） |
| 9. 技术栈 | — | — | — | Critical（C-1，重复）|
| 10. 非功能性需求 | 良 | 优 | 良 | Critical（C-1，重复）|
| 11. 交付范围 | 良 | 良 | 良 | Critical（C-1，重复）|

---

## 六、实现前必须确认的清单

开发工程师在开始实现前，请确认以下事项（可直接按本审核报告中的建议实现，无需等待 ProductManager 修订文档）：

- [ ] 目录结构以 `AgentTeam/src/` 为准（第二版第 9 章）
- [ ] 技术栈以第二版依赖锁定版本为准（loguru、aiosqlite 等）
- [ ] 认证接口（register/login/logout）和 API Key CRUD 接口按 M-1 建议实现
- [ ] 计费扣减走 PostgreSQL SELECT FOR UPDATE，Redis 作为读缓存（M-2）
- [ ] RouteChannel.strategy 字段移至模型层（M-5）
- [ ] 新增 model_aliases 表（M-3，MVP 可不实现功能，但表可建）
- [ ] RequestLog 新增 image_count / video_seconds 字段（m-3）

---

*审核完成时间: 2026-08-13*
