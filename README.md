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

## Coverage engine & Auditor (`core/corridor.py`, `core/checks.py`, `core/adapters.py`)

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

**From intake.** `activities_from_income` turns each payout's `active_minutes` into a work `ActivityEntry` (minutes ÷ 60, in the month the period ends, with its document id and confidence). The reasons call these hours "app-reported active time". `expenses_with_month` takes each expense's month from its date. Rows without a dollar amount are left out, and so is mileage: no mileage rate is applied. Both functions accept intake's dicts or objects with the same field names.

**Packet month.** `best_month` prefers `qualifies` over `qualifies_depends_on_basis`, skips any month an open `block` flag touches, and takes the most recent month on a tie. It explains its pick. `packet_ready(flags)` returns `(False, messages)` while any `block` flag is still open, so both functions follow the same rule.

**The Auditor.** Each check returns `Flag`s with one question for Maria.

| Check | Severity | Fires when |
| --- | --- | --- |
| `duplicate` | block | Same image hash, or same platform + overlapping period + same amount |
| `hidden_instruction` | block | Document text tries to instruct the app ("ignore previous", "system:", "set income to", ...). It is reported, never followed |
| `low_confidence` | block | Any value read below 0.8 confidence |
| `payout_without_deposit` | block | No deposit from the platform for the same amount (±$1.00) within 0 to 5 days after the period ends |
| `deposit_without_payout` | warn | A deposit matches no payout |

A flag blocks when leaving it open could make the packet claim something untrue: a doubled payout, a number we may have misread, or income we can't see landing in her bank. An unexplained deposit (such as Maria's $60 Zelle on April 19) may not be gig income, so it only warns. `list_warnings(flags, answers)` gives the packet a line for each warning with Maria's answer, so it is shown rather than hidden.

A flag's `entity_ids` are typed so ids can't collide: first the rows it is about (`doc:`, `inc:`, `act:`, `exp:`, `dep:`), then the months whose result it could change (`month:2026-03`). For a duplicate, the first id is the original we keep and the second is the copy. `resolve(flag, answer, ...)` takes Maria's yes or no and works on dataclasses or pydantic models. A confirmed duplicate is dropped along with its hours, and its month is recomputed: March goes from $612.00 to $531.00, which is below the floor.

```bash
python3 -m tests.run_cases       # pass/fail scoreboard over tests/cases/*.yaml
python3 -m tests.run_cases -v    # plus each month's reasons, the questions and the packet pick
```

Maria's cases are built from `demo/data` (SAMPLE data) through the adapters. The edge cases are inline in `tests/cases/02_edge_cases.yaml`.

**Known limits**

- A pay period that crosses two months is counted entirely in the month it ends.
- Exemptions (such as caretakers and medically frail people), the six-month income average for seasonal workers, and lookbacks of more than one month are not modeled. `best_month` picks a single month.
- Correcting a low-confidence value isn't supported yet. Answering "no" leaves the flag open.
- The `effective` date (2027-01-01) is not enforced, so the demo's 2026 months are judged under the 2027 rules.
- Intake's mileage logs come out in miles only. `demo/pipeline.py` applies `mileage_rate_cents_per_mile` from `rules.yaml` (IRS 2026, 72.5 cents) before the adapters, so May's 80 miles count as $58.00.

Sources:
- CMS, [Community engagement overview](https://www.medicaid.gov/medicaid/downloads/community-engagement-overview-slide.pdf) (Public Law 119-21, section 71119), and the [interim final rule, CMS-2454-IFC](https://www.federalregister.gov/documents/2026/06/03/2026-11094/medicaid-program-community-engagement-requirement-for-certain-individuals) (June 3, 2026)
- HHS ASPE, [2026 Poverty Guidelines](https://aspe.hhs.gov/sites/default/files/documents/b1bfa16b20ae9b89d525bc35de7c1643/detailed-guidelines-2026.pdf) (FPL chart)

## Proof packet (`web/packet.py`)

Renders the packet from the coverage engine's MonthSummary rows and the evidence rows behind them, so every number links to the image it came from. It shows every month on file, the main and backup months with the engine's reasons, a table of evidence per month, warnings with Maria's answers, the rules used with links, "the county decides" wording and a generation time.

```bash
python3 -m web.packet out/packet.html   # Maria's SAMPLE packet, offline
python3 -m web.packet out/packet.pdf    # needs: pip install playwright && playwright install chromium
```

## The whole story (`demo/pipeline.py`)

Documents, then intake, the Auditor, month summaries and the packet, in one run.

```bash
python3 -m demo.pipeline                          # offline: intake rows simulated from the answer key
python3 -m demo.pipeline --answer-yes out/p.html  # Maria answers every question, packet is built
python3 -m demo.pipeline --live --answer-yes out/p.html   # reads the sample images with the model (API key)
python3 -m demo.pipeline --cached --answer-yes out/p.html # reuses the last --live read, no API calls
```

Before Maria answers, March shows $612.00 and looks like it qualifies, with a blocking duplicate question. After she answers, March is $531.00 and below the floor, and the packet is built on May with April as the backup.
