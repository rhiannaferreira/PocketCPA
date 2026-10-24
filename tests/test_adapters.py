"""Adapters: intake-shaped rows become the work hours and monthly expenses the engine counts."""
from types import SimpleNamespace

import pytest

from core.adapters import activities_from_income, confidence_of, expenses_with_month
from core.corridor import load_rules, summarize_month
from tests.fixtures import CONFIDENCE, activity, answer_key, income

RULES = load_rules()


def field(value, conf=0.97):
    return {"shown": str(value), "region": [0.1, 0.2, 0.5, 0.25], "confidence": conf}


def intake_payout(start="2026-06-01", end="2026-06-07", minutes=360, conf=0.97):
    """Shaped like agents/intake.py's income rows: one confidence per field, none on the row."""
    return {"id": "earn-1-p1", "document_id": "earn-1", "platform": "Parcelo", "period_start": start,
            "period_end": end, "gross_cents": 8100, "active_minutes": minutes, "status": "unverified",
            "sources": {"period_start": field(start), "period_end": field(end), "gross_amount": field("81.00"),
                        "active_time": field(minutes, conf)}}


# ---------- work hours from payouts

def test_active_minutes_become_work_hours_with_their_source():
    [a] = activities_from_income([intake_payout()])
    assert (a.id, a.document_id, a.kind, a.month, a.hours) == ("earn-1-p1-work", "earn-1", "work", "2026-06", 6)
    assert a.confidence == 0.97


def test_intake_rows_take_the_lowest_field_confidence():
    [a] = activities_from_income([intake_payout(conf=0.6)])
    assert a.confidence == 0.6


def test_objects_with_a_confidence_carry_it_over():
    entry = SimpleNamespace(id="p1", document_id="d1", period_end="2026-06-07", active_minutes=90, confidence=0.91)
    [a] = activities_from_income([entry])
    assert (a.hours, a.confidence, a.document_id) == (1.5, 0.91, "d1")


def test_hours_count_in_the_month_the_period_ends():
    [a] = activities_from_income([intake_payout("2026-03-29", "2026-03-31", 420)])  # paid Apr 2
    assert a.month == "2026-03"


def test_a_payout_with_no_active_time_gives_no_hours():
    assert activities_from_income([intake_payout(minutes=None)]) == []


def test_a_row_with_no_confidence_at_all_is_refused():
    with pytest.raises(ValueError, match="no confidence"):
        confidence_of({"id": "x"})


def test_april_reaches_82_hours_through_the_adapter():
    key = answer_key()
    payouts = [{**e, "id": f"{e['document_id']}-p1", "confidence": CONFIDENCE} for e in key["income_entries"]]
    work = activities_from_income(payouts)
    volunteer = [activity("vol", a["month"], a["hours"], a["kind"], document_id=a["document_id"])
                 for a in key["activity_entries"]]
    inc = [income(p["id"], p["period_start"], p["period_end"], p["gross_cents"], document_id=p["document_id"])
           for p in payouts]
    apr = summarize_month("2026-04", inc, work + volunteer, [], RULES)
    assert (apr.hours, apr.status) == (82, "qualifies")
    assert apr.reasons[:2] == ["82 hours (58 work + 24 volunteer) meets the 80-hour route.",
                               "Work hours are app-reported active time."]


# ---------- expenses by month

def test_expenses_get_their_month_from_their_date():
    phone = {"id": "phone-e1", "document_id": "phone", "category": "phone", "date": "2026-05-31",
             "amount_cents": 1200, "bill_total_cents": 4000, "business_share": 0.3, "status": "stated",
             "sources": {"total_amount": field("40.00"), "business_share": field("30", 0.85)}}
    [x] = expenses_with_month([phone])
    assert (x.id, x.document_id, x.month, x.amount_cents, x.category, x.confidence) == (
        "phone-e1", "phone", "2026-05", 1200, "phone", 0.85)


def test_expenses_with_no_amount_or_no_date_are_left_out():
    mileage = {"id": "m-e1", "document_id": "m", "category": "mileage", "date": "2026-05-31", "miles": 80,
               "amount_cents": None, "sources": {"lines": [field(18), field(22)], "total_miles": field(80)}}
    undated = {"id": "r-e1", "document_id": "r", "category": "phone", "date": None, "amount_cents": 1200,
               "confidence": 0.9}
    assert expenses_with_month([mileage, undated]) == []


def test_mays_sample_expenses_come_through_in_may():
    rows = [{**x, "id": f"{x['document_id']}-e1", "confidence": CONFIDENCE} for x in answer_key()["expenses"]]
    assert sum(x.amount_cents for x in expenses_with_month(rows) if x.month == "2026-05") == 7000
