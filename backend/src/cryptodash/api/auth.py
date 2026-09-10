"""Auth endpoints: register / login / logout / me."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator

from cryptodash.api.deps import SESSION_COOKIE, auth_attempts_limited, require_user
from cryptodash.api.errors import BadRequest, Conflict, Unauthorized
from cryptodash.config import get_settings
from cryptodash.db import db
from cryptodash.security.auth import check_password_strength, hash_password, verify_password

log = logging.getLogger("cryptodash.api.auth")

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=200)
    display_name: str | None = Field(default=None, max_length=60)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v


class LoginIn(BaseModel):
    email: str
    password: str


def _set_session_cookie(response: Response, token: str, max_age: int) -> None:
    s = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        value=token,
        max_age=max_age,
        httponly=True,                       # no JS access to the session token
        secure=s.is_production or s.force_https,   # HTTPS-only in prod
        samesite="lax",                      # CSRF protection against cross-site POSTs
        path="/api",                         # scoped: not sent to unrelated origins' paths
    )


@router.post("/register", status_code=201)
async def register(payload: RegisterIn, request: Request, response: Response,
                   _rl=Depends(auth_attempts_limited())) -> dict:
    check_password_strength(payload.password)

    async with db.admin() as conn:
        exists = await db.fetch_val(conn, "SELECT id FROM users WHERE email=%s", (payload.email.lower(),))
        if exists is not None:
            raise Conflict("an account with that email already exists")
        row = await db.fetch_one(
            conn,
            """INSERT INTO users(email, password_hash, display_name)
               VALUES (%s, %s, %s) RETURNING id, email""",
            (payload.email.lower(), hash_password(payload.password), payload.display_name or payload.email.split("@")[0]),
        )

    token, max_age = request.app.state.session.issue(int(row["id"]))
    _set_session_cookie(response, token, max_age)
    return {"email": row["email"], "display_name": payload.display_name or row["email"].split("@")[0]}


@router.post("/login")
async def login(payload: LoginIn, request: Request, response: Response,
                _rl=Depends(auth_attempts_limited())) -> dict:
    async with db.admin() as conn:
        row = await db.fetch_one(conn, "SELECT id, password_hash, display_name FROM users WHERE email=%s",
                                 (payload.email.strip().lower(),))
    if row is None or not verify_password(row["password_hash"], payload.password):
        raise Unauthorized("invalid credentials")

    token, max_age = request.app.state.session.issue(int(row["id"]))
    _set_session_cookie(response, token, max_age)
    return {"display_name": row["display_name"]}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    s = get_settings()
    response.delete_cookie(SESSION_COOKIE, path="/api", secure=s.is_production or s.force_https,
                           httponly=True, samesite="lax")
    return {"ok": True}


@router.get("/me")
async def me(user=Depends(require_user)) -> dict:
    return {"id": user["id"], "email": user["email"], "display_name": user["display_name"]}
