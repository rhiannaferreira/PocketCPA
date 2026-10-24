"""Coverage corridor: does a month's evidence meet the Medi-Cal floor and stay under the ceiling?

Pure functions: no network, no model calls, no clock. Money is integer cents and every threshold
comes from rules.yaml. Whether the county counts a self-employed person's income gross or net of
business expenses is unresolved, so each month is judged both ways and the status says when the
answer depends on that choice. The county decides; this only reports what the evidence shows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Literal, Optional, Sequence

import yaml

from core.schemas import ActivityEntry, Expense, Flag, IncomeEntry, MonthSummary

RULES_PATH = Path(__file__).with_name("rules.yaml")
ACTIVITY_KINDS = ("work", "school", "volunteer")
QUALIFYING = ("qualifies", "qualifies_depends_on_basis")  # in order of preference
MONTH_ID = re.compile(r"\d{4}-\d{2}")

Status = Literal["qualifies", "qualifies_depends_on_basis", "below_floor", "above_ceiling"]


# ---------- rules

@dataclass(frozen=True)
class Rules:
    effective: date
    floor_hours: float
    floor_income_cents: int
    ceiling_cents: int
    low_confidence_threshold: float
    deposit_window_days: int
    deposit_tolerance_cents: int
    sources: tuple[dict, ...] = ()


CENTS_KEYS = ("floor_income_cents", "ceiling_cents", "deposit_tolerance_cents")


def load_rules(path: Path | str = RULES_PATH) -> Rules:
    raw = yaml.safe_load(Path(path).read_text())
    for key in CENTS_KEYS:
        if type(raw[key]) is not int:
            raise ValueError(f"{key} must be integer cents, got {raw[key]!r}")
    return Rules(
        effective=date.fromisoformat(str(raw["effective"])),
        floor_hours=raw["floor_hours"],
        floor_income_cents=raw["floor_income_cents"],
        ceiling_cents=raw["ceiling_cents"],
        low_confidence_threshold=raw["low_confidence_threshold"],
        deposit_window_days=raw["deposit_window_days"],
        deposit_tolerance_cents=raw["deposit_tolerance_cents"],
        sources=tuple(raw.get("sources") or ()),
    )


# ---------- small helpers shared with checks.py

def as_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def month_of(day: str | date) -> str:
    return f"{as_date(day):%Y-%m}"


def income_month(entry: IncomeEntry) -> str:
    """Income counts in the month its pay period ends (Mar 29-31, paid Apr 2, is March income)."""
    return month_of(entry.period_end)


def dollars(cents: int) -> str:
    """12345 -> '$123.45', using integer math only."""
    whole, part = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}${whole:,}.{part:02d}"


def hours_text(hours: float) -> str:
    return f"{hours:g}"


def month_label(month: str) -> str:
    """'2026-05' -> 'May 2026'."""
    return f"{as_date(month + '-01'):%B %Y}"


def join_words(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _cents_total(amounts: Iterable[int]) -> int:
    total = 0
    for amount in amounts:
        if type(amount) is not int:
            raise TypeError(f"money must be integer cents, got {amount!r}")
        total += amount
    return total


def hours_by_kind(activities: Iterable[ActivityEntry]) -> dict[str, float]:
    totals = dict.fromkeys(ACTIVITY_KINDS, 0.0)
    for a in activities:
        if a.kind not in totals:
            raise ValueError(f"{a.id}: unknown activity kind {a.kind!r}")
        totals[a.kind] += a.hours
    return {kind: round(h, 2) for kind, h in totals.items()}


# ---------- one month

def summarize_month(month: str, income: Sequence[IncomeEntry], activities: Sequence[ActivityEntry],
                    expenses: Sequence[Expense], rules: Rules) -> MonthSummary:
    """Judge one month under both readings of income. Rows from other months are ignored."""
    inc = [e for e in income if income_month(e) == month]
    act = [a for a in activities if a.month == month]
    exp = [x for x in expenses if x.month == month]

    gross = _cents_total(e.gross_cents for e in inc)
    spent = _cents_total(x.amount_cents for x in exp)
    net = gross - spent
    by_kind = hours_by_kind(act)
    hours = round(sum(by_kind.values()), 2)

    hours_ok = hours >= rules.floor_hours
    floor_gross = hours_ok or gross >= rules.floor_income_cents
    floor_net = hours_ok or net >= rules.floor_income_cents
    ceiling_gross = gross <= rules.ceiling_cents
    ceiling_net = net <= rules.ceiling_cents
    status = _status(floor_gross and ceiling_gross, floor_net and ceiling_net, ceiling_gross)

    reasons = [_hours_reason(by_kind, hours, rules), *_floor_reasons(gross, spent, net, rules),
               _ceiling_reason(gross, spent, net, rules)]
    if status == "qualifies_depends_on_basis":
        reasons.append(_basis_reason(gross_reading_ok=floor_gross and ceiling_gross))

    return MonthSummary(
        month=month, gross_cents=gross, expenses_cents=spent, net_cents=net, hours=hours,
        meets_floor_gross=floor_gross, meets_floor_net=floor_net,
        under_ceiling_gross=ceiling_gross, under_ceiling_net=ceiling_net,
        status=status, reasons=reasons,
        source_ids=sorted({row.document_id for row in (*inc, *act, *exp)}),
    )


def summarize_months(income: Sequence[IncomeEntry], activities: Sequence[ActivityEntry],
                     expenses: Sequence[Expense], rules: Rules) -> list[MonthSummary]:
    """Summaries for every month that has any evidence, oldest first."""
    months = ({income_month(e) for e in income} | {a.month for a in activities}
              | {x.month for x in expenses})
    return [summarize_month(m, income, activities, expenses, rules) for m in sorted(months)]


def _status(gross_ok: bool, net_ok: bool, under_ceiling_gross: bool) -> Status:
    if gross_ok and net_ok:
        return "qualifies"
    if gross_ok or net_ok:
        return "qualifies_depends_on_basis"
    # Neither reading works. Gross over the ceiling is reported first; the reasons give both readings.
    return "below_floor" if under_ceiling_gross else "above_ceiling"


def _hours_reason(by_kind: dict[str, float], hours: float, rules: Rules) -> str:
    route = f"the {hours_text(rules.floor_hours)}-hour route"
    if hours == 0:
        return f"No work, school or volunteer hours on file, so {route} is not met."
    parts = " + ".join(f"{hours_text(h)} {kind}" for kind, h in by_kind.items() if h)
    unit = "hour" if hours == 1 else "hours"
    verdict = "meets" if hours >= rules.floor_hours else "is short of"
    return f"{hours_text(hours)} {unit} ({parts}) {verdict} {route}."


def _floor_reasons(gross: int, spent: int, net: int, rules: Rules) -> list[str]:
    floor = rules.floor_income_cents
    route = f"the {dollars(floor)} income route"
    if spent == 0:
        return [f"Income of {dollars(gross)} {_meets(gross >= floor)} {route}."]
    return [
        f"Gross income of {dollars(gross)} {_meets(gross >= floor)} {route}.",
        f"Net income of {dollars(net)} ({dollars(gross)} gross minus {dollars(spent)} business expenses) "
        f"{_meets(net >= floor)} {route}.",
    ]


def _ceiling_reason(gross: int, spent: int, net: int, rules: Rules) -> str:
    ceiling = dollars(rules.ceiling_cents)
    under_gross, under_net = gross <= rules.ceiling_cents, net <= rules.ceiling_cents
    if spent == 0:
        return f"Income of {dollars(gross)} is {_under(under_gross)} the {ceiling} income ceiling."
    if under_gross == under_net:
        return (f"Gross ({dollars(gross)}) and net ({dollars(net)}) income are both "
                f"{_under(under_gross)} the {ceiling} income ceiling.")
    return (f"Gross income of {dollars(gross)} is {_under(under_gross)} the {ceiling} income ceiling; "
            f"net income of {dollars(net)} is {_under(under_net)} it.")


def _basis_reason(gross_reading_ok: bool) -> str:
    yes, no = ("gross", "net") if gross_reading_ok else ("net", "gross")
    return (f"Qualifies if the county counts {yes} income, not if it counts {no} income. "
            "The county decides which applies.")


def _meets(ok: bool) -> str:
    return "meets" if ok else "is below"


def _under(ok: bool) -> str:
    return "at or under" if ok else "above"


# ---------- picking the packet month

@dataclass(frozen=True)
class MonthChoice:
    month: Optional[str]
    explanation: str


def flag_touches(flag: Flag, summary: MonthSummary) -> bool:
    """A flag touches a month when it names the month or a document behind the month's numbers."""
    ids = set(flag.entity_ids)
    return summary.month in ids or not ids.isdisjoint(summary.source_ids)


