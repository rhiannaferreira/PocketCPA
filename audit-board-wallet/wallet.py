"""Wallet: every card 'bids' what it would earn Maria; the best bid that doesn't hurt her credit wins.

Rules, in order:
  1. A card with an interest-bearing balance never competes (rewards < interest).  -> excluded
  2. A card that would go over its limit never competes.                           -> excluded
  3. A card that would end up over the utilization ceiling within the reporting
     window before its statement closes is held back (that number gets reported).  -> held_back
  4. Best reward among eligible cards wins. Ties go to lower utilization.
  5. If every competing card is held back, pick the one that ends lowest.
The Wallet only recommends. Nothing is charged until Maria confirms.

This file also builds Maria's card state from CRS and holds the actions the board calls
(refresh_from_crs, pick, confirm). Every action leaves a line on the audit board.
"""
from __future__ import annotations

import calendar
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache

import yaml

import crs
from core import ROOT, demo_now, emit, tx

# ======================================================================
# engine: Wallet: every card 'bids' what it would earn Maria; the best bid that doesn't hurt her credit wins.
# ======================================================================

HERE = ROOT


@lru_cache(maxsize=1)
def load_rules() -> dict:
    return yaml.safe_load((HERE / "cards.yaml").read_text())


@lru_cache(maxsize=1)
def load_merchants() -> list[dict]:
    return yaml.safe_load((HERE / "merchants.yaml").read_text())


def categorize(merchant: str) -> str:
    m = merchant.lower()
    for rule in load_merchants():
        if re.search(rf"(?<![a-z0-9]){re.escape(rule['match'])}(?![a-z0-9])", m):
            return rule["category"]
    return "other"


def quarter_key(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def next_close(today: date, close_day: int) -> date:
    def clamp(y, m):
        return date(y, m, min(close_day, calendar.monthrange(y, m)[1]))
    this = clamp(today.year, today.month)
    if this >= today:
        return this
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return clamp(y, m)


# ---------- reward math (cap-aware, splits a purchase that straddles a cap) ----------

def reward_for(rule: dict, category: str, amount: float, usage: dict, activated: bool,
               today: date) -> tuple[float, str]:
    base = rule.get("base_rate", 0.0)
    flat = max(base, rule.get("fixed", {}).get(category, 0.0))
    reward, notes = amount * flat, f"{flat:.0%} on {category}"

    rot = rule.get("rotating")
    if rot and category in rot.get("categories", {}).get(quarter_key(today), []):
        if rot.get("requires_activation") and not activated:
            notes += " (5% bonus not activated)"
        else:
            room = max(0.0, rot["cap_usd_per_quarter"] - usage.get("rotating_qtd", 0.0))
            bonus_part = min(amount, room)
            r = bonus_part * max(rot["rate"], flat) + (amount - bonus_part) * flat
            if r > reward:
                reward = r
                notes = f"{rot['rate']:.0%} rotating {category}" + (
                    "" if bonus_part == amount else f" on first ${bonus_part:,.0f}, then {flat:.0%}")

    cap = rule.get("shared_cap")
    for c in rule.get("capped", []):
        if category in c["categories"] and cap:
            room = max(0.0, cap["usd_per_year"] - usage.get("capped_ytd", 0.0))
            capped_part = min(amount, room)
            after = max(cap.get("after_rate", base), flat)
            r = capped_part * c["rate"] + (amount - capped_part) * after
            if r > reward:
                reward = r
                notes = f"{c['rate']:.0%} on {category}" + (
                    "" if capped_part == amount else f" until the ${cap['usd_per_year']:,} cap")
    return round(reward, 2), notes


# ---------- state ----------

@dataclass
class Card:
    card_id: str
    name: str
    limit: float
    balance: float
    close_day: int
    activated: bool
    carrying_balance: bool
    open_date: str | None
    source: str
    usage: dict = field(default_factory=dict)

    @property
    def util(self) -> float:
        return self.balance / self.limit if self.limit else 1.0


def load_cards(path: str | None = None) -> list[Card]:
    rules = load_rules()
    today = demo_now().date()
    q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1).isoformat()
    y_start = date(today.year, 1, 1).isoformat()
    priors = {u["card_id"]: u.get("prior_spend", {}) for u in rules["user_cards"]}
    order = [u["card_id"] for u in rules["user_cards"]]
    cards = []
    with tx(path) as con:
        rows = con.execute("SELECT * FROM cards_state").fetchall()
        spend = [dict(s) for s in con.execute(
            "SELECT card_id, ts, category, amount_usd FROM wallet_spend WHERE ts >= ?", (y_start,))]
    for r in sorted(rows, key=lambda r: order.index(r["card_id"]) if r["card_id"] in order else 99):
        if r["card_id"] not in rules["catalog"]:
            continue                      # unknown card in the ledger: skip rather than crash the demo
        rule = rules["catalog"][r["card_id"]]
        rot_cats = set((rule.get("rotating") or {}).get("categories", {}).get(quarter_key(today), []))
        cap_cats = set((rule.get("shared_cap") or {}).get("applies_to", []))
        mine = [s for s in spend if s["card_id"] == r["card_id"]]
        p = priors.get(r["card_id"], {})
        cards.append(Card(
            card_id=r["card_id"], name=rule["name"], limit=r["limit_usd"], balance=r["balance_usd"],
            close_day=r["statement_close_day"], activated=bool(r["rotating_activated"]),
            carrying_balance=bool(r["carrying_balance"]), open_date=r["open_date"], source=r["source"],
            usage={"rotating_qtd": sum(s["amount_usd"] for s in mine
                                       if s["category"] in rot_cats and s["ts"] >= q_start)
                   + p.get("rotating_qtd_usd", 0),
                   "capped_ytd": sum(s["amount_usd"] for s in mine if s["category"] in cap_cats)
                   + p.get("gas_ytd_usd", 0)}))
    return cards


