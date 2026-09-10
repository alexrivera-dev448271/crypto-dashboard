"""Security-layer unit tests.

Written against the *real* API surface (confirmed by reading source):
  auth:      hash_password / verify_password / check_password_strength,
             SessionService(secret) with issue(user_id)->(token,max_age), resolve(token)->int|None
  rate_limit:RateLimiter().check(key, limit, window_s=None)->bool  (module singleton `rate_limiter`)
  secrets:   SecretVault(master_key:str).encrypt(str)->bytes / .decrypt(bytes)->str

Run:  .venv/bin/pytest -q            (from project root)
"""
from __future__ import annotations

import pytest

# NOTE: import the module so we can patch its time reference for the expiry test.
from cryptography.fernet import Fernet

from cryptodash.security.auth import (
    SessionService,
    check_password_strength,
    hash_password,
    validate_email,
    verify_password,
)
from cryptodash.security.rate_limit import RateLimiter
from cryptodash.security.secrets import SecretVault


# ── passwords (Argon2id) ───────────────────────────────────────────────────
class TestPasswords:
    def test_hash_roundtrip(self):
        h = hash_password("correct-horse-battery")
        assert verify_password(h, "correct-horse-battery") is True
        assert verify_password(h, "wrong-password-123") is False

    def test_argon2id_used(self):
        h = hash_password("s3cret-value!x")
        assert h.startswith("$argon2id$"), f"expected argon2id hash, got {h[:12]!r}"

    @pytest.mark.parametrize("bad", ["short", "password", "a" * 9, "onlyletters"])
    def test_weak_rejected(self, bad):
        with pytest.raises(ValueError):
            check_password_strength(bad)

    def test_ok_accepted(self):
        # letters AND digits, >=10 chars
        check_password_strength("correct-horse-1")


# ── email validation ───────────────────────────────────────────────────────
class TestEmail:
    @pytest.mark.parametrize(
        "bad", ["no-at-sign", "@missing.local", "a b@c.com", "plain@", "a@" + ("x" * 300) + ".com"]
    )
    def test_invalid_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_email(bad)

    def test_valid_normalized(self):
        assert validate_email("  User@Example.COM ") == "user@example.com"


# ── session tokens (itsdangerous signed + expiring) ────────────────────────
class TestSessions:
    SECRET = "unit-test-session-signing-material-1234"

    def test_issue_resolve_roundtrip(self):
        s = SessionService(self.SECRET)
        token, max_age = s.issue(42)
        assert isinstance(max_age, int) and max_age > 0
        assert s.resolve(token) == 42

    def test_tampered_token_rejected(self):
        s = SessionService(self.SECRET)
        token, _ = s.issue(7)
        # flip the last base64url char to break the signature
        bad = token[:-1] + ("A" if not token.endswith("A") else "B")
        assert s.resolve(bad) is None

    def test_empty_or_none_rejected(self):
        s = SessionService(self.SECRET)
        assert s.resolve(None) is None
        assert s.resolve("") is None

    def test_wrong_secret_cannot_resolve(self):
        a, b = SessionService("secret-one-material-1234"), SessionService("secret-two-material-9876")
        token, _ = a.issue(5)
        assert b.resolve(token) is None  # different signing key

    def test_expired_token_rejected(self):
        import time as time_mod
        from cryptodash.security.auth import SESSION_TTL_S

        s = SessionService(self.SECRET)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(time_mod, "time", lambda: 1_000.0)
            token, _ = s.issue(9)
            assert s.resolve(token) == 9  # valid at issue time (sanity)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(time_mod, "time", lambda: 1_000.0 + SESSION_TTL_S + 5)
            assert s.resolve(token) is None

    def test_unexpired_token_still_valid(self):
        import time as time_mod
        from cryptodash.security.auth import SESSION_TTL_S

        s = SessionService(self.SECRET)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(time_mod, "time", lambda: 1_000.0)
            token, _ = s.issue(5)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(time_mod, "time", lambda: 1_000.0 + SESSION_TTL_S // 2)
            assert s.resolve(token) == 5


# ── at-rest secret vault (Fernet) ──────────────────────────────────────────
class TestVault:
    @staticmethod
    def _vault() -> SecretVault:
        return SecretVault(Fernet.generate_key().decode())

    def test_encrypt_decrypt_roundtrip(self):
        v = self._vault()
        ct = v.encrypt("my-fred-api-key-abc")
        assert isinstance(ct, (bytes, bytearray))
        # plaintext must not appear verbatim in the ciphertext
        assert b"fred" not in bytes(ct)
        assert v.decrypt(bytes(ct)) == "my-fred-api-key-abc"

    def test_ciphertext_differs_per_call(self):
        v = self._vault()
        a, b = v.encrypt("same"), v.encrypt("same")  # random IV per encryption
        assert a != b
        assert v.decrypt(bytes(a)) == v.decrypt(bytes(b)) == "same"

    def test_wrong_master_key_fails_to_decrypt(self):
        k1 = SecretVault(Fernet.generate_key().decode())
        k2 = SecretVault(Fernet.generate_key().decode())
        ct = k1.encrypt("top-secret-value")
        with pytest.raises(ValueError):  # rotated / wrong key -> ValueError
            k2.decrypt(bytes(ct))


# ── rate limiter (sliding window) ──────────────────────────────────────────
class TestRateLimiter:
    def test_allows_until_limit_then_blocks(self):
        rl = RateLimiter()
        results = [rl.check("bucket-a", 3, window_s=60) for _ in range(5)]
        assert all(results[:3]) is True
        assert all(r is False for r in results[3:])

    def test_separate_buckets_independent(self):
        rl = RateLimiter()
        assert rl.check("b1", 1, window_s=60) is True
        assert rl.check("b1", 1, window_s=60) is False
        assert rl.check("b2", 1, window_s=60) is True  # other bucket unaffected

    def test_window_expiry_releases_slots(self):
        import cryptodash.security.rate_limit as rl_mod

        rl = RateLimiter()
        now = [1_000.0]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(rl_mod.time, "monotonic", lambda: now[0])
            assert rl.check("k", 2, window_s=10) is True
            assert rl.check("k", 2, window_s=10) is True
            assert rl.check("k", 2, window_s=10) is False  # exhausted
            now[0] += 20.0  # jump past the window
            assert rl.check("k", 2, window_s=10) is True   # slot freed