def best_month(summaries: Sequence[MonthSummary], open_flags: Sequence[Flag]) -> MonthChoice:
    """Pick the month to build the packet on.

    Prefers "qualifies" over "qualifies_depends_on_basis", skips months touched by an open flag
    and breaks ties on the most recent month.
    """
    open_flags = [f for f in open_flags if f.status == "open"]
    qualifying = [s for s in summaries if s.status in QUALIFYING]
    blocked = [s for s in qualifying if any(flag_touches(f, s) for f in open_flags)]
    usable = [s for s in qualifying if s not in blocked]
    if not usable:
        return MonthChoice(None, _explain_none(summaries, blocked, open_flags))
    pick = max(usable, key=lambda s: (-QUALIFYING.index(s.status), s.month))
    return MonthChoice(pick.month, _explain_pick(pick, usable, blocked, open_flags))


def _explain_pick(pick: MonthSummary, usable: list[MonthSummary], blocked: list[MonthSummary],
                  open_flags: list[Flag]) -> str:
    name = month_label(pick.month)
    if pick.status == "qualifies":
        why = "it meets the requirement whether the county counts gross or net income"
    else:
        basis = "gross" if pick.meets_floor_gross and pick.under_ceiling_gross else "net"
        why = f"it meets the requirement only if the county counts {basis} income, which the county decides"
    lines = [f"Build the packet on {name}: {why}."]
    same = [s for s in usable if s.status == pick.status and s is not pick]
    weaker = [s for s in usable if s.status != pick.status]
    if same:
        lines.append(f"{_months(same)} also {_verb(same, 'qualif')}; {name} is more recent.")
    if weaker:
        lines.append(f"{_months(weaker)} {_verb(weaker, 'qualif')} under only one reading of income.")
    if blocked:
        lines.append(f"Skipped {_months(blocked)}: open questions touch {_pronoun(blocked)}.")
    lines += _open_questions_note(open_flags)
    return " ".join(lines)


