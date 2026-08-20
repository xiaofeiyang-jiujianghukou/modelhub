"""
供应商注册表：11 家供应商元数据配置（单一数据源）

- 每供应商一条 ProviderSpec
- model_source='api'：添加 Key 后自动 GET /models 拉取（DeepSeek/Moonshot/MiniMax/OpenAI/Claude/Grok/Gemini/方舟）
- model_source='static'：无官方 /models 端点，使用内置清单（混元/百炼/智谱）
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
    model_id_map: Tuple[Tuple[str, str], ...] = ()  # (上游模型ID, 网关统一ID) 同名模型归一化（如百炼 glm-5.2 → glm-5-2）
    keep_latest_only: bool = False  # 同厂商同类型只保留最新版本，同步时移除旧版（方舟多版本日期后缀模型用）


def _excluded(model_id: str, patterns: Tuple[str, ...]) -> bool:
    """按 ID 前缀/包含过滤非 chat 模型（embedding/tts/image 等）"""
    if not patterns:
        return False
    lower = model_id.lower()
    return any(p in lower for p in patterns)


# ── 注册表定义 ────────────────────────────────────────────────────────────────

_PROVIDER_SPECS = [
    # ── DeepSeek（api 拉取，标准 OpenAI 结构）────────────────────────────────
    # 价格/上下文兜底值见 config/model_reference.json → model_references 表
    ProviderSpec(
        key="deepseek", display_name="DeepSeek",
        default_base_url="https://api.deepseek.com/v1",
    ),
    # ── 火山方舟 Coding Plan（/api/coding/v1/models 可用，api 拉取）──
    # 方舟 /models 返回全量（含 Shutdown/Retiring 退役 + embedding/3D 等），
    # 由 _fetch_ark parser 过滤退役模型 + 排除网关不支持的 embedding/3D/router，
    # 并按 id 识别 model_type（seedream→image / seedance→video / 其余→llm）
    ProviderSpec(
        key="ark", display_name="方舟",
        default_base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        model_source="api",
        models_parser="ark",
        exclude_patterns=("embedding", "hyper3d", "hitem3d", "seed3d", "smart-router", "seed-translation", "qwen"),
        keep_latest_only=True,
    ),
    # ── 腾讯混元（无 /v1/models，静态清单）─────────────────────────────────────
    # 注意：4 个旧模型已从现行官方文档移除（迁移 TokenHub，停止新购）；价格为历史官方公告价
    # （元/1M，按 7.2 折算 USD）：TurboS 0.8/2、Turbo 15/50（2024-09 发布价）、Pro 30/100（2024-05 降价公告）、Lite 免费
    # 上下文：TurboS 最大输入 32K/输出 16K；Turbo 28K/4K；Pro 32K 长文/28K/4K；Lite 256K（官方公告/产品页）
    # 暂时不接混元（不划算）——static 清单清空，后续接入时再补模型
    ProviderSpec(
        key="hunyuan", display_name="混元",
        default_base_url="https://api.hunyuan.cloud.tencent.com/v1",
        model_source="static",
        static_models=(),
    ),
    # ── 阿里百炼 Token Plan（套餐端点，GET /models 可用，api 拉取）────────────────
    # token-plan.cn-beijing.maas.aliyuncs.com 为 Token Plan 订阅套餐专用端点（Key 为 sk-sp- 前缀）
    # 模型清单由套餐决定（qwen3.x-max/plus/flash、glm-5.2、deepseek-v4-* 等），
    # 套餐按订阅计费无按量单价 → 拉取后标 default 价
    # 过滤：wan2.7 图像、qwen-audio 音频（非文本 LLM）
    ProviderSpec(
        key="bailian", display_name="百炼",
        default_base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        model_source="api",
        exclude_patterns=("wan2.7", "qwen-audio", "audio"),
        # 百炼镜像同名模型归一化到主模型（统一 ID + 百炼通道）
        model_id_map=(
            ("deepseek-v4-flash-0731", "deepseek-v4-flash"),
        ),
    ),
    # ── 月之暗面 Kimi（api 拉取，OpenAI 结构 + 能力字段）──────────────────────
    # 过滤：moonshot-v1-* 老系列（已被 kimi-k2.x/k3 取代）、kimi-k2.5/k2.6 旧版
    ProviderSpec(
        key="moonshot", display_name="月之暗面",
        default_base_url="https://api.moonshot.cn/v1",
        exclude_patterns=("moonshot-v1-", "kimi-k2.5", "kimi-k2.6"),
    ),
    # ── 智谱 GLM（无 /models 端点，静态清单）──────────────────────
    # 模型清单 + 价格 + 上下文见 config/model_reference.json → model_references 表
    # base_url 用 Coding Plan 套餐端点 /api/coding/paas/v4（按量端点是 /api/paas/v4，
    # 用错会导致 Coding Plan 账号报"余额不足或无可用资源包"——套餐资源包只在 coding 端点可见）
    ProviderSpec(
        key="glm", display_name="智谱",
        default_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        model_source="static",
    ),
    # ── MiniMax（api 拉取，标准 OpenAI 结构）──────────────────────────────────
    ProviderSpec(
        key="minimax", display_name="MiniMax",
        default_base_url="https://api.minimaxi.com/v1",
    ),
    # ── OpenAI ChatGPT（api 拉取，过滤非 chat 模型）───────────────────────────
    ProviderSpec(
        key="openai", display_name="OpenAI",
        default_base_url="https://api.openai.com/v1",
        exclude_patterns=(
            "text-embedding", "whisper", "tts", "gpt-image", "dall-e",
            "moderation", "rerank", "audio", "realtime", "transcription",
            "speech", "embedding",
        ),
    ),
    # ── Anthropic Claude（api 拉取，专用解析 + after_id 分页）─────────────────
    ProviderSpec(
        key="anthropic", display_name="Anthropic",
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
