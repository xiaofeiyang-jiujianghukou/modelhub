"""
凭证加密测试（AES-256-GCM）
"""

import base64
import json
import os

import pytest

from src.services.crypto import (
    CryptoConfigError,
    PLACEHOLDER_KEY,
    _PREFIX,
    decrypt_credentials,
    encrypt_credentials,
    encrypt_text,
    decrypt_text,
    generate_key,
    key_is_placeholder,
)


@pytest.fixture(autouse=True)
def _real_key(monkeypatch):
    """测试使用真实随机密钥，隔离 .env 占位值"""
    from src.config import settings
    monkeypatch.setattr(settings, "credentials_encryption_key", base64.b64encode(os.urandom(32)).decode())
    yield


def test_roundtrip():
    plain = {"api_key": "sk-abc123", "api_secret": "s3cr3t"}
    enc = encrypt_credentials(plain)
    assert enc.startswith(_PREFIX)
    assert decrypt_credentials(enc) == plain


def test_roundtrip_unicode():
    plain = {"api_key": "密钥·测试"}
    assert decrypt_credentials(encrypt_credentials(plain)) == plain


def test_tampered_ciphertext_raises():
    enc = encrypt_credentials({"api_key": "sk-x"})
    tampered = enc[:-4] + ("AAAA" if not enc.endswith("AAAA") else "BBBB")
    with pytest.raises(ValueError):
        decrypt_credentials(tampered)


def test_wrong_key_fails(monkeypatch):
    from src.config import settings
    enc = encrypt_credentials({"api_key": "sk-x"})
    # 换一把不同的密钥解密
    monkeypatch.setattr(settings, "credentials_encryption_key", base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(ValueError):
        decrypt_credentials(enc)


def test_legacy_plaintext_compat():
    """旧 MVP 格式：明文 JSON 字符串（含空凭证 '{}'）"""
    assert decrypt_credentials("{}") == {}
    assert decrypt_credentials(json.dumps({"api_key": "sk-legacy"})) == {"api_key": "sk-legacy"}
    assert decrypt_credentials("not-json") == {}
    assert decrypt_credentials("") == {}


def test_placeholder_key_rejected():
    from src.config import settings
    settings.credentials_encryption_key = PLACEHOLDER_KEY
    with pytest.raises(CryptoConfigError):
        encrypt_credentials({"api_key": "sk-x"})


def test_generate_key_is_32_bytes():
    key = generate_key()
    assert len(base64.b64decode(key)) == 32
    assert key_is_placeholder(key) is False
    assert key_is_placeholder("") is True
    assert key_is_placeholder(PLACEHOLDER_KEY) is True


def test_invalid_key_format_rejected(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "credentials_encryption_key", "not-base64!!!")
    with pytest.raises(CryptoConfigError):
        encrypt_credentials({"api_key": "sk-x"})

    monkeypatch.setattr(settings, "credentials_encryption_key", base64.b64encode(b"short").decode())
    with pytest.raises(CryptoConfigError):
        encrypt_credentials({"api_key": "sk-x"})


def test_text_helpers():
    enc = encrypt_text("hello")
    assert decrypt_text(enc) == "hello"
    # 非密文原样返回
    assert decrypt_text("plain") == "plain"
