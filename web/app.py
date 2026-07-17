"""
Agentic Scheduler — FastAPI Web Backend
"""

import uuid
import sys
import os
import json
import time
import hashlib
import base64
import logging
import tempfile
import secrets as _secrets
from collections import defaultdict, deque
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from scheduler.models import ScheduleEntry
from scheduler.config import UserConfig
from scheduler.llm.pipeline import pipeline
from scheduler.llm.adjustment_handler import apply as apply_adjustment
from scheduler.data.providers.google_auth import SCOPES, credentials_config, save_token

logger = logging.getLogger("agentic_scheduler")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Agentic Scheduler")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    # index.html uses inline <script>/<style>, so allow inline for self only
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'"
    )
    return response


# ── Rate limiting (per-IP sliding window on LLM-backed endpoints) ──────────

RATE_LIMIT_CALLS = 10          # max LLM requests…
RATE_LIMIT_WINDOW = 600        # …per 10 minutes per client
_rate_buckets: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    now = time.monotonic()
    bucket = _rate_buckets[_client_ip(request)]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_CALLS:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — please wait a few minutes and try again.",
        )
    bucket.append(now)


# ── Session store (write-through to disk so uvicorn/Render process restarts
#    within the same container don't lose active schedules) ─────────────────
# session_id → (schedule, user_config, confirmed)
# confirmed=False means the schedule is a proposal not yet written to Calendar.

_sessions: dict[str, tuple[list[ScheduleEntry], UserConfig, bool]] = {}
_SESSIONS_FILE = Path(tempfile.gettempdir()) / "agentic_scheduler_sessions.json"


def _persist_sessions() -> None:
    try:
        data = {
            sid: {
                "schedule": [e.model_dump() for e in schedule],
                "config": config.model_dump(),
                "confirmed": confirmed,
            }
            for sid, (schedule, config, confirmed) in _sessions.items()
        }
        _SESSIONS_FILE.write_text(json.dumps(data))
    except OSError as exc:
        logger.warning("Could not persist sessions: %s", exc)


def _load_sessions() -> None:
    if not _SESSIONS_FILE.exists():
        return
    try:
        data = json.loads(_SESSIONS_FILE.read_text())
        for sid, payload in data.items():
            schedule = [ScheduleEntry.model_validate(e) for e in payload["schedule"]]
            config = UserConfig.model_validate(payload["config"])
            _sessions[sid] = (schedule, config, payload.get("confirmed", True))
        logger.info("Restored %d session(s) from disk", len(_sessions))
    except Exception as exc:
        logger.warning("Could not restore sessions: %s", exc)


_load_sessions()


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
    week_start: str = ""      # ISO Monday date the schedule applies to
    day_dates: dict[str, str] = {}   # day name → ISO date it resolves to (rolling window)
    confirmed: bool = False   # proposal only — nothing written until /api/confirm
    calendar_synced: bool = True
    calendar_error: str | None = None


class AdjustRequest(BaseModel):
    session_id: str = Field(max_length=64)
    change: str = Field(min_length=1, max_length=1000)


class AdjustResponse(BaseModel):
    schedule: list[ScheduleEntry]
    confirmed: bool = False


class ConfirmRequest(BaseModel):
    session_id: str = Field(max_length=64)


class ConfirmResponse(BaseModel):
    confirmed: bool
    events_written: int


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
def build_schedule(req: ScheduleRequest, request: Request):
    if not _is_authorized():
        raise HTTPException(status_code=401, detail="not_authorized")
    _check_rate_limit(request)

    try:
        # Preview mode: build the proposal without touching Google Calendar.
        # Events are written only when the user confirms via /api/confirm.
        schedule, user_config, calendar_error = pipeline.invoke(
            {"goals": req.goals, "write_calendar": False}
        )
    except RuntimeError as exc:
        # Solver infeasibility — a crafted, user-facing explanation
        if "feasible" in str(exc).lower():
            raise HTTPException(status_code=422, detail=str(exc))
        logger.exception("Schedule build failed")
        raise HTTPException(
            status_code=500,
            detail="Could not build the schedule. Try rephrasing your goals.",
        )
    except Exception:
        logger.exception("Schedule build failed")
        raise HTTPException(
            status_code=500,
            detail="Could not build the schedule. Try rephrasing your goals.",
        )

    session_id = str(uuid.uuid4())
    _sessions[session_id] = (schedule, user_config, False)
    _persist_sessions()

    from scheduler.data.mock_week import DAYS, current_week_start, date_for_day
    return ScheduleResponse(
        session_id=session_id,
        schedule=schedule,
        config=user_config,
        week_start=current_week_start().isoformat(),
        day_dates={d: date_for_day(d).isoformat() for d in DAYS},
        confirmed=False,
        calendar_synced=calendar_error is None,
        calendar_error=calendar_error,
    )


@app.post("/api/confirm", response_model=ConfirmResponse)
def confirm_schedule(req: ConfirmRequest):
    if req.session_id not in _sessions:
        raise HTTPException(
            status_code=404,
            detail="Session expired — please rebuild your schedule.",
        )

    schedule, user_config, _ = _sessions[req.session_id]

    try:
        from scheduler.data.providers.calendar_writer import write_schedule
        write_schedule(schedule, user_config.calendar_id)
    except Exception:
        logger.exception("Calendar write failed on confirm")
        raise HTTPException(
            status_code=502,
            detail="Could not write to Google Calendar. Check your connection and try again.",
        )

    _sessions[req.session_id] = (schedule, user_config, True)
    _persist_sessions()

    written = sum(1 for e in schedule if e.category != "hard_block")
    return ConfirmResponse(confirmed=True, events_written=written)


@app.post("/api/adjust", response_model=AdjustResponse)
def adjust_schedule(req: AdjustRequest, request: Request):
    if req.session_id not in _sessions:
        raise HTTPException(
            status_code=404,
            detail="Session expired — please rebuild your schedule.",
        )
    _check_rate_limit(request)

    schedule, user_config, confirmed = _sessions[req.session_id]

    try:
        # Before confirmation nothing is on the calendar, so edit in memory only;
        # after confirmation keep Google Calendar in sync with each change.
        updated = apply_adjustment(req.change, schedule, user_config, sync_calendar=confirmed)
    except Exception:
        logger.exception("Adjustment failed")
        raise HTTPException(
            status_code=500,
            detail="Could not apply that change. Try rephrasing it.",
        )

    _sessions[req.session_id] = (updated, user_config, confirmed)
    _persist_sessions()
    return AdjustResponse(schedule=updated, confirmed=confirmed)


@app.get("/api/schedule/{session_id}")
def get_schedule(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    schedule, config, confirmed = _sessions[session_id]
    return {"schedule": schedule, "config": config, "confirmed": confirmed}


# ── Static files & SPA fallback ───────────────────────────────────────────

_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.get("/")
def index():
    return FileResponse(_static / "index.html")
