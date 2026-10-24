"""Proof packet: one printable page set that a caseworker can read.

It renders only from MonthSummary rows and evidence entries, never from model
text, so every number on the page traces back to a ledger row and an image.

    python3 -m web.packet --demo out/packet.html      # stand-in data from the answer key
    python3 -m web.packet --demo out/packet.pdf       # same, as PDF (needs playwright)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOOR_CENTS = 58000
FLOOR_HOURS = 80
CEILING_CENTS = 183600

HOW = {
    "income": "Meets the income test",
    "hours": "Meets the hours test",
    "combination": "Meets the hours test with work and volunteer or school hours combined",
    "none": "Does not meet the test",
}


def money(c: int | None) -> str:
    if c is None:
        return "–"
    sign, c = ("−", -c) if c < 0 else ("", c)
    return f"{sign}${c // 100:,}.{c % 100:02d}"


def hours(h: float | None) -> str:
    if h is None:
        return "–"
    return f"{h:g} h"


def month_name(m: str) -> str:
    return datetime.strptime(m, "%Y-%m").strftime("%B %Y")


def render(packet: dict, doc_base: str = "documents/") -> str:
    """packet = {person, generated_at, packet_months, backup_months, months: {YYYY-MM: summary},
    evidence: {id: entry}, rules: {...}, sample: bool}"""
    p, ev = packet, packet["evidence"]
    used = p["packet_months"] + p.get("backup_months", [])

    def link(eid: str) -> str:
        e = ev[eid]
        return f'<a href="{escape(doc_base + e["file"])}">{escape(e["document_id"])}</a>'

    month_blocks = []
    for m in used:
        s = p["months"][m]
        role = "Main month" if m in p["packet_months"] else "Backup month"
        rows = []
        for eid in s["evidence_ids"]:
            e = ev[eid]
            rows.append(
                f"<tr><td>{escape(e['label'])}</td><td>{escape(e['period'])}</td>"
                f"<td class=num>{money(e.get('amount_cents'))}</td><td class=num>{hours(e.get('hours'))}</td>"
                f"<td>{escape(e['status'])}</td><td>{link(eid)}</td></tr>"
            )
        tests = []
        if s["floor_met_by"] == "income":
            basis = "gross" if p["rules"]["floor_income_basis"] == "gross" else "after expenses"
            counted = s["gross_verified_cents"] if basis == "gross" else s["net_cents"]
            tests.append(f"Income counted ({basis}): <b>{money(counted)}</b>, at least {money(FLOOR_CENTS)}.")
        if s["floor_met_by"] in ("hours", "combination"):
            parts = " + ".join(f"{hours(v)} {k}" for k, v in s["hours_by_kind"].items() if v)
            tests.append(f"Hours: {parts} = <b>{hours(s['hours_verified'])}</b>, at least {FLOOR_HOURS} h.")
        tests.append(f"Income is under the {money(CEILING_CENTS)} monthly limit."
                     if not s["above_ceiling"] else
                     f"<b>Income is above the {money(CEILING_CENTS)} monthly limit.</b>")
        month_blocks.append(f"""
<section class=month>
  <h2>{month_name(m)} <span class=tag>{role}</span></h2>
  <p class=how>{HOW[s['floor_met_by']]}</p>
  <ul>{''.join(f'<li>{t}</li>' for t in tests)}</ul>
  <table>
    <thead><tr><th>Evidence</th><th>Period</th><th class=num>Amount</th><th class=num>Hours</th><th>Status</th><th>Source</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p class=totals>Gross verified {money(s['gross_verified_cents'])} · Expenses {money(s.get('expenses_cents'))} · After expenses {money(s.get('net_cents'))} · Hours verified {hours(s['hours_verified'])}</p>
</section>""")

    sources = "".join(f'<li>{escape(r["name"])}: <a href="{escape(r["url"])}">{escape(r["url"])}</a></li>'
                      for r in p["rules"]["sources"])
    summary = "".join(
        f"<tr><td>{month_name(m)}</td><td>{HOW[s['floor_met_by']]}</td>"
        f"<td class=num>{money(s['gross_verified_cents'])}</td><td class=num>{hours(s['hours_verified'])}</td></tr>"
        for m, s in sorted(p["months"].items()))
    banner = '<div class=sample>SAMPLE · DEMO DATA · Not a real person or case</div>' if p.get("sample") else ""

    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<title>Medi-Cal proof packet · {escape(p['person'])}</title>
<style>
  @page {{ size: Letter; margin: 0.6in; }}
  body {{ font: 11pt/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1a1a; margin: 0; }}
  h1 {{ font-size: 20pt; margin: 0 0 4px; }}
  h2 {{ font-size: 14pt; margin: 0 0 4px; }}
  .meta {{ color: #555; margin: 0 0 14px; }}
  .sample {{ background: #fde8e8; color: #9b1c1c; font-weight: 600; padding: 6px 10px; margin-bottom: 14px; }}
  .note {{ border-left: 4px solid #127a60; background: #eef7f4; padding: 8px 12px; margin: 12px 0 18px; }}
  .month {{ border: 1px solid #ddd; border-radius: 6px; padding: 12px 14px; margin: 0 0 16px; break-inside: avoid; }}
  .tag {{ font-size: 9pt; font-weight: 600; background: #127a60; color: #fff; padding: 2px 8px; border-radius: 10px; vertical-align: middle; }}
  .how {{ font-weight: 600; color: #127a60; margin: 2px 0 4px; }}
  ul {{ margin: 4px 0 10px; padding-left: 20px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 9.5pt; }}
  th, td {{ text-align: left; padding: 4px 6px; border-bottom: 1px solid #e5e5e5; }}
  th {{ background: #f5f5f5; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .totals {{ color: #444; font-size: 9.5pt; margin: 6px 0 0; }}
  a {{ color: #0b5cad; }}
  footer {{ color: #666; font-size: 9pt; margin-top: 18px; }}
</style></head>
<body>
{banner}
<h1>Medi-Cal renewal: proof of work or income</h1>
<p class=meta>{escape(p['person'])} · Prepared {escape(p['generated_at'])} by Pocket CPA</p>
<div class=note><b>The county decides.</b> This packet organizes Maria's own records. It is not an eligibility decision.
Every number links to the document it came from, and "verified" means it matched a bank deposit.</div>

<h2>Months included</h2>
<table><thead><tr><th>Month</th><th>Result</th><th class=num>Gross verified</th><th class=num>Hours verified</th></tr></thead>
<tbody>{summary}</tbody></table>
<p>Main month: {", ".join(month_name(m) for m in p['packet_months'])}{". Backup: " + ", ".join(month_name(m) for m in p.get('backup_months', [])) if p.get('backup_months') else ""}.</p>

{''.join(month_blocks)}

<h2>Rules used</h2>
<ul>{sources}</ul>
<footer>Generated {escape(p['generated_at'])}. Income counted for the {money(FLOOR_CENTS)} test: {escape(p['rules']['floor_income_basis'])}. Money is shown exactly as recorded, in dollars and cents.</footer>
</body></html>"""