def _explain_none(summaries: Sequence[MonthSummary], blocked: list[MonthSummary],
                  open_flags: list[Flag]) -> str:
    if not summaries:
        return "No month has any evidence yet."
    if blocked:
        lines = [f"No month can be used yet: {_months(blocked)} would qualify, but open questions "
                 f"touch {_pronoun(blocked)}."]
        return " ".join(lines + _open_questions_note(open_flags))
    status_text = {"below_floor": "below the floor", "above_ceiling": "above the income ceiling"}
    found = "; ".join(f"{month_label(s.month)} is {status_text[s.status]}" for s in summaries)
    return f"No month on file meets the requirement yet: {found}."


def _open_questions_note(open_flags: list[Flag]) -> list[str]:
    n = len(open_flags)
    if not n:
        return []
    return [f"{n} open question{'s' if n != 1 else ''} must be answered before the packet can be built."]


def _months(summaries: list[MonthSummary]) -> str:
    return join_words([month_label(s.month) for s in sorted(summaries, key=lambda s: s.month)])


def _verb(summaries: list[MonthSummary], stem: str) -> str:
    return f"{stem}ies" if len(summaries) == 1 else f"{stem}y"


def _pronoun(summaries: list[MonthSummary]) -> str:
    return "it" if len(summaries) == 1 else "them"
