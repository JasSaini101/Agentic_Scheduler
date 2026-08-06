"""
Shared Google OAuth helper — multi-user.

Tokens are stored per user (keyed by Google account email) in the scheduler
store (SQLite locally, Postgres via DATABASE_URL when hosted). The OAuth
*client* config is single-tenant and resolves from:
  1. credentials.json file (local dev)
  2. GOOGLE_CREDENTIALS_JSON env var (Render / hosted — paste the file contents)

Legacy fallback: a user_email of None reads the old single-user sources
(token.json file or GOOGLE_TOKEN_JSON env var) so the CLI keeps working
for a solo local setup without signing in through the web flow.
"""

import json
import os
from pathlib import Path

from scheduler import store

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar",
]

_TOKEN_PATH = Path("token.json")
_CREDENTIALS_PATH = Path("credentials.json")


def credentials_config() -> dict:
    """Return the OAuth client config as a dict — never written to disk."""
    if _CREDENTIALS_PATH.exists():
        raw = _CREDENTIALS_PATH.read_text()
    else:
        raw = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not raw:
        raise RuntimeError(
            "Google credentials not found. Either place credentials.json in the "
            "repo root, or set the GOOGLE_CREDENTIALS_JSON environment variable."
        )
    return json.loads(raw)


def _token_json(user_email: str | None) -> str | None:
    """Return the token JSON string for this user, or None."""
    if user_email:
        return store.load_token(user_email)
    # Legacy single-user sources (local CLI without web sign-in)
    if _TOKEN_PATH.exists():
        return _TOKEN_PATH.read_text()
    return os.getenv("GOOGLE_TOKEN_JSON")


def save_token(user_email: str | None, token_json: str) -> None:
    """Persist a token JSON string for this user."""
    if user_email:
        store.save_token(user_email, token_json)
        return
    try:
        _TOKEN_PATH.write_text(token_json)
    except OSError:
        pass  # read-only filesystem — nothing to fall back to in legacy mode


def get_credentials(user_email: str | None = None):
    """Return valid Google credentials or raise RuntimeError('not_authorized')."""
    raw = _token_json(user_email)
    if not raw:
        raise RuntimeError("not_authorized")

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                # Revoked/expired token or network failure — treat as signed out
                # so the UI shows "connect your calendar" instead of a raw 500.
                raise RuntimeError("not_authorized") from exc
            save_token(user_email, creds.to_json())
        else:
            raise RuntimeError("not_authorized")

    return creds


def get_service(user_email: str | None = None):
    """Return an authenticated Google Calendar service for this user."""
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=get_credentials(user_email))