def demo_packet() -> dict:
    """Stand-in input built from the answer key until the coverage engine's MonthSummary rows exist."""
    key = json.loads((ROOT / "demo" / "data" / "answer_key.json").read_text())
    files = {d["id"]: d["file"].removeprefix("documents/") for d in key["documents"]}
    evidence, months = {}, {}
    for m, s in key["month_summaries"].items():
        months[m] = {"gross_verified_cents": s["gross_verified_cents"], "expenses_cents": s.get("expenses_cents", 0),
                     "net_cents": s.get("net_cents", s["gross_verified_cents"] - s.get("expenses_cents", 0)),
                     "hours_by_kind": {"work": s["work_hours_verified"], "volunteer": s.get("volunteer_hours", 0)},
                     "evidence_ids": []}
    for e in key["income_entries"]:
        if e["expected_status"] != "verified":
            continue
        eid = "ev-" + e["document_id"]
        evidence[eid] = {"document_id": e["document_id"], "file": files[e["document_id"]],
                         "label": f"{e['platform']} weekly earnings", "period": f"{e['period_start']} to {e['period_end']}",
                         "amount_cents": e["gross_cents"], "hours": round(e["active_minutes"] / 60, 2),
                         "status": "verified"}
        months[e["period_start"][:7]]["evidence_ids"].append(eid)
    for a in key["activity_entries"]:
        eid = "ev-" + a["document_id"]
        evidence[eid] = {"document_id": a["document_id"], "file": files[a["document_id"]],
                         "label": "Volunteer letter", "period": a["month"], "hours": a["hours"],
                         "status": "stated (letter)"}
        months[a["month"]]["evidence_ids"].append(eid)
    for x in key["expenses"]:
        eid = "ev-" + x["document_id"]
        evidence[eid] = {"document_id": x["document_id"], "file": files[x["document_id"]],
                         "label": f"Expense: {x['category']}", "period": x["date"][:7],
                         "amount_cents": -x["amount_cents"], "status": x["expected_status"]}
        months[x["date"][:7]]["evidence_ids"].append(eid)
    for m, s in months.items():
        s["hours_verified"] = round(sum(s["hours_by_kind"].values()), 2)
        s["above_ceiling"] = s["gross_verified_cents"] > CEILING_CENTS
        if s["gross_verified_cents"] >= FLOOR_CENTS:
            s["floor_met_by"] = "income"
        elif s["hours_by_kind"]["work"] >= FLOOR_HOURS:
            s["floor_met_by"] = "hours"
        elif s["hours_verified"] >= FLOOR_HOURS:
            s["floor_met_by"] = "combination"
        else:
            s["floor_met_by"] = "none"
    return {
        "person": "Maria Delgado", "sample": True,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "packet_months": ["2026-05"], "backup_months": ["2026-04"],
        "months": months, "evidence": evidence,
        "rules": {"floor_income_basis": "gross", "sources": [
            {"name": "IRS 2026 business mileage rate, 72.5 cents a mile", "url": key["mileage_rate_source"]},
            {"name": "Medi-Cal work requirement ($580 a month or 80 hours): replace with the DHCS link from rules.yaml",
             "url": "https://www.dhcs.ca.gov/"},
        ]},
    }


def to_pdf(html_path: Path, pdf_path: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(f"For PDF: pip install playwright && playwright install chromium. "
                         f"Or open {html_path} in a browser and use Print > Save as PDF.")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri())
        page.pdf(path=str(pdf_path), format="Letter", print_background=True)
        browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", help="output .html or .pdf")
    ap.add_argument("--demo", action="store_true", help="use stand-in data from the answer key")
    ap.add_argument("--input", help="packet JSON (months, evidence, rules)")
    args = ap.parse_args()
    packet = demo_packet() if args.demo else json.loads(Path(args.input).read_text())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    docs = (ROOT / "demo" / "data" / "documents").resolve().as_uri() + "/"
    html_path = out.with_suffix(".html")
    html_path.write_text(render(packet, doc_base=docs))
    if out.suffix == ".pdf":
        to_pdf(html_path, out)
    print(out)


if __name__ == "__main__":
    main()
