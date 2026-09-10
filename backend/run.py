"""Server entrypoint.

Run:  python backend/run.py [--reload]
Serves API + built SPA (if present) on BACKEND_PORT with uvicorn workers=1 —
deliberate: the in-process rate limiter and embedded postgres assume a single
process; horizontal scale-out is a documented extension point, not an accident.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the backend source tree is importable when run as a script (python backend/run.py),
# independent of how/where it's launched. The src layout keeps `cryptodash` off the path until we add it.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn

from cryptodash.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="CryptoDash server")
    parser.add_argument("--reload", action="store_true", help="uvicorn auto-reload (dev only)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    host = args.host or ("127.0.0.1" if not settings.is_production else "0.0.0.0")
    port = args.port or settings.backend_port

    uvicorn.run(
        "cryptodash.app:create_app",
        factory=True,
        host=host,
        port=port,
        workers=1,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
