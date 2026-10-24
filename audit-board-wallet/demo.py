"""Demo tool: reset the ledger, replay Maria's story onto the board, and check it runs identically.

    python demo.py reset              local ledger + CRS (live -> cached -> cards.yaml)
    python demo.py reset --nessie     also create a fresh Maria in Nessie (needs NESSIE_API_KEY)
    python demo.py reset --no-crs     skip CRS entirely
    python demo.py replay             paced run-through of the whole story
    python demo.py replay --instant   no pauses
    python demo.py replay --only save just the Wallet part
    python demo.py check3             3 resets + replays must produce the identical story

Data comes from the team's sample set in ../demo/data (answer_key.json, nessie_seed.json):
Maria Delgado, Parcelo payouts, February to May 2026, money in integer cents.

Replay: Verify and Prove lines come from the reference matcher below and are tagged
source=replay (the board shows a "rehearsal replay" badge) until the real agents post events.
Save lines are real: they call the actual Wallet and CRS code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import crs
import wallet
from core import (
    ROOT,
    TEAM_DATA,
    connect,
    db_path,
    demo_now,
    emit,
    init_schema,
    set_config,
    tx,
)

HOURS_NEEDED, INCOME_NEEDED_CENTS = 80, 58000
FEE_TOLERANCE_CENTS = 100
MAX_PAYOUT_LAG_DAYS = 5
# Upcoming purchases the Wallet is asked about, based on Maria's usual spending in nessie_seed.json.
SCENARIO: list[dict] = [
    {"merchant": "Valley Gas #12", "amount": 41.20, "why": "gas moment: best card held back to protect her credit"},
    {"merchant": "Corner Grocery", "amount": 24.65, "why": "everyday pick"},
]
EXPECTED_GAS = {"held_back": "costco_anywhere_visa", "winner": "citi_double_cash"}


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def day(s: str) -> str:
    x = date.fromisoformat(s)
    return f"{x:%b} {x.day}"


def span(a: str, b: str) -> str:
    x, y = date.fromisoformat(a), date.fromisoformat(b)
    return f"{x:%b} {x.day}-{y.day}" if x.month == y.month else f"{day(a)}-{day(b)}"


def month_name(m: str) -> str:
    return date.fromisoformat(m + "-01").strftime("%B %Y")


# ======================================================================
# Team data -> ledger rows
# ======================================================================

def load_team_data() -> tuple[dict, dict]:
    key = json.loads((TEAM_DATA / "answer_key.json").read_text())
    bank = json.loads((TEAM_DATA / "nessie_seed.json").read_text())
    return key, bank


def entries_from(key: dict) -> list[dict]:
    files = {d["id"]: d["file"] for d in key["documents"]}
    return [{"id": e["document_id"], "source_file": files.get(e["document_id"], ""), "platform": e["platform"],
             "period_start": e["period_start"], "period_end": e["period_end"], "gross_cents": e["gross_cents"],
             "active_minutes": e.get("active_minutes")} for e in key["income_entries"]]


# ======================================================================
# Reference matcher (fixture self-check and replay; the real Bookkeeper/Auditor replaces it)
# A payout matches a deposit with the platform name in its description, the same amount
# (within $1 for fees), landing 0-5 days after the pay period ends. One deposit per payout.
# ======================================================================

def reconcile(entries: list[dict], deposits: list[dict]) -> dict:
    used: set[str] = set()
    results: list[dict] = []
    for e in entries:
        end = date.fromisoformat(e["period_end"])
        hit = next((d for d in deposits if d["id"] not in used
                    and e["platform"].upper() in d["description"].upper()
                    and abs(d["amount_cents"] - e["gross_cents"]) <= FEE_TOLERANCE_CENTS
                    and 0 <= (date.fromisoformat(d["date"]) - end).days <= MAX_PAYOUT_LAG_DAYS), None)
        if hit:
            used.add(hit["id"])
            results.append({"entry": e, "status": "verified", "deposit": hit})
            continue
        twin = next((r for r in results if r["status"] == "verified"
                     and (r["entry"]["platform"], r["entry"]["period_start"], r["entry"]["period_end"],
                          r["entry"]["gross_cents"]) ==
                     (e["platform"], e["period_start"], e["period_end"], e["gross_cents"])), None)
        if twin:
            results.append({"entry": e, "status": "duplicate", "duplicate_of": twin["entry"]["id"],
                            "deposit": twin["deposit"]})
        else:
            results.append({"entry": e, "status": "missing"})
    return {
        "results": results,
        "duplicates": [r["entry"]["id"] for r in results if r["status"] == "duplicate"],
        "missing": [r["entry"]["id"] for r in results if r["status"] == "missing"],
        "unexplained_deposits": [d["id"] for d in deposits if d["id"] not in used],
    }


def month_summaries(entries, deposits, activities, expenses) -> dict:
    """Per month, by the pay period's start (a Mar 29-31 period paid Apr 2 counts in March)."""
    r = reconcile(entries, deposits)
    out: dict = defaultdict(lambda: {"gross_stated_cents": 0, "gross_verified_cents": 0, "work_minutes": 0,
                                     "volunteer_hours": 0, "expenses_cents": 0})
    for x in r["results"]:
        m = out[x["entry"]["period_start"][:7]]
        m["gross_stated_cents"] += x["entry"]["gross_cents"]
        if x["status"] == "verified":
            m["gross_verified_cents"] += x["entry"]["gross_cents"]
            m["work_minutes"] += x["entry"].get("active_minutes") or 0
    for a in activities:
        out[a["month"]]["volunteer_hours"] += a["hours"]
    for e in expenses:
        out[e["date"][:7]]["expenses_cents"] += e["amount_cents"]
    for m in out.values():
        m["work_hours_verified"] = m.pop("work_minutes") / 60
        m["net_cents"] = m["gross_verified_cents"] - m["expenses_cents"]
        m["hours_total"] = m["work_hours_verified"] + m["volunteer_hours"]
        m["meets_hours"] = m["hours_total"] >= HOURS_NEEDED
        m["meets_income"] = m["net_cents"] >= INCOME_NEEDED_CENTS
    return dict(sorted(out.items()))


