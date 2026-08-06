"""
Agentic Scheduler — FastAPI Web Backend
"""

import uuid
import sys
import os
import time
import hashlib
import base64
import logging
import secrets as _secrets
from collections import defaultdict, deque
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel, Field

from scheduler import store
from scheduler.models import ScheduleEntry
from scheduler.config import UserConfig
from scheduler.llm.pipeline import pipeline
from scheduler.graph.adjust import apply_change
from scheduler.graph.build import _default_llm
from scheduler.solver import ConstraintSet, InfeasibleError
from scheduler.data.providers.google_auth import SCOPES, credentials_config, save_token

logger = logging.getLogger("agentic_scheduler")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Agentic Scheduler")
store.init()


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


# ── User identity (signed cookie) ──────────────────────────────────────────
# The Google OAuth flow establishes who the user is; a signed HttpOnly cookie
# carries their email between requests. Tokens and schedule sessions live in
# the scheduler store, keyed by that email — never shared between users.

_COOKIE_NAME = "scheduler_user"
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days


def _serializer() -> URLSafeTimedSerializer:
    secret = os.getenv("SESSION_SECRET")
    if not secret:
        # Local dev convenience: derive a per-process secret. Cookies won't
        # survive a restart — set SESSION_SECRET for anything real.
        secret = globals().setdefault("_dev_secret", _secrets.token_urlsafe(32))
    return URLSafeTimedSerializer(secret, salt="scheduler-user")


def current_user(request: Request) -> str | None:
    """Email from the signed cookie, or None if absent/invalid/expired."""
    raw = request.cookies.get(_COOKIE_NAME)
    if not raw:
        return None
    try:
        return _serializer().loads(raw, max_age=_COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="not_authorized")
    return user


def _set_user_cookie(response: Response, email: str) -> None:
    response.set_cookie(
        _COOKIE_NAME,
        _serializer().dumps(email),
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=_base_url().startswith("https"),
        samesite="lax",
    )


def _allowed(email: str) -> bool:
    """AUTHORIZED_EMAILS allowlist (comma-separated). Unset = open."""
    raw = os.getenv("AUTHORIZED_EMAILS", "").strip()
    if not raw:
        return True
    allowed = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return email.lower() in allowed


# ── Schedule sessions (store-backed) ───────────────────────────────────────
# confirmed=False means the schedule is a proposal not yet written to Calendar.
# constraints is the solver ConstraintSet — change requests patch it and re-solve.

def _save_session(session_id: str, user_email: str, schedule: list[ScheduleEntry],
                  config: UserConfig, confirmed: bool,
                  constraints: ConstraintSet | None) -> None:
    store.save_session(
        session_id, user_email,
        [e.model_dump() for e in schedule],
        config.model_dump(),
        confirmed,
        constraints.model_dump() if constraints else None,
    )


def _load_session(session_id: str, user_email: str):
    """Return (schedule, config, confirmed, constraints) or None.

    Sessions are private: a session belonging to another user is treated
    as nonexistent rather than revealing that the id is in use."""
    row = store.load_session(session_id)
    if row is None or row["user_email"] != user_email:
        return None
    schedule = [ScheduleEntry.model_validate(e) for e in row["schedule"]]
    config = UserConfig.model_validate(row["config"])
    constraints = (ConstraintSet.model_validate(row["constraints"])
                   if row["constraints"] else None)
    return schedule, config, row["confirmed"], constraints


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = _secrets.token_urlsafe(96)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Helpers ────────────────────────────────────────────────────────────────

def _base_url() -> str:
    return os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")


def _is_authorized(user_email: str | None) -> bool:
    """True if this user has a valid (or refreshable) Google token."""
    if not user_email:
        return False
    try:
        from scheduler.data.providers.google_auth import get_credentials
        get_credentials(user_email)
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
    """Exchange the code for tokens, identify the user, persist per-user."""
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

    # Who just signed in? (email scope is part of SCOPES)
    from google.auth.transport.requests import AuthorizedSession
    userinfo = AuthorizedSession(flow.credentials).get(
        "https://www.googleapis.com/oauth2/v2/userinfo"
    ).json()
    email = (userinfo.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=502, detail="Google did not return an email.")

    if not _allowed(email):
        logger.info("Sign-in rejected (not on allowlist): %s", email)
        return RedirectResponse("/?denied=1")

    save_token(email, flow.credentials.to_json())

    response = RedirectResponse("/?authorized=1")
    _set_user_cookie(response, email)
    return response


