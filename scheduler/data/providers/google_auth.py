"""
Shared Google OAuth helper.
Reads token.json if it exists and is valid; raises RuntimeError otherwise.
The web OAuth flow (web/app.py) is responsible for creating token.json.
"""

from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_TOKEN_PATH = Path("token.json")
_CREDENTIALS_PATH = Path("credentials.json")


def get_credentials():
    """Return valid Google credentials or raise RuntimeError."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not _TOKEN_PATH.exists():
        raise RuntimeError("not_authorized")

    creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _TOKEN_PATH.write_text(creds.to_json())
        else:
            raise RuntimeError("not_authorized")

    return creds


def get_service():
    """Return an authenticated Google Calendar service."""
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=get_credentials())


def credentials_path() -> Path:
    return _CREDENTIALS_PATH


def token_path() -> Path:
    return _TOKEN_PATH
