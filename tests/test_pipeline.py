"""Maria's story end to end, offline: intake rows -> Auditor -> months -> packet month."""
from demo.pipeline import run, simulated_intake


def test_before_answers_the_duplicate_makes_march_look_like_it_qualifies():
    out = run(simulated_intake())
    march = next(s for s in out["summaries"] if s.month == "2026-03")
    assert march.gross_cents == 61200 and march.status == "qualifies"
    kinds = {f.kind for f in out["open_flags"]}
    assert {"duplicate", "hidden_instruction", "deposit_without_payout"} <= kinds
    assert "low_confidence" not in kinds and not out["ready"]


def test_after_maria_answers_march_drops_and_may_is_the_packet_month():
    out = run(simulated_intake(), answer_yes=True)
    months = {s.month: s for s in out["summaries"]}
    assert months["2026-03"].gross_cents == 53100 and months["2026-03"].status == "below_floor"
    assert months["2026-04"].hours == 82
    assert months["2026-05"].net_cents == 65000  # $58 mileage at the rules.yaml rate + $12 phone
    assert out["choice"].month == "2026-05" and out["ready"]