@app.get("/api/auth-status")
def auth_status(request: Request):
    user = current_user(request)
    return {"authorized": _is_authorized(user), "email": user}


# ── Schedule endpoints ─────────────────────────────────────────────────────

@app.post("/api/schedule", response_model=ScheduleResponse)
def build_schedule(req: ScheduleRequest, request: Request):
    user = _require_user(request)
    if not _is_authorized(user):
        raise HTTPException(status_code=401, detail="not_authorized")
    _check_rate_limit(request)

    try:
        # Preview mode: build the proposal without touching Google Calendar.
        # Events are written only when the user confirms via /api/confirm.
        schedule, user_config, calendar_error, constraints = pipeline.invoke(
            {"goals": req.goals, "write_calendar": False, "user_email": user}
        )
    except ValueError as exc:
        # Goal parsing rejected the input (e.g. a change request typed into
        # the goals box) — the message is crafted to guide the user.
        raise HTTPException(status_code=422, detail=str(exc))
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
    _save_session(session_id, user, schedule, user_config, False, constraints)

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
def confirm_schedule(req: ConfirmRequest, request: Request):
    user = _require_user(request)
    row = _load_session(req.session_id, user)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Session expired — please rebuild your schedule.",
        )
    schedule, user_config, _, constraints = row

    try:
        from scheduler.data.providers.calendar_writer import write_schedule
        write_schedule(schedule, user_config.calendar_id, user_email=user)
    except Exception:
        logger.exception("Calendar write failed on confirm")
        raise HTTPException(
            status_code=502,
            detail="Could not write to Google Calendar. Check your connection and try again.",
        )

    _save_session(req.session_id, user, schedule, user_config, True, constraints)

    written = sum(1 for e in schedule if e.category != "hard_block")
    return ConfirmResponse(confirmed=True, events_written=written)


@app.post("/api/adjust", response_model=AdjustResponse)
def adjust_schedule(req: AdjustRequest, request: Request):
    user = _require_user(request)
    row = _load_session(req.session_id, user)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Session expired — please rebuild your schedule.",
        )
    _check_rate_limit(request)

    schedule, user_config, confirmed, constraints = row
    if constraints is None:
        # Session predates the constraint engine — nothing to patch against.
        raise HTTPException(
            status_code=404,
            detail="Session expired — please rebuild your schedule.",
        )

    try:
        # Change request → ConstraintPatch → CP-SAT re-solve of the whole week.
        updated, new_constraints, unplaced = apply_change(
            req.change, schedule, constraints, llm=_default_llm())
    except InfeasibleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logger.exception("Adjustment failed")
        raise HTTPException(
            status_code=500,
            detail="Could not apply that change. Try rephrasing it.",
        )
    for shortfall in unplaced:
        logger.info("[adjust] could not fit: %s", shortfall)

    if confirmed:
        # Already on the calendar — keep Google Calendar in sync with the change.
        try:
            from scheduler.data.providers.calendar_writer import write_schedule
            write_schedule(updated, user_config.calendar_id, user_email=user)
        except Exception:
            logger.exception("Calendar sync failed after adjustment")

    _save_session(req.session_id, user, updated, user_config, confirmed, new_constraints)
    return AdjustResponse(schedule=updated, confirmed=confirmed)


@app.get("/api/schedule/{session_id}")
def get_schedule(session_id: str, request: Request):
    user = _require_user(request)
    row = _load_session(session_id, user)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    schedule, config, confirmed, _constraints = row
    return {"schedule": schedule, "config": config, "confirmed": confirmed}


# ── Static files & SPA fallback ───────────────────────────────────────────

_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.get("/")
def index():
    # no-cache: browsers must revalidate the app shell on every load, so UI
    # changes appear on a normal reload instead of requiring a hard refresh
    return FileResponse(_static / "index.html",
                        headers={"Cache-Control": "no-cache"})
