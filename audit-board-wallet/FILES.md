# audit-board-wallet: what each file does

The audit board, the Wallet (card recommendations with CRS credit data), Nessie seeding, and a one-command demo reset for Pocket CPA. Needs Python 3.10 or newer.

It reads the team's sample data in `../demo/data` (Maria Delgado, Parcelo payouts, February to May 2026, money in cents), so the board, the proof packet and Nessie all tell the same story. `python demo.py reset` checks its totals against `answer_key.json` every time.

## Run it

```bash
cd audit-board-wallet
pip install -r requirements.txt
python demo.py reset                       # prints READY and "gas moment: OK"
python -m uvicorn server:app --port 8000   # board at http://127.0.0.1:8000
python demo.py replay                      # second terminal: play the whole story
```

## Code

| File | What it does |
|---|---|
| `server.py` | The board's web server. Streams events live to the page, with a polling fallback. Also the endpoints the page uses: pull CRS, ask the Wallet about a purchase, approve it, reset the demo. |
| `board.html` | The board itself: Verify, Prove and Save columns, counters, both Medi-Cal meters (80 hours or $580), card usage bars, and a "Try a purchase" box. No internet needed. |
| `core.py` | Shared plumbing: the frozen demo clock, the SQLite ledger, `emit()` to put a line on the board, and `post_event()` for agents running in another process. |
| `wallet.py` | Picks the best card for each purchase. Every card "bids" what it would earn; a card is held back if it would report over 30% of its limit near its statement date. Fixed-wording credit tips. Only recommends: nothing is charged until Maria approves, and each approval works once. |
| `crs.py` | CRS sandbox: login, Experian soft pull, and reading the card accounts from the report. Tries live, then the last saved response, then `cards.yaml`. The board shows which one ran. |
| `nessie.py` | Capital One Nessie (mock bank): creates a fresh Maria with her deposits and bills on each reset. |
| `demo.py` | The demo tool. `reset` loads the team's sample data and checks every month against the answer key, `replay` plays the story onto the board, `check3` runs it 3 times and confirms identical results. |

## Data and settings

| File | What it holds |
|---|---|
| `schema.sql` | Ledger tables in cents and minutes, matching `agents/intake.py`: entries, deposits, activities, expenses, flags, events, plus the Wallet's tables. |
| `cards.yaml` | Card reward rules with a source link for each, Maria's three cards, and the 30% ceiling. Rotating 5% categories are only verified for Q4 2026, so none apply in the June demo. Set `crs_apply: display_only` if live CRS numbers break the gas example. |
| `merchants.yaml` | Merchant name to category (gas, grocery, dining...). An estimate; issuers decide by merchant code. |
| `crs_persona.json` | CRS's published sandbox test identity. Not a real person. |
| `requirements.txt` | Extra Python packages this folder needs on top of the repo's own. |

## For the agents: putting a line on the board

```python
from core import post_event      # never raises if the board is down
post_event("auditor", "duplicate", "earn-2026-03-w2b is the same payout as w2a",
           {"entry_id": "earn-2026-03-w2b", "amount_cents": 8100}, "alert")
```

Or `POST /events` with JSON `{agent, kind, summary, payload, severity}`. Agents: intake, bookkeeper, auditor, benefits, wallet, crs, coordinator, system. Severity: info, ok, warn, alert. These drive the counters:

| agent / kind | payload keys |
|---|---|
| intake / extracted | amount_cents, hours |
| bookkeeper / match | amount_cents |
| any / duplicate, missing, deposit_without_payout, hidden_instruction | adds 1 to "caught" |
| benefits / progress | month (YYYY-MM), hours_verified, income_verified_cents, hours_needed, income_needed_cents |

## Environment variables (all optional)

| Variable | Use |
|---|---|
| `CRS_USERNAME`, `CRS_PASSWORD` | Live CRS pull |
| `NESSIE_API_KEY` | `python demo.py reset --nessie` |
| `BOARD_TOKEN` | Agents must send it as `X-Board-Token`. Set it whenever the board is reachable from a network or tunnel. |
| `BOARD_URL` | Where `post_event()` sends lines (default `http://127.0.0.1:8000`) |
| `DEMO_NOW` | Demo clock, default Jun 1, 2026 6pm PT (after Maria's sample months). `live` uses real time. |

## Honest labels

Maria's data is the team's simulated sample set, Nessie is a mock bank, and CRS uses a test identity. The board shows a "rehearsal replay" badge whenever lines come from `demo.py replay` instead of the real agents. Card tips are educational information, not financial advice.
