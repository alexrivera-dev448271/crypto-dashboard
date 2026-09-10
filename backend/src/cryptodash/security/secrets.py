"""At-rest encryption for user-supplied secrets (Fernet).

User API keys are stored as ciphertext only; plaintext exists briefly in memory
during set/get. The Fernet key itself comes from the environment (MASTER_KEY)
and is never persisted alongside the data it protects — rotating MASTER_KEY in
.env re-encrypts on next write and old rows stay decryptable until rotated out.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretVault:
    def __init__(self, master_key: str) -> None:
        key = master_key.encode()
        self._f = Fernet(key) if isinstance(master_key, str) else Fernet(key)

    def encrypt(self, plaintext: str | bytes) -> bytes:
        data = plaintext.encode() if isinstance(plaintext, str) else plaintext
        return self._f.encrypt(data)

    def decrypt(self, ciphertext: bytes | bytearray | memoryview) -> str:
        try:
            return self._f.decrypt(bytes(ciphertext)).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("ciphertext does not match current MASTER_KEY (was it rotated?)") from exc
