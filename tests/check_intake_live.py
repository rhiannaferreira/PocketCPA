"""Run intake on every sample document with the real model and compare to the answer key.

Needs ANTHROPIC_API_KEY. Costs a little per run (two calls per document).

    python3 -m tests.check_intake_live
"""
import json
from pathlib import Path

from agents import intake

DATA = Path(__file__).resolve().parent.parent / "demo" / "data"


def main():
    key = json.loads((DATA / "answer_key.json").read_text())
    expected = {e["document_id"]: e for e in key["income_entries"]}
    ok = bad = 0
    for doc in key["documents"]:
        r = intake.process(DATA / doc["file"], doc_id=doc["id"])
        problems = []
        if r.document["kind"] != doc["kind"]:
            problems.append(f"kind {r.document['kind']} != {doc['kind']}")
        if doc["id"] in expected:
            want = expected[doc["id"]]
            got = r.income_entries[0] if r.income_entries else {}
            for k in ("period_start", "period_end", "gross_cents", "active_minutes"):
                if got.get(k) != want[k]:
                    problems.append(f"{k} {got.get(k)} != {want[k]}")
        if doc.get("expected_flag") and not any(f["check"] == doc["expected_flag"] for f in r.flags):
            problems.append(f"missing flag {doc['expected_flag']}")
        flags = ", ".join(f["check"] for f in r.flags) or "none"
        print(f"{'OK ' if not problems else 'BAD'} {doc['id']:<24} flags: {flags}  {'; '.join(problems)}")
        ok, bad = (ok + 1, bad) if not problems else (ok, bad + 1)
    print(f"\n{ok} matched, {bad} did not")


if __name__ == "__main__":
    main()
