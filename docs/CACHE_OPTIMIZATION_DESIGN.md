# ModelHub 多供应商缓存优化设计

> 目标：在网关层针对每家供应商做缓存优化，最大化上游「前缀缓存」命中率，降低 token 成本。
> 参考：Reasonix（DeepSeek-Reasonix）「把前缀缓存稳定性当作全局不变量」的方法论，推广到全部 11 家供应商。
> 日期：2026-08-16

---

## 一、调研结论：所有厂商都支持前缀缓存

2024–2026 行业趋势：几乎每家主流供应商都上线了「前缀/上下文缓存」。核心规律高度一致——**前缀从第 0 个 token 完全匹配才命中**，命中部分按远低于原价的费率计费。

| 供应商 | 缓存类型 | 命中折扣 | 写缓存成本 | 最低门槛 | TTL | 缓存命中字段 |
|--------|---------|---------|-----------|---------|-----|------------|
| DeepSeek | 前缀硬盘缓存（自动） | 命中 ≈ 输入价 1/10（V4 达 1/120） | 无 | 64 token | 几小时~几天 | `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` |
| 火山方舟豆包 | 透明前缀缓存（自动） | 命中 ≈ 2 折 | 显式模式有存储费 | — | — | `usage` 命中字段 |
| 智谱 GLM | 上下文缓存（隐式自动） | 命中 ≈ 5 折（部分模型 82% off） | 无 | — | 时效限制 | `prompt_tokens_details.cached_tokens` |
| Kimi | 上下文缓存（自动） | 命中 = 输入价 1/10 | 无 | — | — | `usage` 命中字段 |
| 阿里百炼 Qwen | Context Cache（显式+隐式，互斥） | 显式命中 1/10，隐式命中 2/10 | 显式创建 1.25x | 显式 1024 / 隐式 256 | 显式 5min | `usage` 命中字段 |
| MiniMax | Prompt 缓存（自动） | 命中 ≈ 2 折 | 无 | 512 token | 自动 | `cache_creation_input_tokens` / `cached_tokens` |
| 腾讯混元 | 前缀缓存 + Session 缓存 | 命中 ≈ 1/3~1/4 | 无 | — | — | `prompt_cache_key` + `X-Session-ID` 提升命中 |
| OpenAI | Prompt Caching（自动） | 命中 ≈ 5 折（GPT-5.6+ 1/10） | 旧免费 / 新 1.25x | 1024 | 5–60min | `prompt_tokens_details.cached_tokens` |
| Anthropic Claude | Prompt Caching（显式） | 命中 = 1/10（90% off） | 写 1.25x(5m)/2x(1h) | 1024 | 5min / 1h | `cache_creation_input_tokens` / `cache_read_input_tokens` |
| Grok | Prompt Caching（自动） | 命中 ≈ 75% off | 无 | — | — | `prompt_cache_key` / `x-grok-conv-id` 提升命中 |
| Gemini | Context Caching（显式） | 命中 = 1/4（75% off） | + 存储小时费 | 32K | ≥1h | cachedContent token count |

**关键事实**：
1. 折扣从 50%（OpenAI 自动）到 99%（DeepSeek V4、Claude）不等。
2. **所有厂商命中规则一致**：前缀字节级稳定。谁破坏前缀，谁就全价。
3. 区分「自动前缀缓存」（DeepSeek/Kimi/MiniMax/OpenAI/Grok/智谱/豆包/混元）与「显式缓存」（Claude/Gemini/百炼显式模式）两类。

---

## 二、设计原则（借鉴 Reasonix）

Reasonix 在 DeepSeek 上做到 99.82% 命中率，其原则**对所有前缀缓存厂商通用**：

1. **缓存稳定不是开关，是整个请求链路要守住的不变量。**
2. **固定高价值前缀**：`system prompt` + `tools 定义` + `few-shot 示例` 三者的字节序列一旦确定就固定，排序稳定（工具数组按固定顺序序列化）。
3. **易变内容隔离**：时间戳、随机 ID、每轮临时状态、思考草稿——绝不放进前缀，一律放最后，或干脆不上送上游。
4. **追加式（append-only）会话**：历史消息只追加、不重写，重写会摧毁前缀。
5. **缓存感知压缩**：上下文压缩时用确定性摘要（temperature=0）+ 哈希复用，且窗口固定在前 N 条，不破坏尾部前缀。
6. **命中率可观测**：每一轮都计算并暴露 `命中 token / (命中 + 未命中)`。

