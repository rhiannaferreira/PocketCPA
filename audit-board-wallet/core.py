"""Shared plumbing: demo clock, SQLite ledger, board events, and the agent-side post_event()."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ======================================================================
# config: Shared settings. Everything reads time from demo_now() so the demo is deterministic.
# ======================================================================

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT
TEAM_DATA = ROOT.parent / "demo" / "data"   # the team's Maria Delgado sample data (single source of truth)
TZ = ZoneInfo(os.getenv("DEMO_TZ", "America/Los_Angeles"))

# Frozen demo clock: Jun 1, 2026, 6pm PT, after Maria's Feb-May sample months are complete.
DEFAULT_DEMO_NOW = "2026-06-01T18:00:00-07:00"


def db_path() -> str:
    return os.getenv("LEDGER_DB", str(ROOT / "ledger.db"))


def demo_now() -> datetime:
    """Frozen clock unless DEMO_NOW=live. Every agent should use this, never datetime.now()."""
    raw = os.getenv("DEMO_NOW", DEFAULT_DEMO_NOW)
    if raw.lower() == "live":
        return datetime.now(TZ)
    return datetime.fromisoformat(raw).astimezone(TZ)


# ======================================================================
# db:
# ======================================================================

def connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or db_path(), timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


@contextmanager
def tx(path: str | None = None):
    con = connect(path)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript((ROOT / "schema.sql").read_text())


def get_config(con, key, default=None):
    row = con.execute("SELECT v FROM config WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def set_config(con, key, value) -> None:
    con.execute("INSERT INTO config(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, str(value)))


# ======================================================================
# events: One way to put a line on the audit board: emit().
# ======================================================================

AGENTS = {"intake", "bookkeeper", "auditor", "benefits", "wallet", "crs", "system", "coordinator"}
SEVERITIES = {"info", "ok", "warn", "alert"}


def emit(agent: str, kind: str, summary: str, payload: dict | None = None,
         severity: str = "info", path: str | None = None) -> int:
    if severity not in SEVERITIES:
        severity = "info"
    with tx(path) as con:
        cur = con.execute(
            "INSERT INTO events(ts,agent,kind,severity,summary,payload) VALUES (?,?,?,?,?,?)",
            (demo_now().isoformat(), agent, kind, severity, summary,
             json.dumps(payload or {}, default=str)),
        )
        return cur.lastrowid


def max_event_id(path: str | None = None) -> int:
    try:
        with tx(path) as con:
            return con.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def rows_after(last_id: int, limit: int = 200, path: str | None = None) -> list[dict]:
    try:
        with tx(path) as con:
            rows = con.execute("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?",
                               (last_id, limit)).fetchall()
    except sqlite3.OperationalError:      # ledger missing or mid-reset
        return []
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"] or "{}")
        except json.JSONDecodeError:
            d["payload"] = {}
        out.append(d)
    return out


# ======================================================================
# client: Drop-in helper for agents that run in another process (uAgents, Agentverse, partner's code).
# ======================================================================

BOARD_URL = os.getenv("BOARD_URL", "http://127.0.0.1:8000")


def post_event(agent, kind, summary, payload=None, severity="info", timeout=2.0) -> bool:
    try:
        headers = {"X-Board-Token": os.environ["BOARD_TOKEN"]} if os.getenv("BOARD_TOKEN") else {}
        r = requests.post(f"{BOARD_URL}/events", timeout=timeout, headers=headers, json={
            "agent": agent, "kind": kind, "summary": summary,
            "payload": payload or {}, "severity": severity})
        return r.ok
    except requests.RequestException:
        return False
