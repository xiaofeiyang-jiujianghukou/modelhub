"""
同步 Codex 模型目录（~/.codex/model-catalog.local.json）

Codex 通过 model_catalog_json 指向该文件，启动时加载（不支持 URL/热更新）。
网关配置 CODEX_CATALOG_PATH 后会在模型变化时自动重写；本脚本用于手动触发
（或 CI/定时任务）。

用法:
    python scripts/sync_codex_catalog.py                # 同步默认路径（.env 配置或 ~/.codex/）
    python scripts/sync_codex_catalog.py --output xxx.json  # 输出到指定路径
    python scripts/sync_codex_catalog.py --dry-run      # 只显示结果不写入（服务函数无 dry-run，用临时输出路径代替）
"""

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.codex_catalog import configured_catalog_path, sync_codex_catalog  # noqa: E402


async def main() -> None:
    output: Path = None  # type: ignore
    args = [a for a in sys.argv[1:]]
    if "--output" in args:
        idx = args.index("--output")
        output = Path(args[idx + 1])
    elif "--dry-run" in args:
        output = Path("/tmp/model-catalog.dry-run.json")
    else:
        output = configured_catalog_path() or (Path.home() / ".codex" / "model-catalog.local.json")

    from src import database as db_module
    async with db_module.AsyncSessionLocal() as db:
        stats = await sync_codex_catalog(db, path=output)

    if stats.get("skipped"):
        print(f"⚠️ {stats['reason']}（.env 配置 CODEX_CATALOG_PATH 后自动同步）")
        return
    tag = "（dry-run 输出到 /tmp）" if ("--dry-run" in args) else ""
    print(f"✅ 已同步 {stats['total']} 个模型 → {stats['path']} {tag}")


if __name__ == "__main__":
    asyncio.run(main())
