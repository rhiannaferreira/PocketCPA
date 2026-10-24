"""Audit board server.  Run:  uvicorn server:app --port 8000   then open http://127.0.0.1:8000

GET  /            the board
GET  /stream      Server-Sent Events (live lines)
GET  /events      polling fallback  (?after=<id>)
POST /events      agents add a line  {agent, kind, summary, payload, severity}
GET  /api/state   counters + cards, for a fresh page load
POST /api/crs/pull          pull CRS (live -> cached -> fallback) and rebuild cards
POST /api/wallet/pick       {merchant, amount, category?}  recommend only, nothing charged
POST /api/wallet/confirm    {decision_id}  Maria said yes (single use, re-checked)
POST /api/demo/reset        run `demo.py reset` (local demo only)
"""
import asyncio
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

import wallet
from core import (
    AGENTS,
    ROOT,
    connect,
    db_path,
    emit,
    get_config,
    max_event_id,
    rows_after,
)

app = FastAPI(title="Pocket CPA audit board")
MAX_PAYLOAD_BYTES = 16_000


class EventIn(BaseModel):
    agent: str = Field(min_length=1, max_length=32)
    kind: str = Field(min_length=1, max_length=40)
    summary: str = Field(min_length=1, max_length=400)
    payload: dict = Field(default_factory=dict)
    severity: str = "info"


class PickIn(BaseModel):
    merchant: str = Field(min_length=1, max_length=80)
    amount: float = Field(gt=0, le=100000)
    category: str | None = None


class ConfirmIn(BaseModel):
    decision_id: str = Field(min_length=8, max_length=64)


@app.get("/stream")
async def stream(request: Request):
    last = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)

    async def gen():
        nonlocal last
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(rows_after, last)
            if not rows and last and await asyncio.to_thread(max_event_id) < last:
                last = 0                     # ledger was reset: start over from the first line
                yield "event: board_reset\ndata: {}\n\n"
                continue
            for e in rows:
                last = e["id"]
                yield f"id: {e['id']}\nevent: agent_event\ndata: {json.dumps(e)}\n\n"
            if not rows:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/events")
def poll(after: int = 0):
    return rows_after(after)


@app.post("/events")
def ingest(e: EventIn, request: Request):
    token = os.getenv("BOARD_TOKEN")
    if token and not hmac.compare_digest(request.headers.get("x-board-token", ""), token):
        raise HTTPException(401, "missing or wrong X-Board-Token")
    if len(json.dumps(e.payload, default=str)) > MAX_PAYLOAD_BYTES:
        raise HTTPException(413, f"payload over {MAX_PAYLOAD_BYTES} bytes")
    agent = e.agent.lower()
    if agent not in AGENTS:
        raise HTTPException(422, f"agent must be one of {sorted(AGENTS)}")
    return {"id": emit(agent, e.kind, e.summary, e.payload, e.severity)}


@app.get("/api/state")
def state():
    if not Path(db_path()).exists():
        raise HTTPException(503, "no ledger yet: run python scripts/reset_seed.py")
    con = connect()
    try:
        cfg = {k: get_config(con, k) for k in ("demo_now", "bank", "crs_mode", "hours_needed", "income_needed")}
    finally:
        con.close()
    cards = [{"card_id": c.card_id, "name": c.name, "limit": c.limit, "balance": c.balance,
              "util": round(c.util, 4), "source": c.source,
              "closes_on": wallet.next_close(wallet.demo_now().date(), c.close_day).isoformat()}
             for c in wallet.load_cards()]
    return {"config": cfg, "cards": cards}


@app.post("/api/crs/pull")
def crs_pull():
    return wallet.refresh_from_crs()


@app.post("/api/wallet/pick")
def wallet_pick(p: PickIn):
    return wallet.pick(p.merchant, p.amount, p.category)


@app.post("/api/wallet/confirm")
def wallet_confirm(c: ConfirmIn):
    try:
        return wallet.confirm(c.decision_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except wallet.StaleDecisionError as e:
        raise HTTPException(409, str(e)) from e


@app.post("/api/demo/reset")
def demo_reset(request: Request):
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "reset is local-only")
    args = [sys.executable, str(ROOT / "demo.py"), "reset"]
    if os.getenv("RESET_WITH_NESSIE"):
        args.append("--nessie")
    out = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise HTTPException(500, out.stderr[-500:])
    return {"ok": True, "log": out.stdout}


@app.get("/")
def page():
    return HTMLResponse((ROOT / "board.html").read_text())
