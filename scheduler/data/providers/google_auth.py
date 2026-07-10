"""
Shared Google OAuth helper.

Credential resolution order:
  1. credentials.json file (local dev)
  2. GOOGLE_CREDENTIALS_JSON env var (Render / hosted — paste the file contents)

Token resolution order:
  1. token.json file (local dev, or written after web OAuth flow)
  2. GOOGLE_TOKEN_JSON env var (Render — set after first successful auth)
  3. In-memory _token_store (survives within a single process lifetime)
"""

import json
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_TOKEN_PATH = Path("token.json")
_CREDENTIALS_PATH = Path("credentials.json")

# In-process token cache — populated after web OAuth flow completes.
# Survives restarts only if GOOGLE_TOKEN_JSON env var is also set.
_token_store: dict[str, str] = {}   # {"token": "<json string>"}


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


def _token_json() -> str | None:
    """Return the token JSON string from any available source, or None."""
    if _token_store.get("token"):
        return _token_store["token"]
    if _TOKEN_PATH.exists():
        return _TOKEN_PATH.read_text()
    return os.getenv("GOOGLE_TOKEN_JSON")


def save_token(token_json: str) -> None:
    """Persist a token JSON string to all available stores."""
    _token_store["token"] = token_json
    try:
        _TOKEN_PATH.write_text(token_json)
    except OSError:
        pass  # read-only filesystem (some hosted envs) — in-memory is enough


def get_credentials():
    """Return valid Google credentials or raise RuntimeError('not_authorized')."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    raw = _token_json()
    if not raw:
        raise RuntimeError("not_authorized")

    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                # Revoked/expired token or network failure — treat as signed out
                # so the UI shows "connect your calendar" instead of a raw 500.
                raise RuntimeError("not_authorized") from exc
            save_token(creds.to_json())
        else:
            raise RuntimeError("not_authorized")

    return creds


def get_service():
    """Return an authenticated Google Calendar service."""
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=get_credentials())
