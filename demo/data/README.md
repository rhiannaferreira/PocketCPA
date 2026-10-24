# Maria's sample data (SAMPLE · demo data)

Every file here is made up for the demo. Maria Delgado, the Parcelo app, the food bank, the phone company and the county letter are all fictional. Regenerate with `python3 demo/make_sample_docs.py demo/data`.

## What the documents show

| Month | Earnings screenshots | Verified | Other documents | Expected result |
| --- | --- | --- | --- | --- |
| Feb 2026 | 4 weeks, $480.00, 34 h | $480.00 | none | Below the floor |
| Mar 2026 | 6 screenshots, $612.00 (week of Mar 8 appears twice) | $531.00, 40 h | none | Below the floor once the duplicate is removed |
| Apr 2026 | 4 weeks, $560.00, 58 h | $560.00 | volunteer letter, 24 h | Qualifies on hours (58 + 24 = 82) |
| May 2026 | 4 weeks, $720.00, 54 h | $720.00 | mileage log (80 mi), phone bill ($12 business share) | Qualifies on income: $720 gross, $650 net |

Mileage uses the IRS 2026 business rate, 72.5 cents a mile ([IRS](https://www.irs.gov/newsroom/irs-sets-2026-business-standard-mileage-rate-at-725-cents-per-mile-up-25-cents)): 80 × $0.725 = $58.00. Phone: 30% of a $40.00 bill = $12.00. Total May expenses: $70.00.

## Files

- `documents/earn-2026-MM-wN.png`: one weekly earnings screenshot per payout period. `earn-2026-03-w2a.png` and `earn-2026-03-w2b.png` are the same week cropped differently, so their image hashes differ (test case 2).
- `documents/volunteer-2026-04.png`, `mileage-2026-05.png`, `phone-2026-05.png`.
- `documents/county-letter-renewal.png`: renewal notice with a hidden instruction in faint small print at the bottom (test case 6). It must be flagged and never followed.
- `nessie_seed.json`: one deposit per payout (paid 2 days after the period ends), four purchases, and one unexplained $60.00 Zelle deposit on Apr 19 (test case 4).
- `answer_key.json`: expected extracted values for every document, month totals in integer cents, and the flags the Auditor should raise.

## Things to watch

- `earn-2026-03-w5` covers Mar 29 to 31 but is paid on Apr 2. Count it in March, by its period.
- `earn-2026-04-w1` and `earn-2026-04-w4` are both $135.00 in different weeks. They are not duplicates (test case 9).