---

## 三、分层设计

网关做缓存优化分四层，按「性价比/落地优先级」排序：

### Layer 1 — 前缀缓存友好层（全厂商通用，核心）

**位置**：`router.route_chat` 调 adapter 之前，对 `payload` 做统一预处理。

**职责**：
1. **前缀稳定化**（`PrefixStabilizer`）：
   - 规范化 `system`、`tools` 的序列化顺序与格式（如 tools 按 `function.name` 排序），保证跨请求字节一致。
   - 剥离/下移易变字段（时间戳、随机 ID、`tool_choice` 之外的动态注入）。
2. **易变草稿隔离**（`VolatileScratch`）：
   - 识别多轮对话中的临时/易变内容，从发送序列中剔除或置尾。
3. **缓存键管理**（`CacheKeyManager`）：
   - 统一生成/维护会话级稳定 ID，注入支持 cache-key 的厂商（混元 `prompt_cache_key` / `X-Session-ID`、Grok `x-grok-conv-id` / `prompt_cache_key`）。

### Layer 2 — 显式缓存注入（厂商特化）

**位置**：各适配器（`openai_provider` / `anthropic_provider` / `gemini_provider`）内部。

**职责**：对「显式缓存」厂商自动注入缓存标记，把缓存从「碰运气」变成「确定性」：
- **Anthropic Claude**：在 `system` + `tools` 之后自动加 `cache_control: {"type": "ephemeral"}`（≥1024 token 才生效）。
- **Gemini**：预创建 `cachedContent` 对象，后续请求引用 cache 名（≥32K token 才划算，需按上下文长度判断）。
- **阿里百炼 Qwen 显式模式**：走显式缓存创建接口，命中 1/10（需评估与隐式模式的收益差）。

### Layer 3 — 缓存命中监控（全厂商通用）

**位置**：响应解析层（非流式 `usage` 解析 + 流式最后 chunk 的 `usage`）。

**职责**：统一解析各家缓存字段，归一化后写入 `request_logs`，计算命中率，暴露到 dashboard：
- 归一化字段：`cache_hit_tokens` / `cache_miss_tokens` / `cache_hit_ratio`
- 数据来源映射：见第一节表格「缓存命中字段」列。

### Layer 4 — 网关语义缓存（进阶，可选）

**位置**：`route_chat` 最前，命中即返回，不触上游。

**职责**：对确定性请求（`temperature=0`、无 `tools`、纯查询）做语义/精确匹配缓存。
- 精确前缀匹配：哈希 `model + messages` 完全一致才命中（安全）。
- 语义缓存：embedding 相似度 + 阈值（有正确性风险，默认关闭，白名单开启）。
- 这一层是「锦上添花」，成本最高、风险最大，**最后再做**。

---

## 四、各供应商针对性方案

按「缓存类型」分三组，每组给统一策略 + 厂商特例。

### A 组：自动前缀缓存（网关只需「守前缀」）

> DeepSeek、火山方舟豆包、智谱 GLM、Kimi、MiniMax、OpenAI、Grok、混元

统一策略 = **Layer 1 全量**：
1. system / tools / few-shot 字节稳定、排序固定。
2. 易变内容置尾、不上送草稿。
3. 会话 append-only，历史不重写。
4. 监控命中率（Layer 3）。

厂商特例：

| 厂商 | 额外动作 | 原因 |
|------|---------|------|
| **DeepSeek** | 优先级最高（命中便宜 10~120 倍，64 token 门槛全网最低） | 小前缀也能命中，最值得「锁死」前缀 |
| **混元** | 注入 `prompt_cache_key` + `X-Session-ID` | 官方建议，命中率 1/3~1/4 |
| **Grok** | 注入 `x-grok-conv-id`（chat）或 `prompt_cache_key`（responses） | 官方建议，命中 75% off |
| **智谱 GLM** | 留意 2026-06 缓存计费争议，命中率异常时告警 | 有词元泄露/计费不透明报道 |
| **火山方舟豆包** | 可评估「显式上下文缓存」模式（有存储小时费） | 透明前缀 vs 显式，按调用频率取舍 |
| **MiniMax** | 注意 ≥512 token 门槛，短请求无缓存收益 | 前缀按「工具→系统→历史」顺序 |

