#!/usr/bin/env python3
"""清空 Claude Code 的网关模型列表缓存（additionalModelOptionsCache）。

Claude Code 2.1.232 会把 gateway 模式发现的模型列表缓存进 ~/.claude.json，
切回非网关供应商（如 DeepSeek 直连）后不清理，导致 /model 仍显示网关模型残留。

用法：
    python scripts/clear_claude_model_cache.py

切到 Local Gateway 后启动 Claude Code 会自动重新拉取 20 个模型，无需担心清空。
注意：运行时若有 Claude Code 进程，退出时可能把内存缓存写回，建议先退出再清理。
"""

import json
from pathlib import Path


def main() -> None:
    p = Path.home() / ".claude.json"
    if not p.exists():
        print("未找到 ~/.claude.json")
        return

    data = json.loads(p.read_text(encoding="utf-8"))
    cache = data.get("additionalModelOptionsCache", [])
    n = len(cache)
    if n == 0:
        print("缓存已为空，无需清理")
        return

    data["additionalModelOptionsCache"] = []
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已清空 additionalModelOptionsCache（原 {n} 条网关模型）")


if __name__ == "__main__":
    main()
