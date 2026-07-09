"""
Agentic Scheduler — FastAPI Web Backend
"""

import uuid
import sys
import os
import hashlib
import base64
import secrets as _secrets
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from scheduler.models import ScheduleEntry
from scheduler.config import UserConfig
from scheduler.llm.pipeline import pipeline
from scheduler.llm.adjustment_handler import apply as apply_adjustment
from scheduler.data.providers.google_auth import SCOPES, credentials_config, save_token

app = FastAPI(title="Agentic Scheduler")

# In-memory session store: session_id → (schedule, user_config)
_sessions: dict[str, tuple[list[ScheduleEntry], UserConfig]] = {}

def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = _secrets.token_urlsafe(96)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Helpers ────────────────────────────────────────────────────────────────

def _base_url() -> str:
    return os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")


def _is_authorized() -> bool:
    try:
        from scheduler.data.providers.google_auth import get_credentials
        get_credentials()
        return True
    except Exception:
        return False


# ── Request / Response models ──────────────────────────────────────────────

class ScheduleRequest(BaseModel):
    goals: str = Field(min_length=1, max_length=2000)
    calendar_id: str = Field(default="primary", max_length=256)


class ScheduleResponse(BaseModel):
    session_id: str
    schedule: list[ScheduleEntry]
    config: UserConfig
    calendar_synced: bool = True
    calendar_error: str | None = None


class AdjustRequest(BaseModel):
    session_id: str = Field(max_length=64)
    change: str = Field(min_length=1, max_length=1000)


class AdjustResponse(BaseModel):
    schedule: list[ScheduleEntry]


# ── Google OAuth routes ────────────────────────────────────────────────────

@app.get("/auth/google")
def auth_google():
    """Redirect the user to Google's OAuth consent screen."""
    try:
        client_config = credentials_config()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=f"{_base_url()}/auth/callback",
    )
    code_verifier, code_challenge = _pkce_pair()
    # Embed verifier in state so it survives the redirect without server storage.
    # token_urlsafe chars are [A-Za-z0-9_-] — no '.' — making '.' a safe separator.
    csrf = _secrets.token_urlsafe(16)
    composite_state = f"{code_verifier}.{csrf}"
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=composite_state,
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
def auth_callback(code: str, state: str = ""):
    """Exchange the authorization code for tokens and persist them."""
    # Extract verifier from composite state "<verifier>.<csrf>"
    code_verifier = state.split(".", 1)[0] if state and "." in state else None
    try:
        client_config = credentials_config()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=f"{_base_url()}/auth/callback",
    )
    flow.fetch_token(code=code, code_verifier=code_verifier)
    save_token(flow.credentials.to_json())

    return RedirectResponse("/?authorized=1")


@app.get("/api/auth-status")
def auth_status():
    return {"authorized": _is_authorized()}


@app.get("/api/token")
def get_token(key: str = ""):
    """
    One-time export of the token JSON (to set GOOGLE_TOKEN_JSON on Render).
    Disabled unless the TOKEN_EXPORT_KEY env var is set; caller must pass
    the matching ?key= value. Never leave TOKEN_EXPORT_KEY set permanently.
    """
    export_key = os.getenv("TOKEN_EXPORT_KEY")
    if not export_key or not _secrets.compare_digest(key, export_key):
        raise HTTPException(status_code=404, detail="Not found")

    from scheduler.data.providers.google_auth import _token_json
    raw = _token_json()
    if not raw:
        raise HTTPException(status_code=404, detail="No token found — authorize first.")
    return {"token_json": raw}


# ── Schedule endpoints ─────────────────────────────────────────────────────

@app.post("/api/schedule", response_model=ScheduleResponse)
def build_schedule(req: ScheduleRequest):
    if not _is_authorized():
        raise HTTPException(status_code=401, detail="not_authorized")

    try:
        schedule, user_config, calendar_error = pipeline.invoke(req.goals)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    session_id = str(uuid.uuid4())
    _sessions[session_id] = (schedule, user_config)

    return ScheduleResponse(
        session_id=session_id,
        schedule=schedule,
        config=user_config,
        calendar_synced=calendar_error is None,
        calendar_error=calendar_error,
    )


@app.post("/api/adjust", response_model=AdjustResponse)
def adjust_schedule(req: AdjustRequest):
    if req.session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    schedule, user_config = _sessions[req.session_id]

    try:
        updated = apply_adjustment(req.change, schedule, user_config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _sessions[req.session_id] = (updated, user_config)
    return AdjustResponse(schedule=updated)


@app.get("/api/schedule/{session_id}")
def get_schedule(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    schedule, config = _sessions[session_id]
    return {"schedule": schedule, "config": config}


# ── Static files & SPA fallback ───────────────────────────────────────────

_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.get("/")
def index():
    return FileResponse(_static / "index.html")
