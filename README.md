# PocketCPA
## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # then put your key in .env (never committed)
python3 -m pytest              # offline tests
```

## Intake (`agents/intake.py`)

Reads a photo of a document and returns ledger rows. Step 1 identifies the type (earnings, receipt, mileage, school, volunteer, county letter). Step 2 makes one vision call that returns JSON for that type. Every value keeps the text as printed, where it is on the image and a confidence score. Code converts values to cents, minutes and dates, and the model never adds anything up.

- Rows come out `unverified` (income) or `stated` (expenses). The Bookkeeper verifies them against deposits.
- Output that fails the schema, an unreadable value, or a refusal becomes a blocking `extraction_failed` flag. Nothing is guessed.
- A value below 0.8 confidence becomes a `low_confidence` flag with a question for Maria.
- Text in a document that tries to instruct the agents becomes a blocking `hidden_instruction` flag and is never followed.
- Mileage comes out in miles. The dollar amount uses the rate in `rules.yaml`.

```bash
python3 -m agents.intake demo/data/documents/earn-2026-03-w2a.png   # one document
python3 -m tests.check_intake_live                                    # all sample docs vs the answer key (uses the API)
```

## Coverage engine & Auditor (`core/corridor.py`, `core/checks.py`)

Pure Python with no network calls and no model calls. Money is integer cents, and every threshold lives in `core/rules.yaml`. The code decides; documents never do.

**The rule.** Starting January 1, 2027, Medi-Cal adults 19 to 64 must show one of these for the month(s) the county looks at:

- 80 or more hours of work, school and volunteering combined, or
- income of at least $580.00 (the $7.25 federal minimum wage × 80 hours).

They must also stay at or under the income ceiling: $1,836.00 a month for a single adult in 2026. That is 138% of the $15,960 poverty guideline divided by 12, which comes to $1,835.40, and DHCS rounds it up.

**Open question: gross or net?** For self-employed people it isn't settled whether the $580 floor and the ceiling use gross income or income after business expenses. `summarize_month` computes both and never picks one:

- `qualifies`: met under both readings
- `qualifies_depends_on_basis`: met under one reading only, and the reasons say which
- `below_floor` or `above_ceiling`

The county decides which reading applies. Our output only says what the evidence shows, and it never suggests earning less to stay eligible. Each `MonthSummary` carries plain-English `reasons` and the `source_ids` (document ids) behind its numbers. Income counts in the month its pay period ends.

**Packet month.** `best_month` prefers `qualifies` over `qualifies_depends_on_basis`, skips any month an open flag touches, and takes the most recent month on a tie. It explains its pick.

**The Auditor.** Each check returns `Flag`s with one question for Maria. Every open flag blocks the packet.

| Check | Fires when |
| --- | --- |
| `duplicate` | Same image hash, or same platform + overlapping period + same amount |
| `payout_without_deposit` | No deposit from the platform for the same amount (±$1.00) within 0 to 5 days after the period ends |
| `deposit_without_payout` | A deposit matches no payout |
| `low_confidence` | Any value read below 0.8 confidence |
| `hidden_instruction` | Document text tries to instruct the app ("ignore previous", "system:", "set income to", ...). It is reported, never followed |

A flag's `entity_ids` list the rows it is about, then the months (`YYYY-MM`) whose result it could change. For a duplicate, the first id is the original we keep and the second is the copy. `resolve(flag, answer, ...)` takes Maria's yes or no. A confirmed duplicate is dropped and its month recomputed: March goes from $612.00 to $531.00, which is below the floor.

```bash
python3 -m tests.run_cases       # pass/fail scoreboard over tests/cases/*.yaml
python3 -m tests.run_cases -v    # plus each month's reasons, the questions and the packet pick
```

Maria's cases are built from `demo/data` (SAMPLE data). The edge cases are inline in `tests/cases/02_edge_cases.yaml`.

Sources:
- CMS, [Community engagement overview](https://www.medicaid.gov/medicaid/downloads/community-engagement-overview-slide.pdf) (Public Law 119-21, section 71119), and the [interim final rule, CMS-2454-IFC](https://www.federalregister.gov/documents/2026/06/03/2026-11094/medicaid-program-community-engagement-requirement-for-certain-individuals) (June 3, 2026)
- HHS ASPE, [2026 Poverty Guidelines](https://aspe.hhs.gov/sites/default/files/documents/b1bfa16b20ae9b89d525bc35de7c1643/detailed-guidelines-2026.pdf) (FPL chart)

## Proof packet (`web/packet.py`)

Renders the packet from MonthSummary rows and evidence entries only, so every number links to the image it came from. It shows the main and backup months, how each one meets the test, a table of evidence per month, the rules used with links, "the county decides" wording and a generation time.

```bash
python3 -m web.packet --demo out/packet.html   # stand-in data from demo/data/answer_key.json
python3 -m web.packet --demo out/packet.pdf    # needs: pip install playwright && playwright install chromium
python3 -m web.packet --input packet.json out/packet.pdf
```

`demo_packet()` builds stand-in input from the answer key. Once the coverage engine produces MonthSummary rows, pass those in with `--input` instead.
