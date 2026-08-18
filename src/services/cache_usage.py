"""
上游 usage 缓存字段归一化（缓存优化 Layer 3：命中率监控）

各家前缀缓存的命中字段不同，统一解析为 (cache_hit_tokens, cache_miss_tokens)：
- DeepSeek:   usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens
- Anthropic:  usage.cache_read_input_tokens（命中）/ cache_creation_input_tokens（写缓存）
- OpenAI 系:  usage.prompt_tokens_details.cached_tokens（OpenAI/GLM/Kimi/Grok/混元/方舟兼容端点）
- MiniMax:    usage.cached_tokens / cache_creation_input_tokens
- 兜底:       miss 未显式给出时按 prompt_tokens - hit 推算

字段来源调研见 docs/CACHE_OPTIMIZATION_DESIGN.md 第一节表格。
"""

from typing import Optional


def extract_cache_usage(usage: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
    """解析上游 usage → (cache_hit_tokens, cache_miss_tokens)

    无缓存数据（上游未返回任何缓存字段）返回 (None, None)，区分"未上报"与"0 命中"。
    """
    if not isinstance(usage, dict):
        return None, None

    prompt = usage.get("prompt_tokens")

    def _miss(hit: int) -> Optional[int]:
        # 上游未显式给 miss 时按 prompt_tokens - hit 推算（prompt 也缺失则不推）
        if isinstance(prompt, int):
            return max(0, prompt - hit)
        return None

    # 1. DeepSeek：显式 hit/miss 对
    hit = usage.get("prompt_cache_hit_tokens")
    if isinstance(hit, int):
        miss = usage.get("prompt_cache_miss_tokens")
        return hit, miss if isinstance(miss, int) else _miss(hit)

    # 2. Anthropic 风格：cache_read=命中，cache_creation=本次写缓存（不计入命中）
    hit = usage.get("cache_read_input_tokens")
    if isinstance(hit, int):
        return hit, _miss(hit)

    # 3. OpenAI 风格：prompt_tokens_details.cached_tokens
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("cached_tokens"), int):
        hit = details["cached_tokens"]
        if hit > 0:
            return hit, _miss(hit)

    # 4. MiniMax 等顶层 cached_tokens
    hit = usage.get("cached_tokens")
    if isinstance(hit, int) and hit > 0:
        return hit, _miss(hit)

    return None, None


def cache_hit_ratio(hit: Optional[int], miss: Optional[int]) -> Optional[float]:
    """命中率 = hit / (hit + miss)；数据不全返回 None"""
    if not isinstance(hit, int) or not isinstance(miss, int):
        return None
    total = hit + miss
    if total <= 0:
        return None
    return round(hit / total, 4)
