"""Coverage corridor tests: thresholds, both readings of income, reasons and the packet month."""
import re
from dataclasses import replace

import pytest

from core.corridor import RULES_PATH, best_month, dollars, load_rules, summarize_month, summarize_months
from core.schemas import Flag
from tests.fixtures import activity, expense, income, maria

RULES = load_rules()
JUNE = ("2026-06-01", "2026-06-30")


def june(gross_cents=0, expenses_cents=0, work_hours=0.0, **activities):
    inc = [income("i1", *JUNE, gross_cents)] if gross_cents else []
    exp = [expense("x1", "2026-06", expenses_cents)] if expenses_cents else []
    act = [activity("a-work", "2026-06", work_hours)] if work_hours else []
    act += [activity(f"a-{kind}", "2026-06", h, kind) for kind, h in activities.items()]
    return summarize_month("2026-06", inc, act, exp, RULES)


def open_flag(*entity_ids):
    return Flag("f1", "low_confidence", "warn", "test", list(entity_ids), "open", "Is it right?")


# ---------- rules

def test_every_threshold_comes_from_rules_yaml():
    assert (RULES.floor_hours, RULES.floor_income_cents, RULES.ceiling_cents) == (80, 58000, 183600)
    assert (RULES.low_confidence_threshold, RULES.deposit_window_days) == (0.8, 5)
    assert str(RULES.effective) == "2027-01-01"
    assert any("FPL" in s["name"] or "Poverty" in s["name"] for s in RULES.sources)
    assert any("ommunity engagement" in s["name"] for s in RULES.sources)


def test_rules_reject_money_that_is_not_integer_cents(tmp_path):
    text = RULES_PATH.read_text().replace("floor_income_cents: 58000", "floor_income_cents: 580.0")
    (tmp_path / "rules.yaml").write_text(text)
    with pytest.raises(ValueError, match="integer cents"):
        load_rules(tmp_path / "rules.yaml")


def test_float_money_in_a_row_is_refused():
    with pytest.raises(TypeError, match="integer cents"):
        summarize_month("2026-06", [income("i1", *JUNE, 480.0)], [], [], RULES)


def test_dollars_formats_cents_without_floats():
    assert dollars(183600) == "$1,836.00" and dollars(5) == "$0.05" and dollars(-7000) == "-$70.00"


# ---------- Maria's months (SAMPLE)

@pytest.fixture(scope="module")
def maria_months():
    m = maria()
    return {s.month: s for s in summarize_months(m.income, m.activities, m.expenses, RULES)}


def test_maria_february_is_below_the_floor(maria_months):
    feb = maria_months["2026-02"]
    assert (feb.status, feb.gross_cents, feb.hours) == ("below_floor", 48000, 34)
    assert feb.reasons[0] == "34 hours (34 work) is short of the 80-hour route."
    assert "Income of $480.00 is below the $580.00 income route." in feb.reasons


def test_maria_march_as_uploaded_double_counts_the_week_of_march_8(maria_months):
    # The repeated $81 week pushes March over $580. Only the Auditor's duplicate flag stops this.
    mar = maria_months["2026-03"]
    assert (mar.gross_cents, mar.status) == (61200, "qualifies")


def test_maria_april_qualifies_on_hours(maria_months):
    apr = maria_months["2026-04"]
    assert apr.status == "qualifies" and apr.gross_cents == 56000
    assert apr.reasons[0] == "82 hours (58 work + 24 volunteer) meets the 80-hour route."
    assert apr.source_ids[-1] == "volunteer-2026-04"


def test_maria_may_qualifies_on_income_under_both_readings(maria_months):
    may = maria_months["2026-05"]
    assert (may.gross_cents, may.expenses_cents, may.net_cents) == (72000, 7000, 65000)
    assert may.status == "qualifies" and may.meets_floor_gross and may.meets_floor_net
    assert ("Net income of $650.00 ($720.00 gross minus $70.00 business expenses) meets the $580.00 "
            "income route.") in may.reasons
    assert {"mileage-2026-05", "phone-2026-05"} <= set(may.source_ids)


def test_maria_best_month_is_may(maria_months):
    duplicate_week = open_flag("earn-2026-03-w2a-p1", "earn-2026-03-w2b-p1", "2026-03")
    choice = best_month(list(maria_months.values()), [duplicate_week])
    assert choice.month == "2026-05"
    assert choice.explanation.startswith("Build the packet on May 2026: it meets the requirement whether")
    assert "April 2026 also qualifies; May 2026 is more recent." in choice.explanation
    assert "Skipped March 2026: open questions touch it." in choice.explanation


# ---------- thresholds, both readings

def test_income_exactly_at_the_floor_meets_it():
    s = june(58000)
    assert s.meets_floor_gross and s.status == "qualifies"


def test_one_cent_short_of_the_floor_does_not():
    assert june(57999).status == "below_floor"


