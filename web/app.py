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
from fastapi.responses import FileResponse
from pydantic import BaseModel

from scheduler.models import ScheduleEntry
from scheduler.config import UserConfig
from scheduler.llm.pipeline import pipeline
from scheduler.llm.adjustment_handler import apply as apply_adjustment

app = FastAPI(title="Agentic Scheduler")

# In-memory session store: session_id → (schedule, user_config)
_sessions: dict[str, tuple[list[ScheduleEntry], UserConfig]] = {}


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


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.post("/api/schedule", response_model=ScheduleResponse)
def build_schedule(req: ScheduleRequest):
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