def check_against_key(sums: dict, key: dict) -> list[str]:
    problems = []
    for month, exp in key["month_summaries"].items():
        got = sums.get(month, {})
        for field, want in exp.items():
            if field in got and abs(got[field] - want) > 1e-9:
                problems.append(f"{month} {field}: got {got[field]}, answer key says {want}")
    return problems


# ======================================================================
# reset: one command, same demo every time
# ======================================================================

def fingerprint(path: str) -> str:
    h = hashlib.sha256()
    con = connect(path)
    for table in ("entries", "deposits", "activities", "expenses", "cards_state", "events"):
        cols = "*" if table != "deposits" else "date, amount_cents, description"
        # table and cols come from the fixed tuple above, never from input
        for row in con.execute(f"SELECT {cols} FROM {table} ORDER BY 1, 2"):  # noqa: S608  # nosec B608
            h.update(json.dumps(list(row), default=str).encode())
    con.close()
    return h.hexdigest()[:12]


def reset(args) -> int:
    t0 = time.time()
    key, bank_seed = load_team_data()
    path = db_path()
    for suffix in ("", "-wal", "-shm"):
        Path(path + suffix).unlink(missing_ok=True)
    with tx(path) as con:
        init_schema(con)

    deposits = [{k: d[k] for k in ("id", "date", "amount_cents", "description")} for d in bank_seed["deposits"]]
    bank = "sample file (offline)"
    if args.nessie:
        import nessie
        n = nessie.seed(bank_seed, demo_now().strftime("%m%d") + "-" + secrets.token_hex(3))
        deposits, bank = n["deposits"], f"Nessie customer {n['customer_id']}"

    mode, report, note = ("fallback", None, "skipped with --no-crs") if args.no_crs else crs.get_report()
    entries = entries_from(key)
    activities = [{"id": a["document_id"], "kind": a["kind"], "month": a["month"], "hours": a["hours"]}
                  for a in key["activity_entries"]]
    expenses = [{"id": e["document_id"], "date": e["date"], "category": e["category"],
                 "amount_cents": e["amount_cents"]} for e in key["expenses"]]

    with tx(path) as con:
        con.executemany("INSERT INTO entries(id,source_file,platform,period_start,period_end,gross_cents,"
                        "active_minutes) VALUES (:id,:source_file,:platform,:period_start,:period_end,"
                        ":gross_cents,:active_minutes)", entries)
        con.executemany("INSERT INTO deposits(id,date,amount_cents,description) "
                        "VALUES (:id,:date,:amount_cents,:description)", deposits)
        con.executemany("INSERT INTO activities(id,kind,month,hours) VALUES (:id,:kind,:month,:hours)", activities)
        con.executemany("INSERT INTO expenses(id,date,category,amount_cents) "
                        "VALUES (:id,:date,:category,:amount_cents)", expenses)
        cards = wallet.load_wallet_state(con, mode, report)
        for k, v in {"demo_now": demo_now().isoformat(), "bank": bank, "crs_mode": mode,
                     "hours_needed": HOURS_NEEDED, "income_needed": INCOME_NEEDED_CENTS / 100}.items():
            set_config(con, k, v)
        if args.nessie:
            set_config(con, "nessie_customer_id", n["customer_id"])
            set_config(con, "nessie_account_id", n["account_id"])

    emit("system", "reset",
         f"Demo reset: Maria Delgado, Feb-May 2026. {len(entries)} earnings screenshots, {len(deposits)} deposits, "
         f"{len(cards)} cards (bank: {bank}; cards: {mode}).",
         {"bank": bank, "crs_mode": mode, "crs_note": note}, "ok", path=path)

    # Self-check: the team's answer key must still come out of the data.
    r = reconcile(entries, deposits)
    sums = month_summaries(entries, deposits, activities, expenses)
    problems = check_against_key(sums, key)
    exp_dup = next(f["documents"] for f in key["expected_flags"] if f["check"] == "duplicate")
    if r["duplicates"] != exp_dup[1:]:
        problems.append(f"duplicates {r['duplicates']} != {exp_dup[1:]}")
    exp_unexplained = [d for f in key["expected_flags"] if f["check"] == "deposit_without_payout"
                       for d in f["deposits"]]
    if not args.nessie and r["unexplained_deposits"] != exp_unexplained:
        problems.append(f"unexplained deposits {r['unexplained_deposits']} != {exp_unexplained}")
    gas = SCENARIO[0]
    d = wallet.decide(gas["merchant"], gas["amount"], path=path)
    held = [b["card_id"] for b in d["bids"] if b["status"] == "held_back"]
    winner = d["winner"]["card_id"] if d["winner"] else None
    gas_ok = EXPECTED_GAS["held_back"] in held and winner == EXPECTED_GAS["winner"]
    if not gas_ok:
        problems.append(f"gas moment did not trigger (held={held}, winner={winner}). With CRS numbers, "
                        f"set crs_apply: display_only in cards.yaml.")

    print(f"bank: {bank}\ncards: {mode} ({note})")
    for m, s in sums.items():
        verdict = "hours" if s["meets_hours"] else "income" if s["meets_income"] else "not met"
        print(f"{m}: stated {money(s['gross_stated_cents'])}, verified {money(s['gross_verified_cents'])}, "
              f"net {money(s['net_cents'])}, {s['hours_total']:g} h -> {verdict}")
    print(f"duplicates {r['duplicates']}  unexplained deposits {r['unexplained_deposits']}")
    print(f"gas moment: {'OK' if gas_ok else 'NOT TRIGGERED'} -> {d['explanation']}")
    for p in problems:
        print("WARNING:", p)
    print(f"READY in {time.time() - t0:.1f}s  fingerprint {fingerprint(path)}  db {path}")
    return 1 if problems and os.getenv("STRICT") else 0


