"""Adapters: turn rows as intake reads them into the rows the coverage engine counts.

Pure functions. Each input row can be a dict (intake's output) or an object with the same field
names. No mileage rate is applied anywhere, so an expense without a dollar amount is left out.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable

from core.corridor import month_of
from core.schemas import ActivityEntry, Expense


def work_activity_id(income_id: str) -> str:
    """The id of the work hours derived from one payout, so dropping the payout drops them too."""
    return f"{income_id}-work"


def activities_from_income(income_entries: Iterable[Any]) -> list[ActivityEntry]:
    """One work ActivityEntry per payout, from the app's active time (active_minutes / 60).

    The month is the month the pay period ends, as for the income itself. The document id and
    confidence are carried over. A payout with no active time gives no hours.
    """
    return [ActivityEntry(id=work_activity_id(_get(e, "id")), document_id=_get(e, "document_id"), kind="work",
                          month=month_of(_get(e, "period_end")), hours=_get(e, "active_minutes") / 60,
                          confidence=confidence_of(e))
            for e in income_entries if _get(e, "active_minutes") is not None]


def expenses_with_month(expenses: Iterable[Any]) -> list[Expense]:
    """Expenses with their month taken from their date.

    Rows with no date or no dollar amount are left out. That includes intake's mileage rows,
    which come out in miles only.
    """
    return [Expense(id=_get(x, "id"), document_id=_get(x, "document_id"), month=month_of(_get(x, "date")),
                    amount_cents=_get(x, "amount_cents"), category=_get(x, "category"),
                    confidence=confidence_of(x))
            for x in expenses if _get(x, "date") and _get(x, "amount_cents") is not None]


def confidence_of(row: Any) -> float:
    """The row's confidence, or for intake's rows (one confidence per field) the lowest of those."""
    if _get(row, "confidence") is not None:
        return _get(row, "confidence")
    found = [s["confidence"] for s in _field_sources(_get(row, "sources") or {})]
    if not found:
        raise ValueError(f"{_get(row, 'id')}: no confidence")
    return min(found)


def _field_sources(sources: Mapping) -> Iterable[Mapping]:
    for value in sources.values():
        yield from (value if isinstance(value, list) else [value])


def _get(row: Any, key: str) -> Any:
    return row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)
