"""
Simple delivery + volume swing scanner. Sends shortlist to Telegram.

Filters (all configurable via CLI flags):
  - % Change > 0                       (--min-pct-change)
  - Today's Delivery Quantity > 10,000 (--min-delivery-qty)
  - Delivery Times >= 3                (--min-delivery-times)
  - Close above 200 EMA on daily       (--no-above-200ema to disable)
  - Today's Volume >= 3x previous day  (--min-vol-ratio-1d)

Reads symbols from CSV, pulls NSE bhavcopy for delivery data + yfinance for
OHLC, applies filters, sends ranked shortlist to your Telegram bot.

Usage:
  python delivery_scanner.py                     # run with defaults, send to TG
  python delivery_scanner.py --dry-run           # print, don't send
  python delivery_scanner.py --min-delivery-times 5 --min-vol-ratio-1d 5

Best run after 6 PM IST (NSE bhavcopy publishes then).
"""

from __future__ import annotations
import argparse
import logging
import sys
import time
from datetime import datetime

import requests

from swingscanner.config import load_config
from swingscanner.universe import load_universe
from swingscanner.nse_data import BhavcopyStore, compute_delivery_ratios
from swingscanner.ohlc import fetch_ohlc_bulk, add_indicators


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def send_telegram(text: str, bot_token: str | None, chat_id: str | None) -> None:
    """Send to Telegram. Falls back to stdout if no credentials."""
    if not text:
        return
    if not bot_token or not chat_id or "PASTE" in str(bot_token):
        print("\n" + "=" * 70)
        print("(Telegram credentials missing — printing report instead)")
        print("=" * 70)
        print(text)
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # Chunk on line boundaries (Telegram limit is 4096 chars)
    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)

    for chunk in chunks:
        try:
            r = requests.post(url, data={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }, timeout=15)
            if r.status_code != 200:
                logging.error("Telegram error %d: %s", r.status_code, r.text)
        except Exception as e:
            logging.error("Telegram send failed: %s", e)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simple delivery+volume scanner — Telegram alerter")
    parser.add_argument("--universe", default="nifty500.csv",
                        help="CSV with a 'symbol' column (default: nifty500.csv)")
    # Filter knobs
    parser.add_argument("--min-pct-change", type=float, default=0.0,
                        help="Close must be up by at least this %% (default 0)")
    parser.add_argument("--min-delivery-qty", type=int, default=10000,
                        help="Minimum delivery quantity in shares (default 10000)")
    parser.add_argument("--min-delivery-times", type=float, default=3.0,
                        help="Today's delivery / N-day avg, minimum (default 3.0)")
    parser.add_argument("--delivery-lookback", type=int, default=20,
                        help="Days for delivery-times average (default 20)")
    parser.add_argument("--min-vol-ratio-1d", type=float, default=3.0,
                        help="Today's volume must be >= this × yesterday's (default 3.0)")
    parser.add_argument("--require-above-200ema", action="store_true", default=True,
                        help="Require close > 200 EMA (default on)")
    parser.add_argument("--no-above-200ema", dest="require_above_200ema",
                        action="store_false", help="Disable the 200 EMA filter")
    # Data/runtime
    parser.add_argument("--bhavcopy-days", type=int, default=25,
                        help="Bhavcopy history (must exceed delivery_lookback)")
    parser.add_argument("--cache-dir", default=".cache/bhavcopy")
    parser.add_argument("--config", default="config.yaml",
                        help="Read Telegram credentials from this YAML")
    parser.add_argument("--top-n", type=int, default=25,
                        help="How many top results to include in the report")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print report instead of sending to Telegram")
    parser.add_argument("--no-cache", action="store_true",
                        help="Force re-download of bhavcopies")
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("scanner")

    log.info("=" * 70)
    log.info("Delivery + Volume Scanner")
    log.info("=" * 70)
    log.info("Filters:")
    log.info("  %% change      > %.1f%%", args.min_pct_change)
    log.info("  delivery qty  > %d shares", args.min_delivery_qty)
    log.info("  delivery x    >= %.1f (over prior %d days)",
             args.min_delivery_times, args.delivery_lookback)
    log.info("  volume vs prev day  >= %.1f x", args.min_vol_ratio_1d)
    log.info("  above 200 EMA       = %s", args.require_above_200ema)

    t0 = time.time()

    # ---- 1. Universe -------------------------------------------------
    symbols = load_universe(args.universe)

    # ---- 2. Bhavcopies (delivery data) -------------------------------
    log.info("Fetching NSE bhavcopies (%d days)...", args.bhavcopy_days)
    store = BhavcopyStore(args.cache_dir)
    if args.no_cache:
        import shutil
        shutil.rmtree(store.cache_dir, ignore_errors=True)
        store.cache_dir.mkdir(parents=True, exist_ok=True)
    bhavs = store.get_history(end_date=datetime.now(),
                              lookback_days=args.bhavcopy_days)
    if not bhavs:
        log.error("No bhavcopy data available. NSE publishes around 6 PM IST — "
                  "try later, or check your network.")
        return 1
    delivery_df = compute_delivery_ratios(bhavs)
    if delivery_df is None or delivery_df.empty:
        log.error("Could not compute delivery ratios.")
        return 1
    log.info("Delivery data: %d symbols, ratio range %.1f-%.1f",
             len(delivery_df),
             delivery_df["deliv_ratio"].min(),
             delivery_df["deliv_ratio"].max())

    # ---- 3. OHLC bulk fetch (for EMA + volume) -----------------------
    log.info("Bulk-fetching OHLC for EMA + volume check...")
    ohlc_cache = fetch_ohlc_bulk(symbols, days=250)

    # ---- 4. Filter -------------------------------------------------
    candidates: list[dict] = []
    skipped_no_ohlc = skipped_no_delivery = 0

    for symbol in symbols:
        df = ohlc_cache.get(symbol)
        if df is None or len(df) < 200:
            skipped_no_ohlc += 1
            continue
        df = add_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        price     = float(last["Close"])
        vol_today = float(last["Volume"])
        vol_prev  = float(prev["Volume"])
        ema200    = float(last["ema200"])
        pct_chg   = float(last["pct_change"])

        # F1: closed up
        if pct_chg <= args.min_pct_change:
            continue
        # F2: above 200 EMA
        if args.require_above_200ema and price < ema200:
            continue
        # F3: volume vs yesterday
        if vol_prev <= 0:
            continue
        vol_ratio_1d = vol_today / vol_prev
        if vol_ratio_1d < args.min_vol_ratio_1d:
            continue
        # F4-5: delivery quantity + times
        if symbol not in delivery_df.index:
            skipped_no_delivery += 1
            continue
        d = delivery_df.loc[symbol]
        deliv_qty   = float(d["deliv_today"])
        deliv_times = float(d["deliv_ratio"])
        deliv_pct   = float(d.get("deliv_pct", 0))
        if deliv_qty < args.min_delivery_qty:
            continue
        if deliv_times < args.min_delivery_times:
            continue

        candidates.append({
            "symbol":       symbol,
            "price":        round(price, 2),
            "pct_change":   round(pct_chg, 2),
            "vol_today":    int(vol_today),
            "vol_ratio_1d": round(vol_ratio_1d, 2),
            "deliv_qty":    int(deliv_qty),
            "deliv_times":  round(deliv_times, 2),
            "deliv_pct":    round(deliv_pct, 1),
            "ema200":       round(ema200, 2),
        })

    # Rank by delivery times (strongest institutional buying signal)
    candidates.sort(key=lambda c: c["deliv_times"], reverse=True)

    log.info("Scan complete in %.1fs", time.time() - t0)
    log.info("  Candidates:        %d", len(candidates))
    log.info("  Skipped (no OHLC): %d", skipped_no_ohlc)
    log.info("  Skipped (no deliv data): %d", skipped_no_delivery)

    # ---- 5. Format and send -----------------------------------------
    today = datetime.now().strftime("%a, %d %b %Y")
    if not candidates:
        text = (f"📊 *Delivery Scanner — {today}*\n\n"
                f"No stocks matched the filters today.\n"
                f"_Filters: %chg>{args.min_pct_change}, deliv≥{args.min_delivery_qty} "
                f"shares, deliv×≥{args.min_delivery_times}, vol≥{args.min_vol_ratio_1d}× "
                f"prev day, above 200 EMA_")
    else:
        top = candidates[:args.top_n]
        lines = [
            f"📊 *Delivery Scanner — {today}*",
            f"Found *{len(candidates)}* setups (showing top {len(top)})",
            f"_Filters: %chg>{args.min_pct_change}, deliv≥{args.min_delivery_qty} shares,_",
            f"_deliv×≥{args.min_delivery_times}, vol≥{args.min_vol_ratio_1d}× prev day, above 200 EMA_",
            "",
        ]
        for i, c in enumerate(top, 1):
            lines.append(
                f"*{i}. {c['symbol']}*  ₹{c['price']}  ({c['pct_change']:+.1f}%)\n"
                f"   📦 Deliv *{c['deliv_times']}×*  ({c['deliv_qty']:,} sh, {c['deliv_pct']}%)\n"
                f"   📈 Vol *{c['vol_ratio_1d']}×* prev day  |  200 EMA ₹{c['ema200']}\n"
            )
        lines.append("\n_⚠️ Do your fundamental check before entry. Educational only._")
        text = "\n".join(lines)

    # Load Telegram credentials from config.yaml (or env vars via that loader)
    bot_token = chat_id = None
    try:
        cfg = load_config(args.config)
        bot_token = cfg.telegram.bot_token
        chat_id = cfg.telegram.chat_id
    except Exception as e:
        log.warning("Could not load Telegram config (%s): %s",
                    args.config, e)

    if args.dry_run:
        print("\n" + text)
    else:
        send_telegram(text, bot_token, chat_id)
        log.info("Report sent.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
