"""
供应商注册表：11 家供应商元数据配置（单一数据源）

- 每供应商一条 ProviderSpec
- model_source='api'：添加 Key 后自动 GET /models 拉取（DeepSeek/Moonshot/MiniMax/OpenAI/Claude/Grok/Gemini）
- model_source='static'：无官方 /models 端点，使用内置清单（方舟 Coding Plan/混元/百炼/智谱）
- 静态清单价格为官方文档人工核对值（price_source='official'）；无法核实的用网关默认价（'default'）
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class DefaultPrices:
    """网关默认价（每 1M tokens，USD）；用于无官方定价的 api 拉取模型"""
    input_price: Optional[float] = 2.0
    output_price: Optional[float] = 8.0
    unit_price: Optional[float] = None


@dataclass(frozen=True)
class StaticModelDef:
    """静态清单模型定义"""
    id: str                       # 网关模型 ID
    upstream_model: str           # 上游真实模型名
    display_name: str
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    context_window: Optional[int] = None
    price_source: str = "official"   # official=官方文档价 | default=网关默认价


@dataclass(frozen=True)
class ProviderSpec:
    key: str                          # Provider.name（DB unique）
    display_name: str                 # UI 显示名
    default_base_url: str
    auth_type: str = "bearer"         # bearer | x_api_key | api_key_query
    adapter: str = "openai"           # openai | anthropic | gemini | mock
    model_source: str = "api"         # api | static
    models_parser: str = "openai"     # openai | anthropic | gemini | grok
    pricing_mode: str = "default"     # official(响应带价) | default
    default_prices: DefaultPrices = field(default_factory=DefaultPrices)
    static_models: Tuple[StaticModelDef, ...] = ()
    exclude_patterns: Tuple[str, ...] = ()   # 非 chat 模型过滤
    known_prices: Tuple[Tuple[str, float, float], ...] = ()  # (模型ID, 输入价, 输出价) 官方文档核对价
    known_contexts: Tuple[Tuple[str, int], ...] = ()  # (模型ID, 上下文窗口) 官方文档核对值


def _excluded(model_id: str, patterns: Tuple[str, ...]) -> bool:
    """按 ID 前缀/包含过滤非 chat 模型（embedding/tts/image 等）"""
    if not patterns:
        return False
    lower = model_id.lower()
    return any(p in lower for p in patterns)


# ── 注册表定义 ────────────────────────────────────────────────────────────────

_PROVIDER_SPECS = [
    # ── DeepSeek（api 拉取，标准 OpenAI 结构）────────────────────────────────
    # 官方定价页 api-docs.deepseek.com/zh-cn/quick_start/pricing（元/1M，按 7.2 折算 USD）：
    # v4-flash 输入 1 元/输出 2 元 → 0.14/0.28；v4-pro 输入 3 元/输出 6 元 → 0.42/0.83
    # 上下文：官方文档标注 V4 系列 1M（api-docs.deepseek.com 快速上手）
    ProviderSpec(
        key="deepseek", display_name="DeepSeek",
        default_base_url="https://api.deepseek.com/v1",
        known_prices=(
            ("deepseek-v4-flash", 0.14, 0.28),
            ("deepseek-v4-pro", 0.42, 0.83),
        ),
        known_contexts=(
            ("deepseek-v4-flash", 1048576),
            ("deepseek-v4-pro", 1048576),
        ),
    ),
    # ── 火山方舟 Coding Plan（无 /models 端点，静态清单；模型名直填不能带日期）──
    # 计费机制：套餐按"次数"扣额度（token 折算），无按量单价；下方价格为官方按量参考价
    # （方舟官方按量价优先，未查到的用原厂官方价，均按 7.2 汇率折算 USD）
    # 上下文：官方套餐概览（docs/82379/2366394 与模型列表页 1330310）
    ProviderSpec(
        key="ark-plan", display_name="火山方舟 Coding Plan",
        default_base_url="https://ark.cn-beijing.volces.com/api/coding/v1",
        model_source="static",
        static_models=(
            StaticModelDef("ark-doubao-seed-2-1-turbo", "doubao-seed-2-1-turbo-260628", "Doubao Seed 2.1 Turbo (方舟)", 0.42, 2.08, 262144, "official"),
            StaticModelDef("ark-doubao-seed-2-0-lite", "doubao-seed-2-0-lite-260428", "Doubao Seed 2.0 Lite (方舟)", 0.08, 0.5, 262144, "official"),
            StaticModelDef("ark-glm-5-2", "glm-5-2-260617", "GLM-5.2 (方舟)", 1.11, 3.89, 1048576, "official"),
            StaticModelDef("ark-deepseek-v4-pro", "deepseek-v4-pro-260425", "DeepSeek V4 Pro (方舟)", 0.42, 0.83, 1048576, "official"),
            StaticModelDef("ark-deepseek-v4-flash", "deepseek-v4-flash-260425", "DeepSeek V4 Flash (方舟)", 0.14, 0.28, 1048576, "official"),
            StaticModelDef("ark-minimax-m3", "minimax-m3", "MiniMax M3 (方舟)", 0.29, 1.17, 524288, "official"),
            StaticModelDef("ark-kimi-k2-7-code", "kimi-k2-7-code", "Kimi K2.7 Code (方舟)", 0.9, 3.75, 262144, "official"),
            StaticModelDef("ark-code-latest", "ark-code-latest", "ARK Auto (方舟智能路由)", 2.0, 8.0, 131072, "default"),
        ),
    ),
    # ── 腾讯混元（无 /v1/models，静态清单）─────────────────────────────────────
    # 注意：4 个旧模型已从现行官方文档移除（迁移 TokenHub，停止新购）；价格为历史官方公告价
    # （元/1M，按 7.2 折算 USD）：TurboS 0.8/2、Turbo 15/50（2024-09 发布价）、Pro 30/100（2024-05 降价公告）、Lite 免费
    # 上下文：TurboS 最大输入 32K/输出 16K；Turbo 28K/4K；Pro 32K 长文/28K/4K；Lite 256K（官方公告/产品页）
    ProviderSpec(
        key="hunyuan", display_name="腾讯混元",
        default_base_url="https://api.hunyuan.cloud.tencent.com/v1",
        model_source="static",
        static_models=(
            StaticModelDef("hunyuan-turbos-latest", "hunyuan-turbos-latest", "混元 TurboS", 0.11, 0.28, 32768, "official"),
            StaticModelDef("hunyuan-turbo", "hunyuan-turbo", "混元 Turbo", 2.08, 6.94, 28672, "official"),
            StaticModelDef("hunyuan-pro", "hunyuan-pro", "混元 Pro", 4.17, 13.89, 32768, "official"),
            StaticModelDef("hunyuan-lite", "hunyuan-lite", "混元 Lite（免费）", 0.0, 0.0, 262144, "official"),
        ),
    ),
    # ── 阿里百炼 DashScope（无文档化 /models，静态清单）────────────────────────
    # 官方价（USD/1M tokens，华北2北京·中国内地节点，源自 alibabacloud.com/help/zh/model-studio/model-pricing）：
    # qwen-max 0.345/1.377、qwen-plus 0.115/0.287（≤128K 档）、qwen-turbo 0.044/0.087、
    # qwen3-8b 0.072/0.287、qwen3-32b 0.287/1.147（非思考模式；思考模式输出另计）
    # 上下文（源自 help.aliyun.com/zh/model-studio/text-generation-model）：qwen-max 32k、qwen-plus 1M、qwen-turbo 128k、qwen3-8b/32b 128k
    ProviderSpec(
        key="bailian", display_name="阿里百炼 DashScope",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_source="static",
        static_models=(
            StaticModelDef("qwen-max", "qwen-max", "通义千问 Max", 0.345, 1.377, 32768, "official"),
            StaticModelDef("qwen-plus", "qwen-plus", "通义千问 Plus", 0.115, 0.287, 1048576, "official"),
            StaticModelDef("qwen-turbo", "qwen-turbo", "通义千问 Turbo", 0.044, 0.087, 131072, "official"),
            StaticModelDef("qwen3-8b", "qwen3-8b", "Qwen3 8B", 0.072, 0.287, 131072, "official"),
            StaticModelDef("qwen3-32b", "qwen3-32b", "Qwen3 32B", 0.287, 1.147, 131072, "official"),
        ),
    ),
    # ── 月之暗面 Kimi（api 拉取，OpenAI 结构 + 能力字段）──────────────────────
    ProviderSpec(
        key="moonshot", display_name="月之暗面 Kimi",
        default_base_url="https://api.moonshot.cn/v1",
    ),
    # ── 智谱 GLM（无 /models 端点，静态清单，官方文档价）──────────────────────
    # 官方定价页 open.bigmodel.cn/pricing（元/1M，按 7.2 折算 USD）：
    # glm-4-flash 免费（128K）；glm-5.2 8/28 元（1M 上下文）；glm-5.1 6/24 元（<32K 档，200K 上下文）
    ProviderSpec(
        key="glm", display_name="智谱 GLM",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        model_source="static",
        static_models=(
            StaticModelDef("glm-4-flash", "glm-4-flash", "GLM-4 Flash（免费）", 0.0, 0.0, 131072, "official"),
            StaticModelDef("glm-5-2", "glm-5.2", "GLM-5.2", 1.11, 3.89, 1048576, "official"),
            StaticModelDef("glm-5-1", "glm-5.1", "GLM-5.1", 0.83, 3.33, 204800, "official"),
        ),
    ),
    # ── MiniMax（api 拉取，标准 OpenAI 结构）──────────────────────────────────
    ProviderSpec(
        key="minimax", display_name="MiniMax",
        default_base_url="https://api.minimaxi.com/v1",
    ),
    # ── OpenAI ChatGPT（api 拉取，过滤非 chat 模型）───────────────────────────
    ProviderSpec(
        key="openai", display_name="OpenAI ChatGPT",
        default_base_url="https://api.openai.com/v1",
        exclude_patterns=(
            "text-embedding", "whisper", "tts", "gpt-image", "dall-e",
            "moderation", "rerank", "audio", "realtime", "transcription",
            "speech", "embedding",
        ),
    ),
    # ── Anthropic Claude（api 拉取，专用解析 + after_id 分页）─────────────────
    ProviderSpec(
        key="anthropic", display_name="Anthropic Claude",
        default_base_url="https://api.anthropic.com",
        auth_type="x_api_key",
        adapter="anthropic",
        models_parser="anthropic",
    ),
    # ── xAI Grok（api 拉取，响应自带官方价格）─────────────────────────────────
    ProviderSpec(
        key="grok", display_name="Grok (xAI)",
        default_base_url="https://api.x.ai/v1",
        models_parser="grok",
        pricing_mode="official",
        exclude_patterns=("imagine", "voice", "image"),
    ),
    # ── Google Gemini（api 拉取，原生端点解析 + 兼容端点兜底）─────────────────
    ProviderSpec(
        key="gemini", display_name="Google Gemini",
        default_base_url="https://generativelanguage.googleapis.com",
        auth_type="api_key_query",
        adapter="gemini",
        models_parser="gemini",
        exclude_patterns=("embedding", "veo", "imagen", "music"),
    ),
]

PROVIDER_REGISTRY: dict[str, ProviderSpec] = {spec.key: spec for spec in _PROVIDER_SPECS}


def get_spec(name: str) -> Optional[ProviderSpec]:
    """按供应商 key 取注册表配置"""
    return PROVIDER_REGISTRY.get(name.lower())


def list_registry_entries() -> list[dict]:
    """注册表元数据（前端"添加供应商"下拉用），不含任何凭证"""
    return [
        {
            "key": spec.key,
            "display_name": spec.display_name,
            "default_base_url": spec.default_base_url,
            "auth_type": spec.auth_type,
            "model_source": spec.model_source,
            "static_model_count": len(spec.static_models),
        }
        for spec in _PROVIDER_SPECS
    ]


__all__ = [
    "DefaultPrices", "StaticModelDef", "ProviderSpec",
    "PROVIDER_REGISTRY", "get_spec", "list_registry_entries", "_excluded",
]