# ---------- the decision ----------

@dataclass
class Bid:
    card_id: str
    name: str
    reward: float
    rate_note: str
    util_before: float
    util_after: float
    closes_on: str
    days_to_close: int
    status: str          # eligible | held_back | excluded
    reason: str


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def _money(x: float) -> str:
    return f"${x:,.2f}"


def decide(merchant: str, amount: float, category: str | None = None,
           cards: list[Card] | None = None, path: str | None = None) -> dict:
    if amount <= 0:
        raise ValueError("amount must be positive")
    rules = load_rules()
    ceiling = rules["settings"]["utilization_ceiling"]
    window = rules["settings"]["reporting_window_days"]
    today = demo_now().date()
    category = category or categorize(merchant)
    cards = cards if cards is not None else load_cards(path)

    bids: list[Bid] = []
    for c in cards:
        rule = rules["catalog"][c.card_id]
        reward, note = reward_for(rule, category, amount, c.usage, c.activated, today)
        close = next_close(today, c.close_day)
        dtc = (close - today).days
        after = (c.balance + amount) / c.limit
        if c.carrying_balance:
            status, reason = "excluded", "has an interest-bearing balance; pay it down first"
        elif c.balance + amount > c.limit:
            status, reason = "excluded", "would go over the credit limit"
        elif after > ceiling and dtc <= window:
            status, reason = "held_back", (f"would report {after:.0%} of its limit when the statement "
                                           f"closes {_day(close)} ({dtc} days)")
        else:
            status, reason = "eligible", f"stays at {after:.0%} of its limit"
        bids.append(Bid(c.card_id, c.name, reward, note, round(c.util, 4), round(after, 4),
                        close.isoformat(), dtc, status, reason))

    competing = [b for b in bids if b.status != "excluded"]
    eligible = [b for b in bids if b.status == "eligible"]
    top = max(competing, key=lambda b: (b.reward, -b.util_after), default=None)
    if eligible:
        winner = max(eligible, key=lambda b: (b.reward, -b.util_after))
        mode = "best_safe"
    elif competing:
        winner = min(competing, key=lambda b: b.util_after)
        mode = "least_harm"
    else:
        winner, mode = None, "none"

    total_limit = sum(c.limit for c in cards) or 1
    total_bal = sum(c.balance for c in cards)
    overall_after = (total_bal + amount) / total_limit

    if winner is None:
        line = "No card is a safe choice for this purchase. Use debit or cash."
        give_up = 0.0
    else:
        give_up = round(top.reward - winner.reward, 2) if top else 0.0
        line = f"Use {winner.name}: earns {_money(winner.reward)} ({winner.rate_note})."
        if top and top.card_id != winner.card_id and give_up > 0:
            line += (f" {top.name} would earn {_money(top.reward)} but {top.reason}."
                     f" Giving up {_money(give_up)} to protect her credit.")
        elif mode == "least_harm":
            line += " Every card crosses the ceiling, so this one stays lowest."

    tips = credit_tips(cards, bids, amount, ceiling, window, today)
    return {
        "merchant": merchant, "amount": amount, "category": category, "category_is_estimate": True,
        "mode": mode, "winner": asdict(winner) if winner else None, "top_reward": asdict(top) if top else None,
        "gave_up_usd": give_up, "explanation": line, "bids": [asdict(b) for b in bids],
        "overall_util_before": round(total_bal / total_limit, 4), "overall_util_after": round(overall_after, 4),
        "tips": tips, "requires_confirmation": True,
    }


