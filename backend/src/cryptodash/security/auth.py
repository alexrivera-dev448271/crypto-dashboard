"""Server-side authentication and session management.

Design:
* Passwords hashed with Argon2id (memory/time tuned, per-hash parameters stored).
* Sessions are signed HMAC tokens (itsdangerous) carrying ``user_id`` + expiry —
  stateless but server-authoritative: the token is only valid while its
  signature checks and it hasn't expired. Cookies set HttpOnly, SameSite=Lax,
  Secure in production, Path=/api (plus / for logout).
* Login/register attempts are rate-limited per (user+ip) via ``rate_limit.py``.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import argon2
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, EmailStr

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_RE.match(value) or len(value) > 254:
        raise ValueError("invalid email address")
    return value


# ── password hashing (Argon2id) ────────────────────────────────────────────
_pwd_ctx = argon2.PasswordHasher(time_cost=3, memory_cost=19_456, parallelism=4, hash_len=32, salt_len=16)


def hash_password(plaintext: str) -> str:
    return _pwd_ctx.hash(plaintext)


def verify_password(stored_hash: str, plaintext: str) -> bool:
    try:
        return _pwd_ctx.verify(stored_hash, plaintext)
    except argon2.exceptions.VerifyMismatchError:
        return False
    except argon2.exceptions.ArgumentError:  # malformed stored hash
        return False


def check_password_strength(plaintext: str) -> None:
    if len(plaintext) < 10:
        raise ValueError("password must be at least 10 characters")
    if plaintext.isalnum() or not (any(c.isalpha() for c in plaintext) and any(c.isdigit() for c in plaintext)):
        raise ValueError("password needs letters AND digits")


# ── session tokens ──────────────────────────────────────────────────────────
SESSION_TTL_S = 60 * 60 * 24 * 14  # 14 days, re-issued on each login

_TOKEN_KEY = "cryptodash.session.v1"


class SessionService:
    def __init__(self, secret: str) -> None:
        self._ser = URLSafeTimedSerializer(secret, salt=_TOKEN_KEY)
        self.ttl_s = SESSION_TTL_S

    def issue(self, user_id: int) -> tuple[str, int]:
        token = self._ser.dumps({"uid": user_id})
        return token, self.ttl_s

    def resolve(self, token: str | None) -> int | None:
        """Return user id if the token is well-formed and unexpired; else None."""
        if not token:
            return None
        try:
            payload = self._ser.loads(token, max_age=self.ttl_s)
            uid = int(payload["uid"])
            return uid if uid > 0 else None
        except (BadSignature, KeyError, ValueError):
            return None


@dataclass(frozen=True)
class AuthPayload:
    user_id: int
    email: str
    display_name: str
