# TEMP: Anusha owns this file, replace with hers
"""Minimal stand-in for the shared data contract. Only the agreed fields, so merging is easy.

Money is integer cents. Dates are ISO strings (YYYY-MM-DD), months are YYYY-MM.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class Document:
    id: str
    kind: str
    platform: Optional[str]
    image_hash: str
    uploaded_at: str


@dataclass
class IncomeEntry:
    id: str
    document_id: str
    platform: str
    period_start: str
    period_end: str
    gross_cents: int
    confidence: float


@dataclass
class ActivityEntry:
    id: str
    document_id: str
    kind: Literal["work", "school", "volunteer"]
    month: str
    hours: float
    confidence: float


@dataclass
class Expense:
    id: str
    document_id: str
    month: str
    amount_cents: int
    category: str
    confidence: float


@dataclass
class Deposit:
    id: str
    source: str
    date: str
    amount_cents: int
    description: str


@dataclass
class Flag:
    id: str
    kind: str
    severity: str
    message: str
    entity_ids: list[str]
    status: Literal["open", "resolved"]
    question_for_user: str


@dataclass
class MonthSummary:
    month: str
    gross_cents: int
    expenses_cents: int
    net_cents: int
    hours: float
    meets_floor_gross: bool
    meets_floor_net: bool
    under_ceiling_gross: bool
    under_ceiling_net: bool
    status: str
    reasons: list[str]
    source_ids: list[str]
