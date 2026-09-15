# NSE Delivery + Volume Scanner

A small Python scanner that finds NSE stocks with **delivery and volume spikes** in a completed market session and pushes the shortlist to Telegram.

A stock makes the shortlist when **all** of these are true:
- Closed positive (`% change > 0`)
- Today's **delivery quantity** > 10,000 shares
- Today's **delivery times** (today's deliv qty ÷ 20-day avg) ≥ 3.0
- Close **above 200-day EMA**
- Today's volume ≥ **3× yesterday's** volume

All thresholds are configurable via CLI flags.

## Setup

```bash
pip install -r requirements.txt
```

### Telegram bot

1. In Telegram, open **@BotFather**, send `/newbot`, save the token.
2. Send your bot any message ("hi") to activate the chat.
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and grab your `chat.id`.
4. Set environment variables:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-xyz..."
export TELEGRAM_CHAT_ID="123456789"
```

(Or put them in `config.yaml`, but env vars are recommended for safety.)

## Run

```bash
# Default thresholds (matches README defaults above)
python delivery_scanner.py

# Preview the report without sending to Telegram
python delivery_scanner.py --dry-run

# Stricter filter — delivery 5× and volume 5× previous day
python delivery_scanner.py --min-delivery-times 5 --min-vol-ratio-1d 5

# Force re-download of bhavcopies (if cached data looks stale)
python delivery_scanner.py --no-cache
```

Run at **10:30 PM IST** for the same day's completed market session. NSE publishes
bhavcopy/delivery as end-of-day reports; publication and download availability are
not guaranteed at a fixed minute. The scanner requires the requested date's file,
validates its internal `DATE1`, and never substitutes an earlier session.

The price, change, delivery and volume filters run on NSE data first, so only
qualifying symbols need Yahoo history. This reduces bulk-fetch throttling.

The verified NSE bhavcopy supplies the session's open/high/low/close, percentage
change, traded quantity and delivery. The previous NSE session supplies the volume
comparison. Yahoo supplies older chart history for EMAs and breakouts; it must
reach the preceding available NSE session. A missing or intraday latest Yahoo bar
cannot delay the report or replace NSE's final values. History that does not reach the preceding session is skipped, and incomplete
coverage is shown. Internal gaps in older Yahoo history are not calendar-validated.

Before market close, a missing report, or insufficient delivery history produces
**data pending**, not a misleading zero-setup result. Weekends are skipped; a
weekday with no file is reported as unavailable/possibly a market holiday.
No exchange holiday calendar is assumed.

For an explicit replay (including after midnight), choose the session:

```bash
python delivery_scanner.py --as-of 2026-09-15 --dry-run
```

The header always identifies the **market-session date**, not the server date.
Default selection is today's date in `Asia/Kolkata`.

### All CLI options

| Flag | Default | What it does |
|---|---|---|
| `--universe` | `nifty500.csv` | CSV with a `symbol` column |
| `--min-pct-change` | `0.0` | Close must be up by at least this % |
| `--min-delivery-qty` | `10000` | Minimum delivery quantity (shares) |
| `--min-delivery-times` | `3.0` | Today's delivery ÷ N-day avg, minimum |
| `--delivery-lookback` | `20` | Days used for the delivery-times average |
| `--min-vol-ratio-1d` | `3.0` | Today's volume ÷ yesterday's, minimum |
| `--no-above-200ema` | _off_ | Disable the 200 EMA trend filter |
| `--top-n` | `25` | How many top candidates to send |
| `--dry-run` | _off_ | Print report instead of sending to Telegram |
| `--no-cache` | _off_ | Re-download all bhavcopies |
| `--as-of` | Today in IST | Explicit market-session date `YYYY-MM-DD` |

## Daily automation

**Azure is the only automatic scheduler:** `DailyScanner` in
`nse-scanner-4e104b` runs weekdays at **22:30 IST / 17:00 UTC** using NCRONTAB
`0 0 17 * * 1-5`. It sends the same-day completed-session report to Telegram.
A market holiday or unavailable data produces a status message rather than old signals.

`.github/workflows/scanner.yml` is **manual-only**, preventing a second morning
alert. In the Actions tab, optionally enter an `as_of` date. Preview/dry-run is on
by default; switch it off to send using the existing Telegram repository secrets.
The manual workflow uses the full `nse_equity.csv` universe.

Deploy the code and timer together (GitHub changes alone do not deploy Azure):

```bash
func azure functionapp publish nse-scanner-4e104b --build remote --python \
  --subscription 4ce9d3d4-989f-4f92-b751-d0d649e7de1a
```

After deployment, inspect the Azure function's timer binding to verify the new
schedule. `DELIVERY_LOOKBACK` controls the prior-session delivery average (20 by
default); the same setting is available as `--delivery-lookback` in the CLI.

Run regression tests locally:

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

### Local cron alternative (host timezone must be Asia/Kolkata)

Use this only if Azure automation is disabled:

```cron
30 22 * * 1-5  cd /path/to/scanner && /usr/bin/python3 delivery_scanner.py
```

## Project layout

```
.
├── delivery_scanner.py            # main script
├── config.yaml                    # Telegram creds (env vars override)
├── nifty500.csv                   # universe — column: symbol
├── requirements.txt
├── README.md
├── .github/workflows/scanner.yml  # daily automation
└── swingscanner/
    ├── config.py                  # YAML loader + env-var override
    ├── universe.py                # symbol list loader
    ├── nse_data.py                # bhavcopy + delivery-ratio math
    └── ohlc.py                    # OHLC + EMAs (yfinance bulk fetch)
```

## Caveats

- **Educational only.** Not investment advice. Do your own fundamental check (revenue growth, debt, promoter holding, cash flow) before entering any position.
- **Yahoo Finance occasionally throttles** — the script bulk-fetches in batches of 100 to minimize this, but a single rerun usually clears any flake.
- **First run downloads ~25 bhavcopies (~10 MB cached).** Daily reruns only fetch the new day.
- **Coverage:** the scanner is only as good as your `nifty500.csv` universe. Add or remove symbols freely — the only requirement is a `symbol` column with NSE tickers (no `.NS` suffix).

## How the delivery filter works

NSE publishes a daily **bhavcopy** — a CSV with OHLC, total traded quantity, and *deliverable quantity* for every listed equity. Deliverable quantity is the portion that actually changed hands (was settled to a different demat account) rather than intraday-only trades.

- `delivery_qty` = today's deliverable shares.
- `delivery_times` = today's `delivery_qty` ÷ average over the prior 20 days.

A `delivery_times` of 5× means deliverable quantity is five times the prior-session average. Delivery includes both buyers and sellers; it does not identify institutions or prove accumulation.

Pair that with a breakout (close above 200 EMA) and a volume surge (3×+ over yesterday), and you have the same setup pattern Chandan's swing videos describe.