def test_income_exactly_at_the_ceiling_is_under_it():
    s = june(183600)
    assert s.under_ceiling_gross and s.status == "qualifies"
    assert "Income of $1,836.00 is at or under the $1,836.00 income ceiling." in s.reasons


def test_one_dollar_over_the_ceiling_is_above_it():
    s = june(183700)
    assert not s.under_ceiling_gross and s.status == "above_ceiling"
    assert "Income of $1,837.00 is above the $1,836.00 income ceiling." in s.reasons


def test_hours_do_not_override_the_ceiling():
    assert june(200000, work_hours=90).status == "above_ceiling"


def test_exactly_80_hours_across_kinds_meets_the_hours_route():
    s = june(30000, work_hours=50, school=30)
    assert s.status == "qualifies"
    assert s.reasons[0] == "80 hours (50 work + 30 school) meets the 80-hour route."


def test_fractional_hours_add_up_without_float_drift():
    act = [activity(f"a{i}", "2026-06", 80 / 60) for i in range(60)]  # 60 x 1.333... = 80
    assert summarize_month("2026-06", [], act, [], RULES).hours == 80


def test_gross_meets_but_net_fails_depends_on_basis():
    s = june(60000, expenses_cents=5000, work_hours=20)
    assert (s.net_cents, s.meets_floor_gross, s.meets_floor_net) == (55000, True, False)
    assert s.status == "qualifies_depends_on_basis"
    assert s.reasons[-1] == ("Qualifies if the county counts gross income, not if it counts net income. "
                             "The county decides which applies.")


def test_expenses_bringing_net_under_the_ceiling_depends_on_basis():
    s = june(190000, expenses_cents=20000)
    assert (s.under_ceiling_gross, s.under_ceiling_net) == (False, True)
    assert s.status == "qualifies_depends_on_basis"
    assert s.reasons[-1].startswith("Qualifies if the county counts net income, not if it counts gross")


def test_income_counts_in_the_month_its_period_ends():
    late_march = income("w5", "2026-03-29", "2026-03-31", 9600)  # paid Apr 2
    assert summarize_month("2026-03", [late_march], [], [], RULES).gross_cents == 9600
    assert summarize_month("2026-04", [late_march], [], [], RULES).gross_cents == 0


def test_unknown_activity_kinds_are_refused():
    with pytest.raises(ValueError, match="unknown activity kind"):
        summarize_month("2026-06", [], [activity("a1", "2026-06", 5, "gaming")], [], RULES)


def test_no_reason_ever_suggests_earning_less():
    advice = re.compile(r"earn less|work less|fewer hours|reduce|lower your|cut back|stay under|keep .* under", re.I)
    for gross in (0, 48000, 58000, 183600, 183700, 400000):
        for spent in (0, 7000, 50000):
            for h in (0, 34, 80):
                s = june(gross, spent, h)
                assert not any(advice.search(r) for r in s.reasons), s.reasons
    m = maria()
    assert not advice.search(best_month(summarize_months(m.income, m.activities, m.expenses, RULES), []).explanation)


# ---------- best_month

def test_best_month_prefers_qualifies_over_depends_on_basis_even_if_older():
    older = replace(june(70000), month="2026-06")
    newer = replace(june(60000, 5000), month="2026-07")
    choice = best_month([older, newer], [])
    assert choice.month == "2026-06"
    assert "July 2026 qualifies under only one reading of income." in choice.explanation


def test_best_month_breaks_ties_on_the_most_recent_month():
    months = [replace(june(70000), month=m) for m in ("2026-04", "2026-06", "2026-05")]
    assert best_month(months, []).month == "2026-06"


def test_best_month_skips_a_month_named_by_an_open_flag():
    months = [replace(june(70000), month=m) for m in ("2026-05", "2026-06")]
    choice = best_month(months, [open_flag("i1", "2026-06")])
    assert choice.month == "2026-05"
    assert "Skipped June 2026: open questions touch it." in choice.explanation
    assert "1 open question must be answered before the packet can be built." in choice.explanation


def test_best_month_skips_a_month_whose_document_is_flagged():
    may = replace(june(70000), month="2026-05", source_ids=["doc-may"])
    jun = replace(june(70000), month="2026-06", source_ids=["doc-june"])
    assert best_month([may, jun], [open_flag("doc-june")]).month == "2026-05"


def test_resolved_flags_do_not_block_a_month():
    resolved = replace(open_flag("2026-06"), status="resolved")
    assert best_month([june(70000)], [resolved]).month == "2026-06"


def test_best_month_returns_none_when_nothing_qualifies():
    choice = best_month([june(48000), replace(june(183700), month="2026-07")], [])
    assert choice.month is None
    assert choice.explanation == ("No month on file meets the requirement yet: June 2026 is below the floor; "
                                  "July 2026 is above the income ceiling.")


def test_best_month_returns_none_when_every_qualifying_month_is_flagged():
    choice = best_month([june(70000)], [open_flag("2026-06")])
    assert choice.month is None and "June 2026 would qualify, but open questions touch it" in choice.explanation
