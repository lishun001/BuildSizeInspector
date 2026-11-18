"""密码用 Argon2id；令牌和会话只持久化带域隔离的 HMAC。"""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError

PASSWORDS = PasswordHasher(type=Type.ID)


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("密码长度必须为 12–256 字符")
    return PASSWORDS.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return PASSWORDS.verify(encoded, password)
    except (VerificationError, ValueError):
        return False


def opaque_token() -> str:
    return secrets.token_urlsafe(32)


def digest(secret: str, domain: str, value: str) -> str:
    return hmac.new(secret.encode(), f"{domain}:{value}".encode(), hashlib.sha256).hexdigest()
