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

## Proof packet (`web/packet.py`)

Renders the packet from MonthSummary rows and evidence entries only, so every number links to the image it came from. It shows the main and backup months, how each one meets the test, a table of evidence per month, the rules used with links, "the county decides" wording and a generation time.

```bash
python3 -m web.packet --demo out/packet.html   # stand-in data from demo/data/answer_key.json
python3 -m web.packet --demo out/packet.pdf    # needs: pip install playwright && playwright install chromium
python3 -m web.packet --input packet.json out/packet.pdf
```

`demo_packet()` builds stand-in input from the answer key. Once the coverage engine produces MonthSummary rows, pass those in with `--input` instead.
