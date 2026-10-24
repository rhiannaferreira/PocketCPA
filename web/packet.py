"""Proof packet: one printable page set that a caseworker can read.

It renders only from the coverage engine's MonthSummary rows and the evidence rows behind them,
never from model text, so every number on the page traces back to a ledger row and an image.
demo/pipeline.py builds its input.

    python3 -m web.packet out/packet.html      # Maria's SAMPLE packet (offline, no API calls)
    python3 -m web.packet out/packet.pdf       # same, as PDF (needs playwright)
"""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "demo" / "data" / "documents"

STATUS = {
    "qualifies": "Meets the requirement whether income is counted gross or after expenses",
    "qualifies_depends_on_basis": "Meets the requirement under one reading of income only",
    "below_floor": "Does not meet the requirement",
    "above_ceiling": "Income is above the Medi-Cal limit",
}


def money(c: int | None) -> str:
    if c is None:
        return "–"
    sign, c = ("−", -c) if c < 0 else ("", c)
    return f"{sign}${c // 100:,}.{c % 100:02d}"


def hours(h: float | None) -> str:
    return "–" if h is None else f"{round(h, 2):g} h"


def month_name(m: str) -> str:
    from datetime import datetime
    return datetime.strptime(m, "%Y-%m").strftime("%B %Y")


def render(p: dict, doc_base: str = "documents/") -> str:
    ev = p["evidence"]

    def link(eid: str) -> str:
        e = ev[eid]
        return f'<a href="{escape(doc_base + e["file"])}">{escape(e["document_id"])}</a>'

    blocks = []
    for m in p["packet_months"] + p.get("backup_months", []):
        s = p["months"][m]
        role = "Main month" if m in p["packet_months"] else "Backup month"
        rows = "".join(
            f"<tr><td>{escape(ev[i]['label'])}</td><td>{escape(ev[i]['period'])}</td>"
            f"<td class=num>{money(ev[i].get('amount_cents'))}</td><td class=num>{hours(ev[i].get('hours'))}</td>"
            f"<td>{escape(ev[i]['status'])}</td><td>{link(i)}</td></tr>" for i in s["evidence_ids"])
        blocks.append(f"""
<section class=month>
  <h2>{month_name(m)} <span class=tag>{role}</span></h2>
  <p class=how>{STATUS[s['status']]}</p>
  <ul>{''.join(f'<li>{escape(r)}</li>' for r in s['reasons'])}</ul>
  <table>
    <thead><tr><th>Evidence</th><th>Period</th><th class=num>Amount</th><th class=num>Hours</th><th>Status</th><th>Source</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p class=totals>Gross {money(s['gross_cents'])} · Expenses {money(s['expenses_cents'])} · After expenses {money(s['net_cents'])} · Hours {hours(s['hours'])}</p>
</section>""")

    summary = "".join(
        f"<tr><td>{month_name(s['month'])}</td><td>{STATUS[s['status']]}</td>"
        f"<td class=num>{money(s['gross_cents'])}</td><td class=num>{hours(s['hours'])}</td></tr>"
        for s in p["all_months"])
    warnings = "".join(f"<li>{escape(w)}</li>" for w in p.get("warnings", []))
    sources = "".join(f'<li>{escape(r["name"])}: <a href="{escape(r["url"])}">{escape(r["url"])}</a></li>'
                      for r in p["sources"])
    banner = '<div class=sample>SAMPLE · DEMO DATA · Not a real person or case</div>' if p.get("sample") else ""
    backup = (". Backup: " + ", ".join(month_name(m) for m in p["backup_months"])) if p.get("backup_months") else ""

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
<div class=note><b>The county decides.</b> This packet organizes {escape(p['person'].split()[0])}'s own records.
It is not an eligibility decision. Every number links to the document it came from, and "verified"
means it matched a bank deposit.</div>

<h2>Months on file</h2>
<table><thead><tr><th>Month</th><th>Result</th><th class=num>Gross</th><th class=num>Hours</th></tr></thead>
<tbody>{summary}</tbody></table>
<p>Main month: {", ".join(month_name(m) for m in p['packet_months'])}{backup}. {escape(p.get('explanation', ''))}</p>

{''.join(blocks)}
{f'<h2>Notes</h2><ul>{warnings}</ul>' if warnings else ''}
<h2>Rules used</h2>
<ul>{sources}</ul>
<footer>Generated {escape(p['generated_at'])}. Money is shown exactly as recorded, in dollars and cents.</footer>
</body></html>"""


def demo_packet() -> dict:
    """Maria's SAMPLE packet: simulated intake rows through the real Auditor and coverage engine."""
    from demo.pipeline import packet_input, run, simulated_intake
    return packet_input(run(simulated_intake(), answer_yes=True))


def render_file(packet: dict, out: Path, doc_base: str | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    html_path = out.with_suffix(".html")
    html_path.write_text(render(packet, doc_base=doc_base or DOCS.resolve().as_uri() + "/"))
    if out.suffix == ".pdf":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SystemExit(f"For PDF: pip install playwright && playwright install chromium. "
                             f"Or open {html_path} in a browser and use Print > Save as PDF.")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri())
            page.pdf(path=str(out), format="Letter", print_background=True)
            browser.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", help="output .html or .pdf")
    args = ap.parse_args()
    print(render_file(demo_packet(), Path(args.out)))


if __name__ == "__main__":
    main()
