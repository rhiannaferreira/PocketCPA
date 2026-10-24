-- Pocket CPA ledger. The four shared shapes (Entry, Deposit, Flag, Event) are the
-- contract between the two halves of the team. Change them only together.
PRAGMA journal_mode = WAL;

-- Entry: one payout read from an earnings screenshot by the intake agent. Money is integer cents,
-- time is the app's active minutes, matching agents/intake.py and demo/data/answer_key.json.
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY,               -- document id, e.g. 'earn-2026-03-w2a'
  source_file TEXT NOT NULL,         -- image path, so every number links to its source
  platform TEXT NOT NULL,            -- e.g. 'Parcelo'
  period_start TEXT NOT NULL, period_end TEXT NOT NULL,
  gross_cents INTEGER NOT NULL,
  active_minutes INTEGER,
  status TEXT NOT NULL DEFAULT 'unverified'  -- unverified | verified | duplicate | missing
);

-- Deposit: what actually landed in the bank (Nessie, or demo/data/nessie_seed.json offline).
CREATE TABLE IF NOT EXISTS deposits (
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  description TEXT NOT NULL,
  matched_entry_id TEXT              -- one deposit -> at most one entry
);

-- Activity: volunteer or school hours from a letter.
CREATE TABLE IF NOT EXISTS activities (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, month TEXT NOT NULL, hours REAL NOT NULL
);

-- Expense: stated business costs (mileage, phone share) that reduce net income.
CREATE TABLE IF NOT EXISTS expenses (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, category TEXT NOT NULL, amount_cents INTEGER NOT NULL
);

-- Flag: something the Auditor wants a human to look at.
CREATE TABLE IF NOT EXISTS flags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id TEXT, deposit_id TEXT,
  kind TEXT NOT NULL,                -- duplicate | missing | deposit_without_payout | hidden_instruction
  evidence TEXT NOT NULL,            -- JSON
  question TEXT,                     -- what the agent asks Maria
  resolved INTEGER NOT NULL DEFAULT 0
);

-- Event: one line on the audit board. Every agent writes these.
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  agent TEXT NOT NULL,               -- intake | bookkeeper | auditor | benefits | wallet | crs | system
  kind TEXT NOT NULL,                -- see CONTRACT.md
  severity TEXT NOT NULL DEFAULT 'info',  -- info | ok | warn | alert
  summary TEXT NOT NULL,             -- one human line for the board
  payload TEXT NOT NULL DEFAULT '{}' -- JSON details
);
CREATE INDEX IF NOT EXISTS events_id ON events(id);

-- Wallet state: one row per card Maria holds.
CREATE TABLE IF NOT EXISTS cards_state (
  card_id TEXT PRIMARY KEY,          -- matches cards.yaml catalog id
  limit_usd REAL NOT NULL,
  balance_usd REAL NOT NULL,
  statement_close_day INTEGER NOT NULL,
  rotating_activated INTEGER NOT NULL DEFAULT 0,
  carrying_balance INTEGER NOT NULL DEFAULT 0,  -- interest-bearing balance from last statement
  open_date TEXT,
  source TEXT NOT NULL               -- 'crs_live' | 'crs_cached' | 'cards.yaml'
);

-- Spend the Wallet routed, so caps and balances stay correct across picks.
CREATE TABLE IF NOT EXISTS wallet_spend (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, card_id TEXT NOT NULL, merchant TEXT NOT NULL,
  category TEXT NOT NULL, amount_usd REAL NOT NULL, reward_usd REAL NOT NULL
);

-- Recommendations waiting for Maria's OK. Confirm by id only; the server never trusts a
-- decision sent back by a browser.
CREATE TABLE IF NOT EXISTS pending_decisions (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, merchant TEXT NOT NULL, amount_usd REAL NOT NULL,
  category TEXT NOT NULL, winner TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY, v TEXT NOT NULL);