# ---------- credit tips: fixed templates only, so wording can't drift ----------

TIP_TEMPLATES = {
    "pay_before_close": ("Paying {amount} on your {card} before {date} could bring the balance it reports "
                         "to under {pct} of its limit."),
    "carrying": "Your {card} has an interest-bearing balance. Pay that down before earning rewards on it.",
    "pay_in_full": ("Pay the full statement balance by the due date. Carrying a balance costs interest "
                    "and doesn't help your score."),
    "overall": "Your total usage across cards is {pct} of your combined limits.",
    "disclaimer": "Educational information, not financial advice.",
}


def _tip(tip_id: str, **kw) -> dict:
    return {"id": tip_id, "text": TIP_TEMPLATES[tip_id].format(**kw)}


def credit_tips(cards, bids, amount, ceiling, window, today) -> list[dict]:
    tips = []
    by_id = {b.card_id: b for b in bids}
    for c in cards:
        b = by_id[c.card_id]
        if c.carrying_balance:
            tips.append(_tip("carrying", card=c.name))
        elif c.util > ceiling and b.days_to_close <= window:
            need = c.balance - ceiling * c.limit
            tips.append(_tip("pay_before_close", amount=_money(need), card=c.name,
                             date=_day(date.fromisoformat(b.closes_on)), pct=f"{ceiling:.0%}"))
    total_limit = sum(c.limit for c in cards) or 1
    tips.append(_tip("overall", pct=f"{sum(c.balance for c in cards) / total_limit:.0%}"))
    tips.append(_tip("pay_in_full"))
    tips.append(_tip("disclaimer"))
    return tips


# ---------- confirm: only now does anything change ----------

def _charge(decision: dict, path: str | None = None) -> dict:
    w = decision.get("winner")
    if not w:
        raise ValueError("nothing to confirm")
    with tx(path) as con:
        con.execute("UPDATE cards_state SET balance_usd = balance_usd + ? WHERE card_id=?",
                    (decision["amount"], w["card_id"]))
        con.execute("INSERT INTO wallet_spend(ts,card_id,merchant,category,amount_usd,reward_usd) "
                    "VALUES (?,?,?,?,?,?)", (demo_now().isoformat(), w["card_id"], decision["merchant"],
                                            decision["category"], decision["amount"], w["reward"]))
    return {"charged_card": w["card_id"], "amount": decision["amount"]}


# ======================================================================
# state: Builds Maria's cards_state from CRS (live or cached) or from cards.yaml.
# ======================================================================