# ======================================================================
# replay: the story, beat by beat
# ======================================================================

R = {"source": "replay"}


def run(pause: float, only: str | None) -> None:
    key, _ = load_team_data()
    with tx() as con:
        entries = [dict(r) for r in con.execute("SELECT * FROM entries ORDER BY period_start, id")]
        deposits = [dict(r) for r in con.execute("SELECT id, date, amount_cents, description FROM deposits "
                                                 "ORDER BY date, id")]
        activities = [dict(r) for r in con.execute("SELECT * FROM activities")]
        expenses = [dict(r) for r in con.execute("SELECT * FROM expenses")]

    def beat(k=1.0):
        time.sleep(pause * k)

    r = reconcile(entries, deposits)
    by_month: dict = defaultdict(list)
    for x in r["results"]:
        by_month[x["entry"]["period_start"][:7]].append(x)

    if only in (None, "verify"):
        emit("coordinator", "received", f"Maria sent {len(key['documents'])} documents in chat", R)
        beat()
        for month, xs in by_month.items():
            stated = sum(x["entry"]["gross_cents"] for x in xs)
            mins = sum(x["entry"].get("active_minutes") or 0 for x in xs)
            emit("intake", "extracted",
                 f"{month_name(month)}: {len(xs)} earnings screenshots read, {money(stated)}, {mins / 60:g} h active",
                 {**R, "month": month, "amount_cents": stated, "hours": mins / 60}, "info")
            beat(0.5)
        for a in activities:
            emit("intake", "extracted", f"Volunteer letter: {a['hours']:g} h in {month_name(a['month'])}",
                 {**R, "entry_id": a["id"], "hours": a["hours"]}, "info")
        for e in expenses:
            emit("intake", "extracted", f"{e['category'].title()} expense {day(e['date'])}: {money(e['amount_cents'])}"
                 " (stated)", {**R, "entry_id": e["id"]}, "info")
        if any(f["check"] == "hidden_instruction" for f in key["expected_flags"]):
            emit("intake", "hidden_instruction",
                 "County renewal letter has an instruction hidden in faint small print. Flagged and not followed.",
                 {**R, "entry_id": "county-letter-renewal"}, "alert")
        beat()
        for month, xs in by_month.items():
            ok = [x for x in xs if x["status"] == "verified"]
            emit("bookkeeper", "match",
                 f"{month_name(month)}: {len(ok)} of {len(xs)} payouts matched to bank deposits, "
                 f"{money(sum(x['entry']['gross_cents'] for x in ok))}",
                 {**R, "month": month, "amount_cents": sum(x["entry"]["gross_cents"] for x in ok)}, "ok")
            beat(0.5)
            for x in xs:
                if x["status"] == "duplicate":
                    e = x["entry"]
                    beat()
                    emit("auditor", "duplicate",
                         f"{e['id']} is the same {span(e['period_start'], e['period_end'])} payout as "
                         f"{x['duplicate_of']} ({money(e['gross_cents'])}), cropped differently. The bank shows one "
                         f"deposit, so it is not counted twice.",
                         {**R, "entry_id": e["id"], "duplicate_of": x["duplicate_of"], "amount_cents": e["gross_cents"],
                          "evidence": f"{e['source_file']} vs deposit {x['deposit']['id']}",
                          "question": "Are these two screenshots the same Parcelo payout?"}, "alert")
                elif x["status"] == "missing":
                    emit("auditor", "missing", f"{x['entry']['id']}: no matching deposit in the bank",
                         {**R, "entry_id": x["entry"]["id"], "amount_cents": x["entry"]["gross_cents"]}, "alert")
        dep = {d["id"]: d for d in deposits}
        for did in r["unexplained_deposits"]:
            d = dep[did]
            emit("auditor", "deposit_without_payout",
                 f"{money(d['amount_cents'])} deposit on {day(d['date'])} ({d['description']}) has no document "
                 f"behind it. Not counted as income until Maria explains it.",
                 {**R, "deposit_id": did, "amount_cents": d["amount_cents"],
                  "question": f"What was the {money(d['amount_cents'])} deposit on {day(d['date'])} for?"}, "warn")
        beat()

    if only in (None, "prove"):
        for month, s in month_summaries(entries, deposits, activities, expenses).items():
            if s["meets_hours"]:
                verdict = f"meets the hours path ({s['hours_total']:g} h)"
            elif s["meets_income"]:
                verdict = f"meets the earnings path ({money(s['net_cents'])} after stated expenses)"
            else:
                verdict = "below both paths"
            extra = f" + {s['volunteer_hours']:g} h volunteering" if s["volunteer_hours"] else ""
            emit("benefits", "progress",
                 f"{month_name(month)}: screenshots said {money(s['gross_stated_cents'])}; verified "
                 f"{money(s['net_cents'])} and {s['work_hours_verified']:g} h work{extra}. {verdict.capitalize()}.",
                 {**R, "month": month, "hours_verified": s["hours_total"], "income_verified_cents": s["net_cents"],
                  "hours_needed": HOURS_NEEDED, "income_needed_cents": INCOME_NEEDED_CENTS},
                 "ok" if s["meets_hours"] or s["meets_income"] else "warn")
            beat()

    if only in (None, "save"):
        wallet.refresh_from_crs()
        beat()
        for p in SCENARIO:
            wallet.pick(p["merchant"], p["amount"])
            beat(1.2)


