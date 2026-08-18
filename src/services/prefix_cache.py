"""
前缀缓存优化 Layer 1：前缀稳定化 + cache-key 注入
（设计见 docs/CACHE_OPTIMIZATION_DESIGN.md 第三、四节）

核心原则（Reasonix 方法论）：前缀从第 0 个 token 完全匹配才命中，
谁破坏前缀谁就全价。网关能安全做的两件事：
1. stabilize_payload：tools 按名字排序——相同集合 → 相同序列化字节 → 跨轮前缀一致
2. apply_provider_cache_optimizations：为支持 cache-key 的厂商注入会话级稳定 ID

system 内容与历史消息由客户端控制，网关不注入任何易变内容（时间戳/随机 ID），
保持 append-only——这本身就是"易变内容隔离"的实现。
"""

import hashlib
from typing import Any

from src.config import settings


def _tool_sort_key(tool: Any) -> str:
    """工具排序键：function.name（OpenAI 格式）；非标准结构退化为稳定空串"""
    if isinstance(tool, dict):
        fn = tool.get("function") or {}
        return str(fn.get("name") or tool.get("type") or "")
    return ""


def stabilize_payload(payload: dict[str, Any]) -> None:
    """前缀稳定化（原地）：tools 按 function.name 排序。

    - 确定性排序：客户端每轮发送的 tools 集合若相同，网关输出顺序恒一致，
      上游前缀缓存逐 token 对齐可命中（不排序时客户端顺序抖动会摧毁前缀）。
    - 语义等价：模型按名字引用工具，定义顺序不影响理解。
    - 可开关：settings.prefix_cache_stabilize（默认开）。
    """
    tools = payload.get("tools")
    if isinstance(tools, list) and len(tools) > 1:
        payload["tools"] = sorted(tools, key=_tool_sort_key)


def session_cache_key(payload: dict[str, Any]) -> str:
    """会话级稳定 cache key：system + 首条 user 消息的内容哈希。

    - 同一会话跨轮次：system 与首条 user 不变（append-only）→ key 稳定，
      厂商按 key 路由到同一缓存分片，命中率最大化。
    - 不同会话首条消息不同 → key 不同，互不干扰。
    """
    parts: list[str] = []
    for m in payload.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content") or ""
        if isinstance(content, list):
            content = "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
        if role == "system":
            parts.append(f"system:{content}")
        elif role == "user":
            parts.append(f"user:{content}")
            break  # 首条 user 即会话锚点，后续轮次追加的历史不参与
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def apply_provider_cache_optimizations(payload: dict[str, Any], vendor: str) -> None:
    """按厂商注入 prompt_cache_key（在 adapter 调用前执行）。

    支持厂商（docs/CACHE_OPTIMIZATION_DESIGN.md A 组厂商特例）：
    - hunyuan 混元：prompt_cache_key（官方建议，命中价 ≈ 1/3~1/4）
    - grok：prompt_cache_key（官方建议，命中 75% off）

    故障转移安全：先清理上一通道可能注入的 key，再按当前 vendor 决定注入，
    避免 A 厂商的 key 残留到 B 厂商请求。
    """
    payload.pop("prompt_cache_key", None)
    configured = {v.strip().lower() for v in settings.prefix_cache_key_vendors.split(",") if v.strip()}
    if vendor.lower() in configured:
        payload["prompt_cache_key"] = session_cache_key(payload)
