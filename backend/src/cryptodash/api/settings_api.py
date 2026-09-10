"""User settings: per-user encrypted secrets (e.g. FRED API key).

Write = upsert ciphertext; Read returns only a status flag + prefix, never the
plaintext (the value is decrypted server-side when analysis needs it).
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from cryptodash.api.deps import require_user
from cryptodash.config import get_settings
from cryptodash.db import db
from cryptodash.security.secrets import SecretVault

log = logging.getLogger("cryptodash.api.settings")

router = APIRouter()

FRED_KEY_RE = re.compile(r"^[a-z0-9]{32}$", re.IGNORECASE)


class FredKeyIn(BaseModel):
    api_key: str | None = Field(default=None, max_length=64)   # null → remove key


@router.post("/fred-key")
async def set_fred_key(payload: FredKeyIn, user=Depends(require_user)):
    s = get_settings()

    if payload.api_key is None:
        async with db.tenant(user["id"]) as conn:
            await conn.execute("DELETE FROM user_secrets WHERE kind='fred_api_key'")
        return {"has_fred_key": False}

    key = payload.api_key.strip()
    if not FRED_KEY_RE.match(key):
        from cryptodash.api.errors import BadRequest

        raise BadRequest("FRED keys are 32-character alphanumeric strings (see fred.stlouisfed.org/api)")

    vault = SecretVault(s.master_key)   # at-rest encryption: only ciphertext stored
    async with db.tenant(user["id"]) as conn:
        await conn.execute(
            """INSERT INTO user_secrets(owner_id, kind, ciphertext) VALUES (%s, 'fred_api_key', %s)
               ON CONFLICT (owner_id, kind) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, updated_at = now()""",
            (user["id"], vault.encrypt(key)),
        )

    # live-verify the key against FRED before telling the user it's good
    from cryptodash.data.http_client import make_client
    from cryptodash.data.providers import FredProvider

    async with make_client("cryptodash") as client:
        fred = FredProvider(client, key)
        s_m2 = await fred.series("M2SL", limit=5)
    if s_m2 is None or s_m2.empty:
        from cryptodash.api.errors import BadRequest

        raise BadRequest("FRED accepted the key format but rejected it when calling the API — check for a typo")
    return {"has_fred_key": True}


@router.get("/secrets-status")
async def secrets_status(user=Depends(require_user)):
    async with db.tenant(user["id"]) as conn:
        row = await db.fetch_one(conn, "SELECT kind FROM user_secrets WHERE kind='fred_api_key'")
    return {"has_fred_key": row is not None, "server_wide_key_configured": bool(get_settings().fred_api_key)}