# ======================================================================
# check3: the build plan's final check
# ======================================================================

def events_hash() -> tuple[str, int]:
    con = connect()
    rows = [list(r) for r in con.execute("SELECT agent, kind, severity, summary, payload FROM events ORDER BY id")]
    con.close()
    for r in rows:   # CRS notes can carry live error text; decision ids are random by design
        p = json.loads(r[4])
        for k in ("note", "crs_note", "decision_id"):
            p.pop(k, None)
        r[4] = p
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:12], len(rows)


def check3() -> int:
    hashes = []
    for i in range(1, 4):
        for cmd in (["demo.py", "reset"], ["demo.py", "replay", "--instant"]):
            out = subprocess.run([sys.executable, *cmd], cwd=ROOT, capture_output=True, text=True)
            if out.returncode != 0 or "WARNING" in out.stdout:
                print(f"run {i}: FAIL in {' '.join(cmd)}\n{out.stdout}{out.stderr}")
                return 1
        h, n = events_hash()
        hashes.append(h)
        print(f"run {i}: {n} board lines, story hash {h}")
    ok = len(set(hashes)) == 1
    print("PASS: 3/3 identical runs" if ok else "FAIL: runs differ")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Pocket CPA demo tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reset", help="rebuild the demo ledger")
    r.add_argument("--nessie", action="store_true", help="seed a fresh customer in Nessie")
    r.add_argument("--no-crs", action="store_true", help="skip CRS, use cards.yaml")
    p = sub.add_parser("replay", help="play the story onto the board")
    p.add_argument("--instant", action="store_true")
    p.add_argument("--speed", type=float, default=1.0, help="seconds per beat")
    p.add_argument("--only", choices=["verify", "prove", "save"])
    sub.add_parser("check3", help="3 identical runs from reset")
    a = ap.parse_args()
    if a.cmd == "reset":
        return reset(a)
    if a.cmd == "replay":
        run(0 if a.instant else a.speed, a.only)
        print("replay done")
        return 0
    return check3()


if __name__ == "__main__":
    raise SystemExit(main())
