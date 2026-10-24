"""Load the case files in tests/cases, run the engine on each case, and compare expected with got.

Shared by tests/run_cases.py (the scoreboard) and tests/test_cases.py (pytest), so both always
judge the same cases the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

from core.checks import resolve, run_all_checks
from core.corridor import (Rules, as_date, best_month, dollars, flag_touches, hours_text, month_label,
                           summarize_month, summarize_months)
from core.schemas import Flag
from tests.fixtures import Evidence, activity, deposit, document, expense, income, maria

CASES_DIR = Path(__file__).resolve().parent / "cases"
DATASETS = {"maria_sample": maria}
SUMMARY_KEYS = ("status", "gross_cents", "expenses_cents", "net_cents", "hours", "meets_floor_gross",
                "meets_floor_net", "under_ceiling_gross", "under_ceiling_net")
FLAG_KEYS = ("flags", "month_flags")


@dataclass
class Case:
    name: str
    evidence: Evidence
    expect: dict
    answers: list[dict] = field(default_factory=list)


@dataclass
class Outcome:
    case: Case
    expected: str
    got: str
    passed: bool
    details: list[str]


# ---------- loading

def load_suites(directory: Path = CASES_DIR) -> list[tuple[str, list[Case]]]:
    """(title, cases) for every case file, in file-name order."""
    suites = []
    for path in sorted(directory.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())
        cases = [Case(c["name"], _evidence(spec.get("dataset"), c), c["expect"], c.get("answers", []))
                 for c in spec["cases"]]
        suites.append((spec["title"], cases))
    return suites


def load_cases(directory: Path = CASES_DIR) -> list[Case]:
    return [case for _, cases in load_suites(directory) for case in cases]


def _evidence(dataset: Optional[str], spec: dict) -> Evidence:
    ev = DATASETS[dataset]() if dataset else Evidence()
    ev.income += [income(**r) for r in spec.get("income", [])]
    ev.activities += [activity(**r) for r in spec.get("activities", [])]
    ev.expenses += [expense(**r) for r in spec.get("expenses", [])]
    deposits = spec.get("deposits", [])
    ev.deposits += _matching_deposits(ev) if deposits == "matching" else [deposit(**d) for d in deposits]
    ev.raw_texts.update(spec.get("raw_texts", {}))
    ev.documents += [document(**d) for d in spec.get("documents", [])]
    known = {d.id for d in ev.documents}
    for row in (*ev.income, *ev.activities, *ev.expenses):
        if row.document_id not in known:
            ev.documents.append(document(row.document_id))
            known.add(row.document_id)
    return ev


def _matching_deposits(ev: Evidence) -> list:
    """One bank deposit two days after each payout, so a case only shows the flags it is about."""
    return [deposit(f"dep-{e.id}", as_date(e.period_end) + timedelta(days=2), e.gross_cents,
                    f"{e.platform.upper()} PAYOUT") for e in ev.income]


# ---------- running

def run_case(case: Case, rules: Rules) -> Outcome:
    ev, flags, resolved = case.evidence, _checks(case.evidence, rules, ()), set()
    for step in case.answers:
        flag = next((f for f in flags if f.kind == step["kind"] and f.status == "open"), None)
        if flag is None:
            return _fail(case, f"no open {step['kind']} flag to answer")
        r = resolve(flag, step["answer"], ev.income, ev.activities, ev.expenses, rules)
        ev = replace(ev, income=r.income, activities=r.activities, expenses=r.expenses)
        if r.flag.status == "resolved":
            resolved.add(flag.id)
        flags = _checks(ev, rules, resolved)

    open_flags = [f for f in flags if f.status == "open"]
    month = case.expect.get("month")
    summary = summarize_month(str(month), ev.income, ev.activities, ev.expenses, rules) if month else None
    choice = best_month(summarize_months(ev.income, ev.activities, ev.expenses, rules), open_flags)
    want = {k: _normal(k, v) for k, v in case.expect.items() if k != "month"}
    got = {k: _actual(k, summary, open_flags, choice.month) for k in want}

    details = list(summary.reasons) if summary else []
    if any(k in want for k in FLAG_KEYS):
        shown = [f for f in open_flags if summary is None or flag_touches(f, summary)]
        details += [f"{f.kind}: {f.question_for_user}" for f in shown]
    if "best_month" in want:
        details.append(choice.explanation)
    return Outcome(case, _show_all(want), _show_all(got), want == got, details)


def _checks(ev: Evidence, rules: Rules, resolved_ids) -> list[Flag]:
    return run_all_checks(ev.documents, ev.income, ev.activities, ev.expenses, ev.deposits, ev.raw_texts,
                          rules, resolved_ids)


def _actual(key: str, summary, open_flags: list[Flag], packet_month: Optional[str]) -> Any:
    if key in SUMMARY_KEYS:
        return getattr(summary, key)
    if key == "month_flags":
        return sorted(f.kind for f in open_flags if flag_touches(f, summary))
    if key == "flags":
        return sorted(f.kind for f in open_flags)
    if key == "best_month":
        return packet_month
    raise KeyError(f"unknown expectation {key!r}")


def _normal(key: str, value: Any) -> Any:
    if key in FLAG_KEYS:
        return sorted(value)
    return str(value) if key == "best_month" and value is not None else value


def _fail(case: Case, why: str) -> Outcome:
    return Outcome(case, _show_all({k: _normal(k, v) for k, v in case.expect.items() if k != "month"}),
                   why, False, [])


# ---------- display

def _show_all(values: dict) -> str:
    return " · ".join(_show(k, v) for k, v in values.items())


def _show(key: str, value: Any) -> str:
    if value is None and key != "best_month":
        return f"{key} missing"
    if key.endswith("_cents"):
        return f"{key.removesuffix('_cents')} {dollars(value)}"
    if key == "hours":
        return f"{hours_text(value)} h"
    if key in FLAG_KEYS:
        return "flags: " + (", ".join(value) or "none")
    if key == "best_month":
        return f"packet: {month_label(value) if value else 'none'}"
    if key in BOOL_TEXT:
        return BOOL_TEXT[key][bool(value)]
    return str(value)


BOOL_TEXT = {
    "meets_floor_gross": ("gross below floor", "gross meets floor"),
    "meets_floor_net": ("net below floor", "net meets floor"),
    "under_ceiling_gross": ("gross over ceiling", "gross under ceiling"),
    "under_ceiling_net": ("net over ceiling", "net under ceiling"),
}
