"""Create a temporary Shoonya OAuth session.

The access token is printed once for the current shell. Do not commit or
persist it in source files.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_engine.shoonya import ShoonyaBroker, ShoonyaConfig


def main() -> int:
    load_dotenv(ROOT / ".env")

    missing = [
        name
        for name in (
            "SHOONYA_USER_ID",
            "SHOONYA_CLIENT_ID",
            "SHOONYA_SECRET_CODE",
        )
        if not os.getenv(name)
    ]
    if missing:
        raise SystemExit(
            "Missing credentials in .env: " + ", ".join(missing)
            + "\nCopy .env.example to .env and fill those values."
        )

    config = ShoonyaConfig(
        user_id=os.environ["SHOONYA_USER_ID"],
        client_id=os.environ["SHOONYA_CLIENT_ID"],
        secret_code=os.environ["SHOONYA_SECRET_CODE"],
    )
    print(config.authorization_url())
    auth_code = input("Paste the short-lived authorization code: ").strip()
    token = ShoonyaBroker(config).exchange_access_token(auth_code)
    print("Access token obtained.")
    print("Set it in the current PowerShell session with:")
    print(f'$env:SHOONYA_ACCESS_TOKEN="{token}"')
    print("Keep SHOONYA_LIVE_TRADING unset or false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
