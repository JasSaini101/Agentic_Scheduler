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
from scheduler.data.providers.google_auth import SCOPES, credentials_source, save_token

app = FastAPI(title="Agentic Scheduler")

# In-memory session store: session_id → (schedule, user_config)
_sessions: dict[str, tuple[list[ScheduleEntry], UserConfig]] = {}


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
    try:
        creds_file = credentials_source()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        creds_file,
        scopes=SCOPES,
        redirect_uri=f"{_base_url()}/auth/callback",
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
def auth_callback(code: str, state: str = ""):
    """Exchange the authorization code for tokens and persist them."""
    try:
        creds_file = credentials_source()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        creds_file,
        scopes=SCOPES,
        redirect_uri=f"{_base_url()}/auth/callback",
    )
    flow.fetch_token(code=code)
    save_token(flow.credentials.to_json())

    return RedirectResponse("/?authorized=1")


@app.get("/api/auth-status")
def auth_status():
    return {"authorized": _is_authorized()}


@app.get("/api/token")
def get_token():
    """
    Return the current token JSON so the user can set it as GOOGLE_TOKEN_JSON
    on Render to survive restarts. Only useful after a successful OAuth flow.
    """
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
