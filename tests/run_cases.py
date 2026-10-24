"""Scoreboard for the coverage engine and Auditor: every case in tests/cases, expected vs got.

    python3 -m tests.run_cases       # the scoreboard
    python3 -m tests.run_cases -v    # plus each month's reasons, the flags' questions and the packet pick

Exits 1 if any case fails.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):  # also allow `python3 tests/run_cases.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.corridor import load_rules  # noqa: E402
from tests.casekit import Outcome, load_suites, run_case  # noqa: E402

TITLE = "Pocket CPA · Coverage engine & Auditor"
SAMPLE_NOTE = "SAMPLE DATA: Maria Delgado, Parcelo and every amount are fictional."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pass/fail scoreboard for tests/cases.")
    parser.add_argument("-v", "--verbose", action="store_true", help="show reasons, questions and the packet pick")
    args = parser.parse_args(argv)
    rules = load_rules()
    suites = [(title, [run_case(c, rules) for c in cases]) for title, cases in load_suites()]
    width = shutil.get_terminal_size((120, 40)).columns
    print(render(suites, width=width, verbose=args.verbose, color=_use_color()))
    return 0 if all(o.passed for _, outcomes in suites for o in outcomes) else 1


def render(suites: list[tuple[str, list[Outcome]]], width: int = 120, verbose: bool = False,
           color: bool = False) -> str:
    paint = _painter(color)
    outcomes = [o for _, group in suites for o in group]
    cols = (max(len(o.case.name) for o in outcomes), max(len(o.expected) for o in outcomes),
            max(len(o.got) for o in outcomes))
    wide = 5 + sum(cols) + 6 + 4 <= width
    lines = [paint(TITLE, "1"), paint(SAMPLE_NOTE, "2"), ""]
    n = 0
    for title, group in suites:
        lines.append(paint(title, "1"))
        if wide:
            lines.append(paint(f"{'#':>3}  {'Case':<{cols[0]}}  {'Expected':<{cols[1]}}  {'Got':<{cols[2]}}  Result", "2"))
        for o in group:
            n += 1
            mark = paint("PASS", "32") if o.passed else paint("FAIL", "31;1")
            if wide:
                lines.append(f"{n:>3}  {o.case.name:<{cols[0]}}  {o.expected:<{cols[1]}}  {o.got:<{cols[2]}}  {mark}")
            else:
                lines += [f"{n:>3}  {o.case.name:<{cols[0]}}  {mark}",
                          paint(f"     expected  {o.expected}", "2"), f"     got       {o.got}"]
            if verbose:
                lines += [paint(f"       - {d}", "2") for d in o.details]
        lines.append("")
    passed = sum(o.passed for o in outcomes)
    total = f"{passed}/{len(outcomes)} cases passed"
    lines.append(paint(total, "32;1" if passed == len(outcomes) else "31;1"))
    return "\n".join(lines)


def _use_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _painter(color: bool):
    return (lambda text, code: f"\033[{code}m{text}\033[0m") if color else (lambda text, code: text)


if __name__ == "__main__":
    sys.exit(main())
