"""
供应商凭证加解密（AES-256-GCM）

存储格式: gcm:v1:{nonce_b64}:{ct_b64}:{tag_b64}
兼容旧数据: 非 gcm:v1: 前缀的 payload 按 legacy 明文 JSON 解析（json.loads 兜底）。
"""

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config import settings

# .env 中默认的占位密钥（32 字节全零的 base64），必须替换后才允许加密写库
PLACEHOLDER_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_PREFIX = "gcm:v1:"


class CryptoConfigError(RuntimeError):
    """加密密钥缺失/占位/非法"""


def generate_key() -> str:
    """生成 32 字节随机密钥（base64 编码）"""
    return base64.b64encode(os.urandom(32)).decode()


def key_is_placeholder(key_b64: str) -> bool:
    return not key_b64 or key_b64 == PLACEHOLDER_KEY


def _load_key(key_b64: str | None = None) -> bytes:
    key_b64 = key_b64 or settings.credentials_encryption_key
    if key_is_placeholder(key_b64):
        raise CryptoConfigError(
            "CREDENTIALS_ENCRYPTION_KEY is missing or placeholder, "
            "run `python scripts/generate_encryption_key.py` first"
        )
    try:
        key = base64.b64decode(key_b64)
    except Exception:
        raise CryptoConfigError("CREDENTIALS_ENCRYPTION_KEY is not valid base64")
    if len(key) != 32:
        raise CryptoConfigError("CREDENTIALS_ENCRYPTION_KEY must decode to 32 bytes")
    return key


def encrypt_credentials(plain: dict) -> str:
    """加密凭证 dict，返回 gcm:v1: 前缀的密文字符串"""
    key = _load_key()
    nonce = os.urandom(12)
    ct_with_tag = AESGCM(key).encrypt(nonce, json.dumps(plain).encode(), None)
    ct, tag = ct_with_tag[:-16], ct_with_tag[-16:]
    return f"{_PREFIX}{base64.b64encode(nonce).decode()}:{base64.b64encode(ct).decode()}:{base64.b64encode(tag).decode()}"


def decrypt_credentials(payload: str) -> dict:
    """解密凭证；gcm:v1: 前缀走 AES-GCM，否则兼容 legacy 明文 JSON"""
    if not payload:
        return {}
    if payload.startswith(_PREFIX):
        try:
            nonce_b64, ct_b64, tag_b64 = payload[len(_PREFIX):].split(":")
            key = _load_key()
            nonce = base64.b64decode(nonce_b64)
            ct = base64.b64decode(ct_b64)
            tag = base64.b64decode(tag_b64)
            plaintext = AESGCM(key).decrypt(nonce, ct + tag, None)
            return json.loads(plaintext)
        except Exception as e:
            raise ValueError(f"credential decrypt failed: {e}") from e
    # legacy 明文 JSON（MVP 阶段存储格式，含 '{}' 空凭证）
    try:
        return json.loads(payload)
    except Exception:
        return {}


def encrypt_text(plaintext: str) -> str:
    """通用文本加密（备用）"""
    key = _load_key()
    nonce = os.urandom(12)
    ct_with_tag = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    ct, tag = ct_with_tag[:-16], ct_with_tag[-16:]
    return f"{_PREFIX}{base64.b64encode(nonce).decode()}:{base64.b64encode(ct).decode()}:{base64.b64encode(tag).decode()}"


def decrypt_text(payload: str) -> str:
    """通用文本解密（备用）"""
    if not payload.startswith(_PREFIX):
        return payload
    try:
        nonce_b64, ct_b64, tag_b64 = payload[len(_PREFIX):].split(":")
        key = _load_key()
        nonce = base64.b64decode(nonce_b64)
        ct = base64.b64decode(ct_b64)
        tag = base64.b64decode(tag_b64)
        return AESGCM(key).decrypt(nonce, ct + tag, None).decode()
    except Exception as e:
        raise ValueError(f"text decrypt failed: {e}") from e