def load_wallet_state(con, mode: str, report: dict | None) -> list[dict]:
    rules = load_rules()
    apply = rules["settings"].get("crs_apply", "full") == "full"
    tradelines = crs.revolving_tradelines(report) if report else []
    con.execute("DELETE FROM cards_state")
    out = []
    for u in rules["user_cards"]:
        fb = u["fallback"]
        t = tradelines[u["crs_tradeline"]] if apply and u.get("crs_tradeline") is not None \
            and u["crs_tradeline"] < len(tradelines) else None
        row = {
            "card_id": u["card_id"],
            "limit_usd": t["limit_usd"] if t else fb["limit_usd"],
            "balance_usd": t["balance_usd"] if t else fb["balance_usd"],
            "open_date": (t or {}).get("open_date") or fb.get("open_date"),
            "statement_close_day": u["statement_close_day"],
            "rotating_activated": int(u.get("rotating_activated", False)),
            "carrying_balance": int(u.get("carrying_balance", False)),
            "source": f"crs_{mode}" if t else "cards.yaml",
            "crs_creditor": t["creditor"] if t else None,
        }
        con.execute("INSERT INTO cards_state(card_id,limit_usd,balance_usd,statement_close_day,"
                    "rotating_activated,carrying_balance,open_date,source) VALUES (?,?,?,?,?,?,?,?)",
                    (row["card_id"], row["limit_usd"], row["balance_usd"], row["statement_close_day"],
                     row["rotating_activated"], row["carrying_balance"], row["open_date"], row["source"]))
        out.append(row)
    return out


# ======================================================================
# service: What the server and the replay call. Every action here leaves a line on the audit board.
# ======================================================================

def refresh_from_crs(path: str | None = None) -> dict:
    mode, report, note = crs.get_report()
    with tx(path) as con:
        cards = load_wallet_state(con, mode, report)
    tradelines = crs.revolving_tradelines(report) if report else []
    score = crs.score_summary(report) if report else None
    label = {"live": "LIVE", "cached": "CACHED", "fallback": "FALLBACK"}[mode]
    summary = (f"Credit file pulled from CRS sandbox ({label}): {len(tradelines)} revolving accounts"
               if report else "CRS unavailable: using typed card file (FALLBACK)")
    emit("crs", "pull", summary, {"mode": mode, "note": note, "tradelines": tradelines, "score": score,
                                  "cards": cards}, "ok" if mode == "live" else "warn", path=path)
    return {"mode": mode, "note": note, "tradelines": tradelines, "score": score, "cards": cards}


def pick(merchant: str, amount: float, category: str | None = None, path: str | None = None) -> dict:
    d = decide(merchant, amount, category, path=path)
    if d["winner"]:
        d["decision_id"] = uuid.uuid4().hex
        with tx(path) as con:
            con.execute("INSERT INTO pending_decisions(id,ts,merchant,amount_usd,category,winner) "
                        "VALUES (?,?,?,?,?,?)", (d["decision_id"], demo_now().isoformat(), merchant,
                                                 amount, d["category"], d["winner"]["card_id"]))
    held = d["top_reward"] and d["winner"] and d["top_reward"]["card_id"] != d["winner"]["card_id"]
    sev = "warn" if held or d["mode"] != "best_safe" else "ok"
    emit("wallet", "decision", d["explanation"], d, sev, path=path)
    for t in d["tips"]:
        if t["id"] in ("pay_before_close", "carrying"):
            emit("wallet", "tip", t["text"], t, "info", path=path)
    return d


class StaleDecisionError(Exception):
    """The cards changed since this recommendation was made; ask the Wallet again."""


def confirm(decision_id: str, path: str | None = None) -> dict:
    """Charge only a recommendation the server made, once, and only if it still holds."""
    with tx(path) as con:
        row = con.execute("SELECT * FROM pending_decisions WHERE id=?", (decision_id,)).fetchone()
        if not row:
            raise LookupError("unknown or already-confirmed decision")
        # single use: only the caller whose DELETE removes the row may charge (no double charge on double click)
        if con.execute("DELETE FROM pending_decisions WHERE id=?", (decision_id,)).rowcount != 1:
            raise LookupError("unknown or already-confirmed decision")
    fresh = decide(row["merchant"], row["amount_usd"], row["category"], path=path)
    if not fresh["winner"] or fresh["winner"]["card_id"] != row["winner"]:
        raise StaleDecisionError("the best card changed since this was recommended; ask again")
    res = _charge(fresh, path=path)
    w = fresh["winner"]
    emit("wallet", "charged", f"Maria approved: ${fresh['amount']:,.2f} at {fresh['merchant']} on {w['name']}",
         {**res, "decision_id": decision_id}, "ok", path=path)
    return res
