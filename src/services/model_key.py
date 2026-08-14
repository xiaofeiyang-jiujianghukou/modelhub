"""
模型键格式统一（单一数据源）

对外唯一键（Claude Code / Codex / OpenAI SDK 一致）：
    '厂商key/模型名'        例：deepseek/deepseek-v4-pro
    '厂商key/模型名[1M]'    例：deepseek/deepseek-v4-flash[1M]（上下文窗口标记，网关剥离后路由）

向后兼容旧格式（已有配置/缓存）：
    '模型名@厂商key'        例：deepseek-v4-pro@deepseek

裸模型名（旧客户端兜底，策略选择）：
    '模型名'                例：glm-5.2

路由语义：唯一键 = 厂商 + 模型，精确确定一条路（不是测策略）。
"""

import re
from typing import Optional

# 上下文窗口后缀（[1M] / [1m] / [128K] 等），网关剥离后不影响路由键
_CONTEXT_SUFFIX_RE = re.compile(r"\[\d+[MK]\]$", flags=re.I)


def strip_context_suffix(name: str) -> str:
    """剥离上下文窗口后缀：deepseek-v4-flash[1M] → deepseek-v4-flash"""
    return _CONTEXT_SUFFIX_RE.sub("", name).strip()


def format_model_key(model_id: str, vendor: str) -> str:
    """对外唯一键：'厂商key/模型名'"""
    return f"{vendor}/{model_id}"


def parse_model_key(model_name: str) -> tuple[str, Optional[str]]:
    """
    解析模型键 → (模型名, 厂商key 或 None)
    - '厂商key/模型名' / '厂商key/模型名[1M]' → (模型名, 厂商key)   [新格式，厂商在前]
    - '模型名@厂商key'                          → (模型名, 厂商key)   [旧格式兼容，厂商在后]
    - 裸模型名                                   → (模型名, None)
    """
    name = strip_context_suffix(model_name)
    if "/" in name:
        vendor, mid = name.split("/", 1)
        return mid, vendor
    if "@" in name:
        mid, vendor = name.rsplit("@", 1)
        return mid, vendor
    return name, None
