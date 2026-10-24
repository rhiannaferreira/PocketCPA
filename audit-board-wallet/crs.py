"""CRS sandbox: login, Experian soft pull, defensive parser, live -> cached -> fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from core import FIXTURES

# ======================================================================
# crs: CRS sandbox: log in, pull the Experian soft-pull test report, turn tradelines into Wallet cards.
# ======================================================================

BASE = os.getenv("CRS_BASE_URL", "https://api-sandbox.stitchcredit.com:443/api")
REPORT_PATH = os.getenv("CRS_REPORT_PATH", "/experian/credit-profile/credit-report/standard/exp-prequal-vantage4")
CACHE = Path(os.getenv("CRS_CACHE", FIXTURES / "crs_cache.json"))
PERSONA = Path(os.getenv("CRS_PERSONA", FIXTURES / "crs_persona.json"))


class CRSError(RuntimeError):
    pass


def _token_from(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    for k in ("token", "access_token", "accessToken", "jwt", "id_token"):
        if isinstance(body, dict) and body.get(k):
            return body[k]
    auth = resp.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1]
    raise CRSError(f"login worked but no token found; keys were {list(body)[:8] if isinstance(body, dict) else type(body)}")


def login(username: str, password: str, timeout: float = 15) -> str:
    r = requests.post(f"{BASE}/users/login", timeout=timeout,
                      headers={"Content-Type": "application/json", "Accept": "application/json"},
                      json={"username": username, "password": password})
    if not r.ok:
        raise CRSError(f"login {r.status_code}: {r.text[:200]}")
    return _token_from(r)


def pull_report(token: str, persona: dict, timeout: float = 30) -> dict:
    r = requests.post(f"{BASE}{REPORT_PATH}", timeout=timeout, json=persona, headers={
        "Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {token}"})
    if not r.ok:
        raise CRSError(f"report {r.status_code}: {r.text[:200]}")
    return r.json()


# ---------- parsing (every value in the Experian JSON is a string and any may be missing) ----------

def _num(x):
    if x in (None, ""):
        return None
    try:
        return float(str(x).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _find_tradelines(obj) -> list:
    """The tradeline array can be nested differently per configuration; find it wherever it is."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in ("tradeline", "tradelines") and isinstance(v, list):
                return v
        for v in obj.values():
            found = _find_tradelines(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_tradelines(v)
            if found:
                return found
    return []


def _iso(d):
    """Experian dates often come as MMDDYYYY. Return ISO when we can, else pass through."""
    if not d:
        return None
    s = str(d)
    if len(s) == 8 and s.isdigit():
        return f"{s[4:]}-{s[:2]}-{s[2:4]}"
    return s


def revolving_tradelines(report: dict) -> list[dict]:
    out = []
    for i, t in enumerate(_find_tradelines(report)):
        epd = t.get("enhancedPaymentData") or {}
        kind = (t.get("revolvingOrInstallment") or "").upper()
        limit = _num(epd.get("creditLimitAmount"))
        if kind.startswith("I") or not limit:
            continue
        bal = _num(t.get("balanceAmount")) or 0.0
        out.append({
            "index": i, "creditor": t.get("subscriberName"), "limit_usd": limit, "balance_usd": bal,
            "utilization": round(bal / limit, 4), "balance_date": _iso(t.get("balanceDate")),
            "open_date": _iso(t.get("openDate")), "open": t.get("openOrClosed"),
            "late_30": t.get("delinquencies30Days"), "payment_history": t.get("paymentHistory"),
            "type_verified": kind.startswith("R"),   # 'R' meaning revolving is unconfirmed; check your fixture
        })
    return out


def score_summary(report: dict) -> dict | None:
    def find(obj):
        if isinstance(obj, dict):
            if isinstance(obj.get("riskModel"), list) and obj["riskModel"]:
                return obj["riskModel"][0]
            for v in obj.values():
                f = find(v)
                if f:
                    return f
        elif isinstance(obj, list):
            for v in obj:
                f = find(v)
                if f:
                    return f
    rm = find(report)
    if not rm:
        return None
    return {"score": rm.get("score"), "model": rm.get("modelIndicator")}


# ---------- one call the server uses ----------

def get_report() -> tuple[str, dict | None, str]:
    """Returns (mode, report, note). mode is 'live', 'cached' or 'fallback'."""
    user, pw = os.getenv("CRS_USERNAME"), os.getenv("CRS_PASSWORD")
    if user and pw and PERSONA.exists():
        try:
            report = pull_report(login(user, pw), json.loads(PERSONA.read_text()))
            CACHE.write_text(json.dumps(report, indent=2))
            return "live", report, "pulled from CRS sandbox just now"
        except (CRSError, requests.RequestException) as e:
            note = f"live pull failed ({str(e)[:120]})"
    else:
        note = "no CRS credentials set"
    if CACHE.exists():
        return "cached", json.loads(CACHE.read_text()), note + "; using last good response"
    return "fallback", None, note + "; using cards.yaml"
