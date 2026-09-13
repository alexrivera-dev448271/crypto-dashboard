"""Application configuration (pydantic-settings).

All tunables live here; everything secret comes from env / .env only —
never hardcoded. The data directory holds the embedded Postgres cluster and
caches and is gitignored.
"""
from __future__ import annotations

import hashlib
import os
import secrets as _secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # .../crypto dashboard


def _project_tag(root: Path) -> str:
    """Stable short identifier for a checkout so multiple projects coexist under one data root."""
    return hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:12]


def _user_data_root(tag: str) -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "cryptodash" / tag


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    public_host: str = "localhost"
    backend_port: int = 8400
    force_https: bool = False          # true when a TLS reverse proxy terminates HTTPS upstream
    session_secret: str = Field(min_length=24, description="itsdangerous signing key")
    master_key: str = Field(description="Fernet key for at-rest encryption of user secrets")

    fred_api_key: str | None = None    # optional: enables FRED M2/Brent series

    binance_base: str = "https://api.binance.com"
    yahoo_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126 Safari/537.36"
    )

    rl_auth_per_min: int = 5
    rl_api_per_min: int = 120
    rl_analysis_per_min: int = 40

    # Hard wall-clock budget for one /analysis/recommend call. Under a stalled
    # network (blackholed egress) the per-fetch timeouts may not all fire before
    # this trips; instead of hanging the request we cut analysis short and return
    # whatever partial result (cached series, engine scores) had completed.
    analysis_timeout_s: float = 45.0

    log_level: str = "INFO"

    # Optional override for the base data dir (embedded postgres cluster + cache). In
    # production this is the project root's `data/`; tests point it at a temp path via
    # env DATA_DIR_OVERRIDE so two apps can boot in one process without clashing.
    data_dir_override: Path | None = Field(default=None, description="override base data dir")

    # Optional override for the *exact* embedded-postgres cluster dir (pgdata + unix
    # socket). Production deployments should set PGDATA_DIR to a space-free absolute
    # path such as /var/lib/cryptodash/pgdata. Leave unset to auto-pick one.
    pgdata_dir_override: Path | None = Field(
        default=None, description="override exact embedded-postgres pgdata directory"
    )

    @field_validator("app_env")
    @classmethod
    def _valid_env(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"development", "production"}:
            raise ValueError("app_env must be development or production")
        return v

    # ── derived ───────────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def data_dir(self) -> Path:
        """Base dir for project-local artifacts (cache, logs). Gitignored."""
        p = self.data_dir_override or (PROJECT_ROOT / "data")
        p.mkdir(parents=True, exist_ok=True)
        (p / "postgres").mkdir(exist_ok=True)
        (p / "cache").mkdir(exist_ok=True)
        return p

    @property
    def pgdata_dir(self) -> Path:
        """The embedded-postgres cluster dir (pgdata + unix socket).

        The library forwards the socket dir to ``postgres -k <dir>`` via a
        whitespace-split control command, so *any path containing a space is
        unusable* — even though the directory itself exists. This checkout may
        live under such a prefix (e.g. ``~/Desktop/hermes projects/...``), so:

          1. an explicit ``PGDATA_DIR`` override always wins (production / CI);
          2. otherwise, if the effective data dir is space-free, keep the
             cluster in-project;
          3. otherwise relocate to a stable user-level root keyed by this
             checkout, e.g. ``~/.local/share/cryptodash/<12-char-hash>/pgdata``
             (respects ``XDG_DATA_HOME``).

        The path is deterministic per checkout and the directory exists on return.
        """
        if self.pgdata_dir_override:
            p = Path(self.pgdata_dir_override)
        elif not any(c.isspace() for c in str(self.data_dir)):
            p = self.data_dir / "postgres" / "pgdata"
        else:
            p = _user_data_root(_project_tag(PROJECT_ROOT)) / "pgdata"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def frontend_dist(self) -> Path | None:
        d = PROJECT_ROOT / "frontend" / "dist"
        return d if (d / "index.html").is_file() else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()  # type: ignore[call-arg]

    # Hard guardrails so a misconfigured deploy can't silently run insecure.
    if _secrets.compare_digest(s.session_secret, "change-me-please-generate-a-long-random-string"):
        raise RuntimeError(
            "SESSION_SECRET is still the placeholder. Generate one: "
            'python -c "import secrets;print(secrets.token_urlsafe(48))" and put it in .env'
        )
    if s.is_production and not (s.force_https or s.public_host.startswith("localhost")):
        # In production we refuse to serve without an explicit HTTPS posture.
        raise RuntimeError(
            "APP_ENV=production requires FORCE_HTTPS=true (TLS via Caddy/nginx) — refusing insecure start."
        )
    return s