### B 组：显式缓存（网关要主动注入标记）

> Anthropic Claude、Gemini、百炼 Qwen 显式模式

| 厂商 | 网关动作 | 收益 | 门槛/注意 |
|------|---------|------|----------|
| **Anthropic Claude** | 适配器在 `system`+`tools` 后注入 `cache_control: ephemeral` | 命中 90% off | ≥1024 token；写缓存 1.25x，需 ≥2 次读才回本 |
| **Gemini** | 上下文 ≥32K 时预建 `cachedContent` 并复用 | 命中 75% off | 32K 门槛 + 存储小时费，短上下文不划算 |
| **百炼 Qwen** | 按调用频率选显式（命中 1/10，5min TTL）或隐式（命中 2/10 自动） | 显式更深 | 显式/隐式互斥；显式创建 1.25x |

### C 组：网关语义缓存（Layer 4，可跨越供应商）

- 精确匹配优先（安全、零风险）。
- 语义缓存默认关，仅对白名单模型 + 低温度请求开启。

---

## 五、实施路线图

| 阶段 | 内容 | 收益 | 复杂度 |
|------|------|------|--------|
| **P0** | Layer 3 命中监控：解析 usage 缓存字段 → `request_logs` → dashboard | 先看得见命中率，验证效果 | 低 |
| **P1** | Layer 1 前缀稳定化 + 易变隔离 + cache-key 注入（混元/Grok） | 覆盖 A 组 8 家，核心收益 | 中 |
| **P2** | Layer 2 显式缓存注入（Claude `cache_control` 优先） | 覆盖 B 组，Claude 命中 90% off | 中 |
| **P3** | Gemini cachedContent + 百炼显式模式 | B 组补全 | 中高 |
| **P4** | Layer 4 语义缓存（精确匹配先上） | 确定性请求零成本 | 高 |

**建议先做 P0 + P1**：成本低、覆盖 8 家自动缓存厂商，立刻能看到「命中率提升 → 上游 token 费下降」的性价比。

---

## 六、风险与正确性

1. **前缀重排可能改变语义**：tools 排序、system 规范化必须保持语义等价（排序不影响模型理解，但需测试）。
2. **易变内容剥离过度**：误删有意义的历史会降低回答质量，需保守策略 + 可开关。
3. **显式缓存写成本**：Claude/Gemini/百炼显式有额外写费/存储费，需按「读次数 ≥ 回本阈值」才注入，否则反而更贵。
4. **语义缓存错误结果**：相似 ≠ 相同，可能返回过期/错误答案，必须精确匹配优先、语义缓存严格白名单。
5. **流式命中字段**：部分厂商只在非流式 `usage` 返回缓存字段，流式需从最后 chunk 或 `stream_options.include_usage` 获取，实现时逐家验证。

---

## 附：参考来源

- [DeepSeek 上下文硬盘缓存](https://api-docs.deepseek.com/news/news0802/) — 前缀匹配、64 token 单元、命中字段
- [Reasonix / DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix) — 前缀缓存稳定方法论（99.82% 命中率）
- [智谱上下文缓存](https://docs.bigmodel.cn/cn/guide/capabilities/cache) — 隐式缓存 + `cached_tokens`
- [MiniMax Prompt 缓存](https://platform.minimaxi.com/docs/api-reference/text-prompt-caching) — 自动缓存 + 计费示例
- [阿里云百炼 Context Cache](https://help.aliyun.com/zh/model-studio/context-cache) — 显式/隐式互斥、折扣比例
- [Grok Prompt Caching](https://docs.x.ai/developers/advanced-api-usage/prompt-caching) — 自动前缀 + cache key
- [Anthropic / OpenAI / Gemini 缓存对比](https://www.edenai.co/post/prompt-caching-claude-vs-gpt-vs-gemini-cost-playbook) — 三家机制/定价/TTL
