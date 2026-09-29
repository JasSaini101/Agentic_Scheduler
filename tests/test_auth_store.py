"""
Multi-user auth & store tests — hermetic (SQLite in a tmpdir, no network).
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from scheduler import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SCHEDULER_DB", str(tmp_path / "test.db"))
    store.init()


@pytest.fixture()
def webapp():
    import web.app as webapp
    return webapp


def _login(webapp, client: TestClient, email: str) -> None:
    client.cookies.set(webapp._COOKIE_NAME, webapp._serializer().dumps(email))


# ── Store ──────────────────────────────────────────────────────────────────

def test_token_round_trip_and_isolation():
    store.save_token("a@x.com", '{"t": "A"}')
    store.save_token("b@x.com", '{"t": "B"}')
    assert store.load_token("a@x.com") == '{"t": "A"}'
    assert store.load_token("b@x.com") == '{"t": "B"}'
    # Second sign-in overwrites only that user's token
    store.save_token("a@x.com", '{"t": "A2"}')
    assert store.load_token("a@x.com") == '{"t": "A2"}'
    assert store.load_token("b@x.com") == '{"t": "B"}'
    assert store.load_token("nobody@x.com") is None


def test_session_round_trip():
    store.save_session("s1", "a@x.com", [{"day": "Monday"}], {"k": 1}, False, None)
    row = store.load_session("s1")
    assert row["user_email"] == "a@x.com"
    assert row["schedule"] == [{"day": "Monday"}]
    assert row["confirmed"] is False
    assert row["constraints"] is None
    store.save_session("s1", "a@x.com", [{"day": "Tuesday"}], {"k": 1}, True, {"c": 2})
    row = store.load_session("s1")
    assert row["schedule"] == [{"day": "Tuesday"}]
    assert row["confirmed"] is True
    assert row["constraints"] == {"c": 2}


# ── Cookie auth ────────────────────────────────────────────────────────────

def test_api_requires_cookie(webapp):
    client = TestClient(webapp.app)
    res = client.post("/api/schedule", json={"goals": "gym 3x"})
    assert res.status_code == 401


def test_forged_cookie_rejected(webapp):
    client = TestClient(webapp.app)
    client.cookies.set(webapp._COOKIE_NAME, "tampered.cookie.value")
    res = client.post("/api/schedule", json={"goals": "gym 3x"})
    assert res.status_code == 401


def test_auth_status_reports_email(webapp):
    client = TestClient(webapp.app)
    _login(webapp, client, "a@x.com")
    data = client.get("/api/auth-status").json()
    assert data["email"] == "a@x.com"
    assert data["authorized"] is False  # no Google token stored for this user


# ── Session ownership ──────────────────────────────────────────────────────

def test_cross_user_session_is_404(webapp):
    store.save_session(
        "sess-a", "a@x.com",
        [], {"activities": [], "fill_window": [8, 22],
             "calendar_id": "primary", "raw_goals": ""},
        False, None,
    )
    client = TestClient(webapp.app)
    _login(webapp, client, "b@x.com")
    assert client.get("/api/schedule/sess-a").status_code == 404
    assert client.post("/api/confirm", json={"session_id": "sess-a"}).status_code == 404
    # Owner can read it
    _login(webapp, client, "a@x.com")
    assert client.get("/api/schedule/sess-a").status_code == 200


# ── Current-calendar preload ───────────────────────────────────────────────

def test_calendar_requires_cookie(webapp):
    client = TestClient(webapp.app)
    assert client.get("/api/calendar").status_code == 401


def test_calendar_maps_events_to_hard_blocks(webapp, monkeypatch):
    import scheduler.data.providers.calendar_provider as provider
    monkeypatch.setattr(provider, "fetch_hard_events", lambda user=None, calendar_id=None: [
        {"day": "Monday", "start_hour": 9.0, "end_hour": 10.5,
         "label": "Standup", "type": "meeting"},
    ])
    client = TestClient(webapp.app)
    _login(webapp, client, "a@x.com")
    data = client.get("/api/calendar").json()
    assert data["synced"] is True
    assert data["schedule"] == [{
        "day": "Monday", "start_hour": 9.0, "end_hour": 10.5,
        "label": "Standup", "category": "hard_block", "reasons": [],
    }]
    assert data["week_start"] and "Monday" in data["day_dates"]


def test_calendar_unsynced_on_provider_failure(webapp, monkeypatch):
    import scheduler.data.providers.calendar_provider as provider
    def boom(user=None, calendar_id=None):
        raise RuntimeError("not_authorized")
    monkeypatch.setattr(provider, "fetch_hard_events", boom)
    client = TestClient(webapp.app)
    _login(webapp, client, "a@x.com")
    data = client.get("/api/calendar").json()
    assert data == {"schedule": [], "week_start": data["week_start"],
                    "day_dates": data["day_dates"], "synced": False}


# ── OAuth CSRF ─────────────────────────────────────────────────────────────

def test_oauth_start_sets_csrf_cookie(webapp, monkeypatch):
    """The redirect to Google must mirror the state's csrf half in a cookie."""
    monkeypatch.setattr(webapp, "credentials_config", lambda: {"web": {
        "client_id": "id", "client_secret": "secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }})
    client = TestClient(webapp.app)
    res = client.get("/auth/google", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert webapp._CSRF_COOKIE in res.cookies
    # the cookie value is the csrf half of the state in the Google URL
    from urllib.parse import urlparse, parse_qs
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    assert state.partition(".")[2] == res.cookies[webapp._CSRF_COOKIE]


def test_callback_rejects_mismatched_csrf(webapp, monkeypatch):
    """A callback from someone else's OAuth flow must not sign this browser in."""
    def explode():
        raise AssertionError("credentials_config must not be reached on CSRF failure")
    monkeypatch.setattr(webapp, "credentials_config", explode)

    client = TestClient(webapp.app)
    client.cookies.set(webapp._CSRF_COOKIE, "the-real-token")
    res = client.get("/auth/callback?code=stolen&state=verifier.attacker-token",
                     follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/?denied=1"
    assert store.load_token("a@x.com") is None


def test_callback_rejects_missing_csrf_cookie(webapp, monkeypatch):
    monkeypatch.setattr(webapp, "credentials_config",
                        lambda: (_ for _ in ()).throw(AssertionError("unreachable")))
    client = TestClient(webapp.app)
    res = client.get("/auth/callback?code=x&state=verifier.token",
                     follow_redirects=False)
    assert res.headers["location"] == "/?denied=1"


# ── Lazy init ──────────────────────────────────────────────────────────────

def test_ensure_init_is_idempotent_and_preserves_rows():
    store.save_token("a@x.com", "TOKEN")
    store.ensure_init()
    store.ensure_init()
    assert store.load_token("a@x.com") == "TOKEN"


def test_store_recovers_if_init_never_ran(monkeypatch):
    """A database down at startup must self-heal on first use."""
    monkeypatch.setattr(store, "_initialized", False)
    store.save_token("late@x.com", "TOKEN")
    assert store.load_token("late@x.com") == "TOKEN"


# ── Allowlist ──────────────────────────────────────────────────────────────

def test_allowlist(webapp, monkeypatch):
    monkeypatch.delenv("AUTHORIZED_EMAILS", raising=False)
    assert webapp._allowed("anyone@x.com")  # unset = open
    monkeypatch.setenv("AUTHORIZED_EMAILS", "a@x.com, B@X.com")
    assert webapp._allowed("a@x.com")
    assert webapp._allowed("b@x.com")      # case-insensitive
    assert not webapp._allowed("c@x.com")
