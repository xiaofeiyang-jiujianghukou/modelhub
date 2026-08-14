"""
生成 CREDENTIALS_ENCRYPTION_KEY 并写入 .env（缺失或为占位值时）

用法:
    python scripts/generate_encryption_key.py

说明:
- 幂等：已有真实密钥时跳过
- .env 不入库，密钥只在本地环境
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import get_key, set_key  # noqa: E402

from src.services.crypto import generate_key, key_is_placeholder  # noqa: E402

ENV_FILE = PROJECT_ROOT / ".env"


def ensure_encryption_key() -> str:
    """确保 .env 中存在真实加密密钥，返回当前密钥"""
    current = get_key(str(ENV_FILE), "CREDENTIALS_ENCRYPTION_KEY")
    if current and not key_is_placeholder(current):
        return current
    new_key = generate_key()
    set_key(str(ENV_FILE), "CREDENTIALS_ENCRYPTION_KEY", new_key)
    return new_key


if __name__ == "__main__":
    key = ensure_encryption_key()
    print(f"✅ CREDENTIALS_ENCRYPTION_KEY 已就绪（{len(key)} chars，勿提交 .env）")
