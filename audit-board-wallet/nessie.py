"""Capital One Nessie mock bank: seeds a fresh customer each reset, with retries."""
from __future__ import annotations

import os
import time

import requests

# ======================================================================
# nessie: Capital One Nessie (mock bank). Quirks handled here, per other 2026 teams' notes:
# ======================================================================

BASE = os.getenv("NESSIE_BASE_URL", "https://api.nessieisreal.com")


class NessieError(RuntimeError):
    pass


def call(method: str, path: str, body: dict | None = None, tries: int = 4):
    key = os.environ.get("NESSIE_API_KEY")
    if not key:
        raise NessieError("NESSIE_API_KEY is not set")
    for i in range(tries):
        try:
            r = requests.request(method, f"{BASE}{path}", params={"key": key}, json=body, timeout=15)
        except requests.RequestException as e:
            if i == tries - 1:
                raise NessieError(str(e)) from e
            time.sleep(1.5 * (i + 1))
            continue
        if r.status_code == 404 and method == "GET":
            return []                                # empty-list quirk
        if r.status_code < 300:
            return r.json() if r.content else {}
        if r.status_code in (429, 500, 502, 503):
            time.sleep(1.5 * (i + 1))
            continue
        raise NessieError(f"{method} {path} -> {r.status_code} {r.text[:200]}")
    raise NessieError(f"{method} {path} failed after {tries} tries")


def _id(resp) -> str:
    obj = resp.get("objectCreated", resp) if isinstance(resp, dict) else {}
    if not obj.get("_id"):
        raise NessieError(f"no _id in create response: {str(resp)[:200]}")
    return obj["_id"]


def seed(team_seed: dict, run_tag: str) -> dict:
    """Creates Maria + checking account + her deposits from demo/data/nessie_seed.json.
    Returns ids and the deposits as the bank reports them back (in cents)."""
    cust = team_seed.get("customer", {})
    cid = _id(call("POST", "/customers", {
        "first_name": cust.get("first_name", "Maria"), "last_name": f"{cust.get('last_name', 'Delgado')}-{run_tag}",
        "address": {"street_number": "100", "street_name": "Fulton St", "city": "Fresno",
                    "state": "CA", "zip": "93721"}}))
    aid = _id(call("POST", f"/customers/{cid}/accounts", {
        "type": "Checking", "nickname": team_seed.get("account", {}).get("nickname", "Checking"),
        "rewards": 0, "balance": 1200}))
    for d in team_seed["deposits"]:
        if d["amount_cents"] % 100:
            raise NessieError(f"{d['id']}: Nessie stores whole dollars only, got {d['amount_cents']} cents")
        call("POST", f"/accounts/{aid}/deposits", {
            "medium": "balance", "transaction_date": d["date"], "status": "completed",
            "amount": d["amount_cents"] // 100, "description": d["description"]})
    deposits = [{"id": d["_id"], "date": d.get("transaction_date"), "amount_cents": int(d["amount"]) * 100,
                 "description": d.get("description", "")} for d in call("GET", f"/accounts/{aid}/deposits")]
    if len(deposits) != len(team_seed["deposits"]):
        raise NessieError(f"bank shows {len(deposits)} deposits, expected {len(team_seed['deposits'])}")
    return {"customer_id": cid, "account_id": aid, "deposits": deposits}
