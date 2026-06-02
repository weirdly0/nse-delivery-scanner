# CLAUDE.md — NSE Delivery + Breakout Scanner

Context for future Claude sessions. Read this first.

## What this project is

A daily NSE swing/breakout scanner. It finds stocks that (a) closed up on high
**delivery** + **volume**, (b) are **breaking out** of a base/all-time-high above
the 200 EMA, then posts a ranked shortlist to **Telegram**. The strategy mirrors a
YouTube creator's "30% every month" breakout method (delivery-times spike + volume
breakout + all-time-high/base breakout, ~30% target, stop at the breakout candle low).

Entry point: `delivery_scanner.py::run_scan()` (pure-ish; CLI and Azure both call it).

## How it runs (TWO schedulers — important)

1. **Azure Function** `nse-scanner-4e104b` (RG `rg-nse-scanner`, **outlook account
   amitk.mishra1011@outlook.com**, "Visual Studio Enterprise Subscription", Central
   India, Linux Consumption Y1, Python 3.11). Timer `0 30 5 * * 1-5` = 11:00 AM IST
   Mon–Fri. Deploy: `az account set --subscription "Visual Studio Enterprise
   Subscription"` then `func azure functionapp publish nse-scanner-4e104b --build
   remote --python`. Config via App Settings (env vars), Telegram creds already set.
2. **GitHub Actions** `.github/workflows/scanner.yml` ALSO runs `python
   delivery_scanner.py` on cron 05:30 UTC Mon–Fri (uses repo secrets). So the report
   can fire **twice/day** (Azure + Actions). If the user wants one, disable the other.

Git remote: `github.com:weirdly0/nse-delivery-scanner` (branch `main`). Deploy is from
local code (`func publish`) AND from `main` (Actions); commit to `main` to update both.

## Config (all env-tunable on Azure; CLI flags mirror them)

Defaults chosen by backtest (see below): `MIN_TURNOVER_CR=10`, `MIN_PRICE=30`,
`TRADE_BUDGET=1000`, `BREAKOUT_LOOKBACK=30`, `BREAKOUT_TOLERANCE=0.01`,
`MIN_DELIVERY_TIMES=3` (creator uses 5), `MIN_VOL_RATIO_1D=3`, `MIN_DELIVERY_QTY=10000`,
`MIN_MARKET_CAP_CR=100`, `SKIP_CIRCUIT=true`, `UNIVERSE_CSV=nse_equity.csv`, `TOP_N=500`.

The Telegram report splits alerts into **🟢 TRADEABLE** (price ≤ TRADE_BUDGET, shows
shares the budget buys) and **👁 OBSERVE/LEARN** (pricier quality breakouts to watch).
Why: the user pilots with only a few hundred–₹1000, so cheap stocks are "tradeable",
pricier quality names are "watch to learn". Target shown ~30% with a trail-from-20%
note on large-caps.

## Key decisions & WHY (backtested — don't re-litigate without re-running)

- **Liquidity is the real edge, not parameter tuning.** A 2-year, no-hindsight,
  out-of-sample portfolio backtest on the full ~1,870-stock universe showed the raw
  breakout signal **LOSES money** (-24%, PF 0.83, -40% DD) — and grid-searching
  lookback/stop/target **overfits** (year-1 winner went negative in year-2). Adding a
  **~₹10cr avg-20d-turnover floor flips it to +24%, and +14% out-of-sample** (PF 1.34)
  while Nifty was -5.8%. So `MIN_TURNOVER_CR=10` is the single most important setting.
- **Price band:** below ~₹700 the edge goes negative; capping price for affordability
  costs returns. So there is NO max-price filter — instead the report just *tags*
  affordability (tradeable vs observe). Anti-penny floor `MIN_PRICE=30`.
- **Breakout lookback=30, tolerance=0.01** were the best of {15,30,45,60,90} /
  {0,0.01,0.02,0.03} on a separate trade-level backtest (60 was the *worst*).
- **Target = flat ~30%** had higher expectancy than the 20/30 market-cap tier; large
  caps still get a "trail from +20%" note (creator's consistency caution).

## DATA CONSTRAINTS (be honest about these — they bound every backtest)

- **No free point-in-time fundamentals.** yfinance gives only *today's* snapshot;
  using it on a past signal = hindsight. Screener.in / NSE filings have history but
  restated + no clean as-of-date alignment + bulk scraping is fragile/ToS. So deep
  `/stockcheck` fundamentals (ROCE, pledge, cash flow, red flags) are applied **live
  going forward**, NOT in the quant backtest.
- **No free historical bhavcopy delivery** for 2 years → the backtest uses only
  price/volume + turnover/RS proxies, NOT the actual delivery filters. The live
  scanner is MORE selective than the backtest, so real results are likely better than
  the raw -24% figure. The turnover floor partly proxies what delivery confirmation does.

## Backtest harness (re-run before changing config)

- `backtest_breakout.py` — breakout lookback sweep (trade-level).
- `backtest_params.py` — tolerance + target-tier sweep.
- `backtest_full.py` — realistic 2y portfolio sim (entry next open, candle-low stop,
  capital-constrained, max 8 positions) + train/test grid search. `python
  backtest_full.py nse_equity.csv` (full) or `... nifty500.csv 150` (fast subset).
- `scenario_quality.py` — liquidity/price-band sweeps; caches fetched data to
  `.cache/backtest_nse_equity_4y.pkl` (gitignored) so reruns are instant.

If asked to "fine-tune the config", the answer is usually **selectivity (turnover /
liquidity / eventually fundamentals)**, not tweaking lookback/stop/target.

## Related user memory

Project memory at `~/.claude/projects/-Users-amit-Downloads-scanner/memory/` —
see `backtest-liquidity-lever.md`.
