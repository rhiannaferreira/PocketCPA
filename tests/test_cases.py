"""Every case in tests/cases under pytest: the same cases, judged the same way, as the scoreboard."""
from dataclasses import replace

import pytest

from core.corridor import load_rules
from tests import run_cases
from tests.casekit import load_cases, run_case

RULES = load_rules()
CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_case(case):
    outcome = run_case(case, RULES)
    assert outcome.passed, f"expected {outcome.expected}, got {outcome.got}"


def test_a_wrong_expectation_fails():
    may = next(c for c in CASES if c.name.startswith("May"))
    outcome = run_case(replace(may, expect={**may.expect, "status": "below_floor"}), RULES)
    assert not outcome.passed and outcome.got.startswith("qualifies")


def test_answering_a_flag_that_is_not_there_fails():
    feb = next(c for c in CASES if c.name.startswith("Feb"))
    outcome = run_case(replace(feb, answers=[{"kind": "low_confidence", "answer": "yes"}]), RULES)
    assert not outcome.passed and outcome.got == "no open low_confidence flag to answer"


def test_scoreboard_prints_every_case_and_exits_zero(capsys):
    assert run_cases.main([]) == 0
    out = capsys.readouterr().out
    assert f"{len(CASES)}/{len(CASES)} cases passed" in out and "SAMPLE DATA" in out
    assert out.count("PASS") == len(CASES) and "FAIL" not in out
