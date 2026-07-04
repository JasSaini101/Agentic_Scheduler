"""
Agentic Scheduler — FastAPI Web Backend
"""

import uuid
import sys
import os
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from scheduler.models import ScheduleEntry
from scheduler.config import UserConfig
from scheduler.llm.pipeline import pipeline
from scheduler.llm.adjustment_handler import apply as apply_adjustment
from scheduler.data.providers.google_auth import (
    credentials_path, token_path, SCOPES,
)

app = FastAPI(title="Agentic Scheduler")

# In-memory session store: session_id → (schedule, user_config)
_sessions: dict[str, tuple[list[ScheduleEntry], UserConfig]] = {}

# OAuth state store: state → None (used to validate callback)
_oauth_states: set[str] = set()


# ── Helpers ────────────────────────────────────────────────────────────────

def _base_url() -> str:
    return os.getenv("BASE_URL", "http://localhost:8000")


def _is_authorized() -> bool:
    """True if a valid token.json exists."""
    try:
        from scheduler.data.providers.google_auth import get_credentials
        get_credentials()
        return True
    except Exception:
        return False


# ── Request / Response models ──────────────────────────────────────────────

class ScheduleRequest(BaseModel):
    goals: str
    calendar_id: str = "primary"


class ScheduleResponse(BaseModel):
    session_id: str
    schedule: list[ScheduleEntry]
    config: UserConfig


class AdjustRequest(BaseModel):
    session_id: str
    change: str


class AdjustResponse(BaseModel):
    schedule: list[ScheduleEntry]


# ── Google OAuth routes ────────────────────────────────────────────────────

@app.get("/auth/google")
def auth_google():
    """Redirect the user to Google's OAuth consent screen."""
    creds_path = credentials_path()
    if not creds_path.exists():
        raise HTTPException(
            status_code=500,
            detail="credentials.json not found. Download it from Google Cloud Console and place it in the repo root.",
        )

    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        str(creds_path),
        scopes=SCOPES,
        redirect_uri=f"{_base_url()}/auth/callback",
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _oauth_states.add(state)
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
def auth_callback(code: str, state: str):
    """Exchange the authorization code for tokens and save token.json."""
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    _oauth_states.discard(state)

    creds_path = credentials_path()
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        str(creds_path),
        scopes=SCOPES,
        redirect_uri=f"{_base_url()}/auth/callback",
        state=state,
    )
    flow.fetch_token(code=code)
    token_path().write_text(flow.credentials.to_json())

    return RedirectResponse("/?authorized=1")


@app.get("/api/auth-status")
def auth_status():
    return {"authorized": _is_authorized()}


# ── Schedule endpoints ─────────────────────────────────────────────────────

@app.post("/api/schedule", response_model=ScheduleResponse)
def build_schedule(req: ScheduleRequest):
    if not _is_authorized():
        raise HTTPException(status_code=401, detail="not_authorized")

    try:
        schedule, user_config = pipeline.invoke(req.goals)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    session_id = str(uuid.uuid4())
    _sessions[session_id] = (schedule, user_config)

    return ScheduleResponse(
        session_id=session_id,
        schedule=schedule,
        config=user_config,
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
