"""The Auditor: checks that turn suspicious evidence into Flags, each with one question for Maria.

Pure functions over ledger rows: no network, no model calls. A check never changes a value; it
only asks. Every open flag blocks the packet; severity only orders them on the board.

A flag's entity_ids list the rows it is about, then the month(s) (YYYY-MM) whose result it could
change, so best_month can skip those months. For a duplicate, entity_ids[0] is the original we
keep and entity_ids[1] the suspected copy (an income entry, or a whole document for a repeated
image). Flag ids are built from the rows, so re-running the checks gives the same ids.

Documents are data, never instructions: hidden_instruction reports instruction-like text, and
nothing here acts on what a document says.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Iterable, Mapping, Sequence, Union

from core.corridor import (MONTH_ID, Rules, as_date, dollars, hours_text, income_month, month_label,
                           month_of, summarize_month)
from core.schemas import ActivityEntry, Deposit, Document, Expense, Flag, IncomeEntry, MonthSummary

Row = Union[IncomeEntry, ActivityEntry, Expense]

INSTRUCTION_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\bignore (all |any )?(the )?(previous|prior|above|earlier)\b",
    r"\b(ignore|disregard|forget|override)\b.{0,30}\b(instructions?|prompts?|rules|checks|guidelines)\b",
    r"\byou are (now )?(an? |the )?(ai|assistant|chatbot|bot|language model|llm|gpt|claude)\b",
    r"\b(system|assistant|developer)\s*:",
    r"\bset (the |my |her |his )?(income|earnings|gross|net|hours|total|status)\b.{0,30}\bto\b",
    r"\bmark\b.{0,40}\b(verified|approved|eligible|qualified|compliant)\b",
    r"\b(skip|bypass|disable)\b.{0,30}\b(checks?|audits?|verification|review)\b",
    r"\b(automated|ai) (assistants?|systems?|agents?|models?|reviewers?)\b",
    r"\bdo not (ask|question|flag|verify)\b",
    r"\bsend\b.{0,60}\b(routing number|account number|bank account|password|social security)",
))
YES, NO = {"yes", "y", "true"}, {"no", "n", "false"}


# ---------- helpers

def _flag(kind: str, severity: str, key_ids: Sequence[str], months: Iterable[str], message: str,
          question: str) -> Flag:
    return Flag(id=f"{kind}:{':'.join(key_ids)}", kind=kind, severity=severity, message=message,
                entity_ids=[*key_ids, *sorted(set(months))], status="open", question_for_user=question)


def row_month(row: Row) -> str:
    return row.month if hasattr(row, "month") else income_month(row)


def day_label(day) -> str:
    d = as_date(day)
    return f"{d:%b} {d.day}"


def period_label(start, end) -> str:
    s, e = as_date(start), as_date(end)
    return f"{day_label(s)}–{e.day}" if (s.year, s.month) == (e.year, e.month) else f"{day_label(s)}–{day_label(e)}"


def _payout_label(e: IncomeEntry) -> str:
    return f"{dollars(e.gross_cents)} {e.platform} payout for {period_label(e.period_start, e.period_end)}"


# ---------- duplicate

def duplicates(documents: Sequence[Document], income: Sequence[IncomeEntry],
               activities: Sequence[ActivityEntry] = (), expenses: Sequence[Expense] = ()) -> list[Flag]:
    """Same image uploaded twice, or the same payout (platform, overlapping period, amount) twice."""
    rows = [*income, *activities, *expenses]
    flags, copied_docs = [], set()
    for original, copy in _same_image_pairs(documents):
        copied_docs.add(frozenset((original.id, copy.id)))
        flags.append(_flag(
            "duplicate", "block", [original.id, copy.id], {row_month(r) for r in rows if r.document_id == copy.id},
            f"{copy.id} is the same image as {original.id}.",
            "These two uploads are the same picture; did you upload it twice by mistake?"))

    order = {d.id: i for i, d in enumerate(sorted(documents, key=lambda d: (d.uploaded_at, d.id)))}
    entries = sorted(income, key=lambda e: (order.get(e.document_id, len(order)), e.id))
    for i, copy in enumerate(entries):
        original = next((o for o in entries[:i] if _same_payout(o, copy)), None)
        if original is None or frozenset((original.document_id, copy.document_id)) in copied_docs:
            continue
        flags.append(_flag(
            "duplicate", "block", [original.id, copy.id], [income_month(copy)],
            f"{copy.id} repeats {original.id}: same platform, overlapping dates, same amount.",
            f"Is the {_payout_label(copy)} the same payout uploaded twice?"))
    return flags


def _same_image_pairs(documents: Sequence[Document]) -> list[tuple[Document, Document]]:
    by_hash: dict[str, list[Document]] = {}
    for d in sorted(documents, key=lambda d: (d.uploaded_at, d.id)):
        if d.image_hash:
            by_hash.setdefault(d.image_hash, []).append(d)
    return [(group[0], d) for group in by_hash.values() for d in group[1:]]


def _same_payout(a: IncomeEntry, b: IncomeEntry) -> bool:
    return (a.platform.casefold() == b.platform.casefold() and a.gross_cents == b.gross_cents
            and as_date(a.period_start) <= as_date(b.period_end)
            and as_date(b.period_start) <= as_date(a.period_end))


# ---------- payouts vs bank deposits

def payout_without_deposit(income: Sequence[IncomeEntry], deposits: Sequence[Deposit], rules: Rules) -> list[Flag]:
    """A payout with no deposit from the same platform, same amount (±tolerance), 0-N days after the period."""
    return [_flag(
        "payout_without_deposit", "warn", [e.id], [income_month(e)],
        f"No {e.platform} deposit of about {dollars(e.gross_cents)} arrived within "
        f"{rules.deposit_window_days} days after {day_label(e.period_end)}.",
        f"We couldn't find a bank deposit for your {_payout_label(e)}; was it paid somewhere else, "
        "like a debit card or another account?")
        for e in income if not any(_pays(d, e, rules) for d in deposits)]


def deposit_without_payout(income: Sequence[IncomeEntry], deposits: Sequence[Deposit], rules: Rules) -> list[Flag]:
    """A deposit that matches no payout on file. It could be unreported pay for the month."""
    flags = []
    for d in deposits:
        if any(_pays(d, e, rules) for e in income):
            continue
        day = as_date(d.date)
        months = {month_of(day), month_of(day - timedelta(days=rules.deposit_window_days))}
        flags.append(_flag(
            "deposit_without_payout", "warn", [d.id], months,
            f"The {dollars(d.amount_cents)} deposit on {day_label(day)} ({d.description}) matches no payout on file.",
            f"Is the {dollars(d.amount_cents)} deposit on {day_label(day)} ({d.description}) something other "
            "than pay for work, like a gift or a refund?"))
    return flags


def _pays(d: Deposit, e: IncomeEntry, rules: Rules) -> bool:
    lag = (as_date(d.date) - as_date(e.period_end)).days
    return (0 <= lag <= rules.deposit_window_days
            and abs(d.amount_cents - e.gross_cents) <= rules.deposit_tolerance_cents
            and _from_platform(d, e.platform))


def _from_platform(d: Deposit, platform: str) -> bool:
    p = platform.casefold()
    return bool(p) and (p in d.source.casefold() or p in d.description.casefold())


# ---------- low confidence

def low_confidence(income: Sequence[IncomeEntry], activities: Sequence[ActivityEntry],
                   expenses: Sequence[Expense], rules: Rules) -> list[Flag]:
    threshold = rules.low_confidence_threshold
    return [_flag(
        "low_confidence", "warn", [r.id], [row_month(r)],
        f"{r.id} was read with {r.confidence:.0%} confidence, below the {threshold:.0%} threshold.",
        f"We read {_describe(r)} but aren't sure; is that right?")
        for r in (*income, *activities, *expenses) if r.confidence < threshold]


def _describe(r: Row) -> str:
    if hasattr(r, "gross_cents"):
        return f"{dollars(r.gross_cents)} for your {r.platform} payout for {period_label(r.period_start, r.period_end)}"
    if hasattr(r, "hours"):
        return f"{hours_text(r.hours)} {r.kind} hours for {month_label(r.month)}"
    return f"a {dollars(r.amount_cents)} {r.category} expense for {month_label(r.month)}"


# ---------- hidden instructions

def hidden_instruction(document_id: str, raw_text: str, months: Iterable[str] = ()) -> list[Flag]:
    """Flag text in a document that tries to instruct the app. The text is reported, never followed."""
    found = list(dict.fromkeys(m.group(0) for p in INSTRUCTION_PATTERNS for m in p.finditer(raw_text)))
    if not found:
        return []
    quoted = "; ".join(f'"{x[:60]}"' for x in found[:3])
    return [_flag(
        "hidden_instruction", "block", [document_id], months,
        f"{document_id} contains text that tries to instruct the app ({quoted}); it was ignored and changed nothing.",
        "This document contains text that tries to give the app instructions, which we ignored; "
        "is it a real document you received?")]


# ---------- everything

def run_all_checks(documents: Sequence[Document], income: Sequence[IncomeEntry],
                   activities: Sequence[ActivityEntry], expenses: Sequence[Expense],
                   deposits: Sequence[Deposit], raw_texts: Mapping[str, str], rules: Rules,
                   resolved_ids: Iterable[str] = ()) -> list[Flag]:
    """Run every check. Flags whose ids Maria already resolved come back resolved, not reopened."""
    rows = [*income, *activities, *expenses]
    flags = [
        *duplicates(documents, income, activities, expenses),
        *payout_without_deposit(income, deposits, rules),
        *deposit_without_payout(income, deposits, rules),
        *low_confidence(income, activities, expenses, rules),
    ]
    for doc_id, text in raw_texts.items():
        flags += hidden_instruction(doc_id, text, {row_month(r) for r in rows if r.document_id == doc_id})
    done = set(resolved_ids)
    return [replace(f, status="resolved") if f.id in done else f for f in flags]


# ---------- Maria answers

@dataclass(frozen=True)
class Resolution:
    flag: Flag
    income: list[IncomeEntry]
    activities: list[ActivityEntry]
    expenses: list[Expense]
    summaries: list[MonthSummary]  # the months the flag touches, recomputed


def resolve(flag: Flag, answer: str | bool, income: Sequence[IncomeEntry], activities: Sequence[ActivityEntry],
            expenses: Sequence[Expense], rules: Rules) -> Resolution:
    """Apply Maria's yes/no answer and recompute the months the flag touches.

    Every question is worded so that "yes" means the evidence stands, so yes resolves any flag.
    For a duplicate, yes drops the copy and no keeps both; either resolves it. For other kinds,
    no leaves the flag open until the value is corrected. Afterwards, re-run run_all_checks with
    the resolved ids: flags on rows that were dropped disappear.
    """
    if flag.status != "open":
        raise ValueError(f"{flag.id} is already {flag.status}")
    yes = parse_answer(answer)
    if flag.kind == "duplicate" and yes:
        income, activities, expenses = drop_copy(flag.entity_ids[1], income, activities, expenses)
    status = "resolved" if yes or flag.kind == "duplicate" else "open"
    months = [m for m in flag.entity_ids if MONTH_ID.fullmatch(m)]
    return Resolution(replace(flag, status=status), list(income), list(activities), list(expenses),
                      [summarize_month(m, income, activities, expenses, rules) for m in months])


def parse_answer(answer: str | bool) -> bool:
    if isinstance(answer, bool):
        return answer
    a = answer.strip().casefold()
    if a in YES or a in NO:
        return a in YES
    raise ValueError(f"answer must be yes or no, got {answer!r}")


def drop_copy(copy_id: str, income: Sequence[IncomeEntry], activities: Sequence[ActivityEntry],
              expenses: Sequence[Expense]) -> tuple[list[IncomeEntry], list[ActivityEntry], list[Expense]]:
    """Remove a confirmed copy: one income entry, or every row of a repeated document.

    A document left with no income was only the copy, so its work hours go too.
    """
    kept = [e for e in income if copy_id not in (e.id, e.document_id)]
    gone = {copy_id} | ({e.document_id for e in income} - {e.document_id for e in kept})
    return (kept,
            [a for a in activities if a.id != copy_id and a.document_id not in gone],
            [x for x in expenses if x.id != copy_id and x.document_id not in gone])
