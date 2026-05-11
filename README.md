# NSE Delivery + Volume Scanner

A small Python scanner that finds NSE stocks with **high-conviction institutional buying** today and pushes the shortlist to Telegram.

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

Best run **after 6 PM IST** — that's when NSE publishes the day's bhavcopy with delivery data. Before then, you get yesterday's numbers.

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

## Daily automation

This repo includes a **GitHub Actions workflow** at `.github/workflows/scanner.yml` that runs the scanner **every weekday at 11 AM IST** (5:30 AM UTC) and pushes the report to your Telegram. To use it:

1. Fork or clone this repo.
2. Add two repo secrets at **Settings → Secrets and variables → Actions**:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. The workflow runs automatically. You can also trigger it manually from the **Actions** tab.

> ⚠️ **Timing note:** NSE bhavcopy publishes around 6 PM IST. An 11 AM IST run uses **yesterday's** delivery data and **yesterday's** close. If you want the freshest signals, edit the cron in `.github/workflows/scanner.yml` to `30 12 * * 1-5` (= 6 PM IST).

### Local cron alternative

```cron
0 18 * * 1-5  cd /path/to/scanner && /usr/bin/python3 delivery_scanner.py
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

A `delivery_times` of 5× means today's institutional buying is 5× the recent baseline — a strong indicator that real money is accumulating, not just day traders.

Pair that with a breakout (close above 200 EMA) and a volume surge (3×+ over yesterday), and you have the same setup pattern Chandan's swing videos describe.
