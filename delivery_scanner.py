"""
Simple delivery + volume swing scanner. Sends shortlist to Telegram.

Filters (all configurable via CLI flags):
  - % Change > 0                       (--min-pct-change)
  - Today's Delivery Quantity > 10,000 (--min-delivery-qty)
  - Delivery Times >= 3                (--min-delivery-times)
  - Close above 200 EMA on daily       (--no-above-200ema to disable)
  - Today's Volume >= 3x previous day  (--min-vol-ratio-1d)
  - Base / all-time-high breakout      (--no-breakout, --breakout-lookback)
  - Market cap >= 100 cr               (--min-market-cap-cr)
  - Skip circuit-locked stocks         (--keep-circuit to disable)

Each alert tags a suggested target: large-cap (>= --large-cap-cr) -> ~20%
then trail; smaller -> ~30% (per the strategy videos).

Reads symbols from CSV, pulls NSE bhavcopy for delivery data + yfinance for
OHLC, applies filters, sends ranked shortlist to your Telegram bot.

Usage:
  python delivery_scanner.py                     # run with defaults, send to TG
  python delivery_scanner.py --dry-run           # print, don't send
  python delivery_scanner.py --min-delivery-times 5 --min-vol-ratio-1d 5

Scheduled at 22:30 IST; requires validated same-session end-of-day data.
"""

from __future__ import annotations
import argparse
import logging
import math
import sys
import time
from datetime import date, datetime, time as clock_time
from zoneinfo import ZoneInfo

import requests

from swingscanner.config import load_config
from swingscanner.universe import load_universe
from swingscanner.nse_data import BhavcopyStore, compute_delivery_ratios
from swingscanner.ohlc import (
    fetch_ohlc_bulk, add_indicators, enrich_market_caps,
    is_circuit_locked, is_breakout, with_nse_session,
)
from swingscanner.sectors import get_sector, enrich_sectors

IST = ZoneInfo("Asia/Kolkata")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def send_telegram(text: str, bot_token: str | None, chat_id: str | None) -> None:
    """Send to Telegram (HTML parse mode). Falls back to stdout if no creds."""
    if not text:
        return
    if not bot_token or not chat_id or "PASTE" in str(bot_token):
        print("\n" + "=" * 70)
        print("(Telegram credentials missing — printing report instead)")
        print("=" * 70)
        print(text)
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)

    # When a report spans multiple Telegram messages, prefix each chunk
    # with "Part X/Y" so the reader knows it's intentional, not noise.
    if len(chunks) > 1:
        n = len(chunks)
        chunks = [f"<i>📄 Part {i+1}/{n}</i>\n\n{c}" for i, c in enumerate(chunks)]

    for chunk in chunks:
        try:
            r = requests.post(url, data={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=15)
            if r.status_code != 200:
                logging.error("Telegram error %d: %s", r.status_code, r.text)
        except Exception as e:
            logging.error("Telegram send failed: %s", e)


def run_scan(
    universe_csv: str = "nifty500.csv",
    min_pct_change: float = 0.0,
    min_delivery_qty: int = 10000,
    min_delivery_times: float = 3.0,
    delivery_lookback: int = 20,
    min_vol_ratio_1d: float = 3.0,
    require_above_200ema: bool = True,
    min_market_cap_cr: float = 100.0,
    large_cap_cr: float = 10000.0,
    require_breakout: bool = True,
    breakout_lookback: int = 30,    # backtested best of 15/30/45/60/90 (backtest_breakout.py)
    breakout_tolerance: float = 0.01,  # backtested best of 0.00/0.01/0.02/0.03 (backtest_params.py)
    skip_circuit: bool = True,
    min_turnover_cr: float = 10.0,   # avg 20d turnover floor — backtested liquidity lever
    min_price: float = 30.0,         # anti-penny floor
    trade_budget: float = 1000.0,    # splits alerts into 🟢 tradeable vs 👁 observe-only
    bhavcopy_days: int = 25,
    cache_dir: str = ".cache/bhavcopy",
    top_n: int = 25,
    no_cache: bool = False,
    as_of: date | None = None,
) -> tuple[list[dict], str]:
    """
    Run the full scan and return (candidates_list, formatted_report_text).

    Pure-function-ish: no argv parsing, no Telegram send. Callers (CLI,
    Azure Function) wrap this and decide what to do with the output.
    """
    log = logging.getLogger("scanner")
    now = datetime.now(IST)
    session_date = as_of or now.date()
    session_end = datetime.combine(session_date, clock_time())
    if delivery_lookback < 1:
        raise ValueError("Delivery lookback must be positive")

    def pending(note):
        return [], _empty_message(min_pct_change, min_delivery_qty,
                                  min_delivery_times, min_vol_ratio_1d,
                                  note=note, session_date=session_date)

    if session_date > now.date() or (session_date == now.date() and
                                    now.time() < clock_time(15, 30)):
        return pending("Data pending — this market session has not closed yet.")
    if session_date.weekday() >= 5:
        return pending("No regular NSE session on this date (weekend).")
    log.info("Filters: %%chg>%.1f | deliv_qty>%d | deliv×≥%.1f | vol/prev≥%.1f | "
             "200EMA=%s | turnover≥%.0fcr | price≥%.0f",
             min_pct_change, min_delivery_qty, min_delivery_times,
             min_vol_ratio_1d, require_above_200ema, min_turnover_cr, min_price)

    t0 = time.time()
    symbols = load_universe(universe_csv)

    log.info("Fetching NSE bhavcopies (%d days)...", bhavcopy_days)
    store = BhavcopyStore(cache_dir)
    if no_cache:
        import shutil
        shutil.rmtree(store.cache_dir, ignore_errors=True)
        store.cache_dir.mkdir(parents=True, exist_ok=True)
    # Do not silently walk back to yesterday when today's report is unavailable.
    if store.get_day(session_end) is None:
        log.warning("Validated NSE bhavcopy unavailable for %s", session_date)
        return pending("Data pending — NSE bhavcopy unavailable for this date "
                       "(publication delay, download failure, or market holiday). "
                       "No older session substituted.")
    bhavs = store.get_history(end_date=session_end,
                              lookback_days=max(bhavcopy_days, delivery_lookback + 1))
    if session_end not in bhavs:
        return pending("Data pending — requested session missing from bhavcopy history.")
    delivery_df = compute_delivery_ratios(bhavs, lookback=delivery_lookback)
    if delivery_df is None or delivery_df.empty:
        log.error("Could not compute delivery ratios.")
        return pending("Data pending — insufficient valid delivery history.")
    previous_session = sorted(bhavs)[-2].date()
    log.info("Delivery data: %d symbols", len(delivery_df))

    skipped_no_ohlc = skipped_no_delivery = skipped_circuit = 0
    skipped_stale = 0
    # NSE provides these daily filters for the whole universe in one file.
    # Fetch expensive Yahoo history only for symbols that can still qualify.
    shortlisted = []
    for symbol in symbols:
        if symbol not in delivery_df.index:
            skipped_no_delivery += 1
            continue
        d = delivery_df.loc[symbol]
        values = [float(d[k]) for k in ("close", "prev_close", "traded_qty",
                                        "previous_volume", "deliv_today", "deliv_ratio")]
        if not all(math.isfinite(v) and v > 0 for v in values):
            skipped_no_delivery += 1
            continue
        price, prev_close, volume, prev_volume, delivery_qty, delivery_ratio = values
        if ((price / prev_close - 1) * 100 <= min_pct_change or price < min_price or
                volume / prev_volume < min_vol_ratio_1d or
                delivery_qty < min_delivery_qty or delivery_ratio < min_delivery_times):
            continue
        shortlisted.append(symbol)
    log.info("NSE daily filters: %d/%d symbols need chart history", len(shortlisted), len(symbols))
    ohlc_cache = (fetch_ohlc_bulk(shortlisted, days=250, as_of=session_date)
                  if shortlisted else {})

    candidates: list[dict] = []

    for symbol in shortlisted:
        df = ohlc_cache.get(symbol)
        if df is None or len(df) < 200:
            skipped_no_ohlc += 1
            continue
        if symbol not in delivery_df.index:
            skipped_no_delivery += 1
            continue
        delivery = delivery_df.loc[symbol]
        df = with_nse_session(df, delivery, session_date, previous_session)
        if df is None:
            skipped_stale += 1
            log.warning("Skipping %s: missing prior OHLC session or invalid NSE EOD row for %s",
                        symbol, session_date)
            continue
        if len(df) < 200:
            skipped_no_ohlc += 1
            continue
        df = add_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        price     = float(last["Close"])
        vol_today = float(last["Volume"])
        vol_prev  = float(prev["Volume"])
        ema200    = float(last["ema200"])
        pct_chg   = (price / float(delivery["prev_close"]) - 1) * 100

        turnover_cr = float(last["turnover_cr"]) if last["turnover_cr"] == last["turnover_cr"] else 0.0

        if pct_chg <= min_pct_change:
            continue
        if price < min_price:                       # skip penny stocks
            continue
        if turnover_cr < min_turnover_cr:           # liquidity floor (backtested lever)
            continue
        if require_above_200ema and price < ema200:
            continue
        # Skip untradeable circuit-locked names (Shiv Om / GTX in the video).
        if skip_circuit and is_circuit_locked(df):
            skipped_circuit += 1
            continue
        # Require a base / all-time-high breakout, not just "above 200 EMA".
        if require_breakout and not is_breakout(df, breakout_lookback,
                                                breakout_tolerance):
            continue
        if vol_prev <= 0:
            continue
        vol_ratio_1d = vol_today / vol_prev
        if vol_ratio_1d < min_vol_ratio_1d:
            continue
        if symbol not in delivery_df.index:
            skipped_no_delivery += 1
            continue
        d = delivery_df.loc[symbol]
        deliv_qty   = float(d["deliv_today"])
        deliv_times = float(d["deliv_ratio"])
        deliv_pct   = float(d.get("deliv_pct", 0))
        if deliv_qty < min_delivery_qty:
            continue
        if deliv_times < min_delivery_times:
            continue

        candidates.append({
            "symbol":       symbol,
            "session_date": session_date.isoformat(),
            "price":        round(price, 2),
            "pct_change":   round(pct_chg, 2),
            "vol_today":    int(vol_today),
            "vol_ratio_1d": round(vol_ratio_1d, 2),
            "deliv_qty":    int(deliv_qty),
            "deliv_times":  round(deliv_times, 2),
            "deliv_pct":    round(deliv_pct, 1),
            "ema200":       round(ema200, 2),
            "turnover_cr":  round(turnover_cr, 1),
            "sector":       get_sector(symbol),
        })

    candidates.sort(key=lambda c: c["deliv_times"], reverse=True)
    log.info("Scan complete in %.1fs — %d candidates", time.time() - t0, len(candidates))

    # Enrich sector + market cap via yfinance (parallel, only for the small
    # candidate list). Then apply the market-cap floor and the 20%/30%
    # target tier by size.
    if candidates:
        t1 = time.time()
        enrich_sectors(candidates)
        enrich_market_caps(candidates)
        log.info("Sector + market-cap enrichment: %.1fs", time.time() - t1)

        kept: list[dict] = []
        for c in candidates:
            mc = c.get("market_cap_cr")
            # Drop only on a *known* sub-floor cap; keep unknowns (data may
            # fail) but treat them as small/mid for the target tier.
            if mc is not None and mc < min_market_cap_cr:
                continue
            # Flat +30% target for everyone (best backtested expectancy); large
            # caps additionally flagged to trail from +20% (creator's caution).
            c["target_pct"] = 30
            c["large_cap"] = mc is not None and mc >= large_cap_cr
            kept.append(c)
        dropped = len(candidates) - len(kept)
        if dropped:
            log.info("Dropped %d candidate(s) below ₹%.0f cr market cap",
                     dropped, min_market_cap_cr)
        candidates = kept

    log.info("Skipped %d circuit-locked name(s)", skipped_circuit)

    text = _format_report(candidates, top_n, min_pct_change, min_delivery_qty,
                          min_delivery_times, min_vol_ratio_1d, large_cap_cr,
                          trade_budget, min_turnover_cr, min_price,
                          session_date=session_date)
    if skipped_stale or skipped_no_ohlc or skipped_no_delivery:
        text += (f"\n\n⚠️ Coverage incomplete: {skipped_stale} stale/misaligned OHLC; "
                 f"{skipped_no_ohlc} missing/short OHLC; "
                 f"{skipped_no_delivery} missing delivery history. These stocks were skipped.")
    log.info("Session=%s previous=%s skipped_stale=%d skipped_ohlc=%d skipped_delivery=%d",
             session_date, previous_session, skipped_stale, skipped_no_ohlc, skipped_no_delivery)
    return candidates, text


def _esc(s) -> str:
    """Minimal HTML escape for Telegram HTML parse mode."""
    return (str(s).replace("&", "&amp;")
                  .replace("<", "&lt;")
                  .replace(">", "&gt;"))


def _fmt_mcap(mc) -> str:
    """Human-readable market cap in crores, or 'n/a' when unknown."""
    if mc is None:
        return "MCap n/a"
    return f"MCap ₹{mc:,.0f} cr"


def _stock_block(i, c, trade_budget) -> str:
    sym = _esc(c["symbol"])
    sector_tag = f"  <i>{_esc(c['sector'])}</i>" if c.get("sector") else ""
    target = c.get("target_pct", 30)
    is_large = c.get("large_cap", False)
    cap_label = "large-cap · trail from +20%" if is_large else "small/mid-cap"
    mcap_str = _fmt_mcap(c.get("market_cap_cr"))
    price = c["price"]
    turn = c.get("turnover_cr", 0)
    # affordability line for the configured per-trade budget
    if price <= trade_budget and price > 0:
        shares = int(trade_budget // price)
        afford = f"🟢 ₹{trade_budget:,.0f} buys <b>{shares} sh</b>"
    else:
        afford = f"👁 observe — 1 sh = ₹{price:,.0f}"
    # Plain TradingView web URL — always works, opens chart in browser.
    tv_url = f"https://in.tradingview.com/chart/?symbol=NSE%3A{c['symbol']}"
    sc_url = f"https://www.screener.in/company/{c['symbol']}/"
    return (
        f"<b>{i}. {sym}</b>{sector_tag}  ₹{price}  ({c['pct_change']:+.1f}%)\n"
        f"   📦 Deliv <b>{c['deliv_times']}×</b>  ({c['deliv_qty']:,} sh, {c['deliv_pct']}%)\n"
        f"   📈 Vol <b>{c['vol_ratio_1d']}×</b> prev  |  Turnover ₹{turn}cr/day\n"
        f"   🎯 Target ~<b>{target}%</b>  ({cap_label})  |  {mcap_str}\n"
        f"   {afford}\n"
        f"   📊 <a href=\"{tv_url}\">Chart</a>  |  "
        f"📋 <a href=\"{sc_url}\">Fundamentals</a>\n"
    )


def _format_report(candidates, top_n, min_pct_change, min_delivery_qty,
                   min_delivery_times, min_vol_ratio_1d,
                   large_cap_cr: float = 10000.0, trade_budget: float = 1000.0,
                   min_turnover_cr: float = 10.0, min_price: float = 30.0,
                   session_date: date | None = None) -> str:
    today = (session_date or datetime.now(IST).date()).strftime("%a, %d %b %Y")
    if not candidates:
        return _empty_message(min_pct_change, min_delivery_qty,
                              min_delivery_times, min_vol_ratio_1d,
                              session_date=session_date)
    top = candidates[:top_n]
    big_cr = f"{large_cap_cr:,.0f}"
    # split: affordable for the per-trade budget vs observe-only (pricey)
    tradeable = [c for c in top if 0 < c["price"] <= trade_budget]
    observe   = [c for c in top if c["price"] > trade_budget]
    lines = [
        f"📊 <b>Delivery Scanner — {today}</b>",
        "<i>Completed market session (IST)</i>",
        f"Found <b>{len(candidates)}</b> setups (showing top {len(top)})",
        f"<i>Filters: %chg&gt;{min_pct_change}, deliv≥{min_delivery_qty} sh, "
        f"deliv×≥{min_delivery_times}, vol≥{min_vol_ratio_1d}×,</i>",
        f"<i>above 200 EMA, base/ATH breakout, turnover≥₹{min_turnover_cr:.0f}cr/day, "
        f"price≥₹{min_price:.0f} (no penny)</i>",
        f"<i>🎯 Target ~30% · large-cap (≥₹{big_cr} cr): trail from +20%</i>",
    ]
    n = 0
    if tradeable:
        lines.append(f"\n🟢 <b>TRADEABLE</b> (≤ ₹{trade_budget:,.0f} budget) — "
                     f"{len(tradeable)} stocks")
        for c in tradeable:
            n += 1
            lines.append(_stock_block(n, c, trade_budget))
    if observe:
        lines.append(f"\n👁 <b>OBSERVE / LEARN</b> (quality breakouts, above your "
                     f"₹{trade_budget:,.0f} budget) — {len(observe)} stocks")
        for c in observe:
            n += 1
            lines.append(_stock_block(n, c, trade_budget))
    lines.append("\n<i>⚠️ Do your fundamental check before entry. Educational only.</i>")
    return "\n".join(lines)


def _empty_message(min_pct_change, min_delivery_qty, min_delivery_times,
                   min_vol_ratio_1d, note: str = "No stocks matched the filters for this session.",
                   session_date: date | None = None) -> str:
    today = (session_date or datetime.now(IST).date()).strftime("%a, %d %b %Y")
    return (f"📊 <b>Delivery Scanner — {today}</b>\n\n"
            f"{_esc(note)}\n"
            f"<i>Filters: %chg&gt;{min_pct_change}, deliv≥{min_delivery_qty} "
            f"shares, deliv×≥{min_delivery_times}, vol≥{min_vol_ratio_1d}× "
            f"prev day, above 200 EMA</i>")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simple delivery+volume scanner — Telegram alerter")
    parser.add_argument("--universe", default="nifty500.csv")
    parser.add_argument("--min-pct-change", type=float, default=0.0)
    parser.add_argument("--min-delivery-qty", type=int, default=10000)
    parser.add_argument("--min-delivery-times", type=float, default=3.0)
    parser.add_argument("--delivery-lookback", type=int, default=20)
    parser.add_argument("--min-vol-ratio-1d", type=float, default=3.0)
    parser.add_argument("--require-above-200ema", action="store_true", default=True)
    parser.add_argument("--no-above-200ema", dest="require_above_200ema", action="store_false")
    parser.add_argument("--min-market-cap-cr", type=float, default=100.0,
                        help="Drop candidates below this market cap (₹ crore)")
    parser.add_argument("--large-cap-cr", type=float, default=10000.0,
                        help="At/above this MCap → 20%% target tier, else 30%%")
    parser.add_argument("--require-breakout", action="store_true", default=True)
    parser.add_argument("--no-breakout", dest="require_breakout", action="store_false",
                        help="Disable the base/all-time-high breakout filter")
    parser.add_argument("--breakout-lookback", type=int, default=30,
                        help="Sessions in the base whose high must be broken "
                             "(30 backtested best vs 15/45/60/90)")
    parser.add_argument("--breakout-tolerance", type=float, default=0.01,
                        help="Allow close within this fraction below the base high "
                             "(0.01 backtested best vs 0.00/0.02/0.03)")
    parser.add_argument("--keep-circuit", dest="skip_circuit", action="store_false",
                        default=True, help="Don't filter out circuit-locked stocks")
    parser.add_argument("--min-turnover-cr", type=float, default=10.0,
                        help="Min avg 20d turnover ₹cr/day (10 = backtested "
                             "liquidity lever; the single biggest improvement)")
    parser.add_argument("--min-price", type=float, default=30.0,
                        help="Skip penny stocks below this price")
    parser.add_argument("--trade-budget", type=float, default=1000.0,
                        help="Per-trade budget — splits alerts into 🟢 tradeable "
                             "(≤ budget) vs 👁 observe-only (pricier)")
    parser.add_argument("--bhavcopy-days", type=int, default=25)
    parser.add_argument("--cache-dir", default=".cache/bhavcopy")
    parser.add_argument("--config", default="config.yaml",
                        help="Read Telegram credentials from this YAML")
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--as-of", type=date.fromisoformat,
                        help="Explicit market-session date YYYY-MM-DD (default: today in IST)")
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("scanner")
    log.info("=" * 70)
    log.info("Delivery + Volume Scanner (CLI)")
    log.info("=" * 70)

    _, text = run_scan(
        universe_csv        = args.universe,
        min_pct_change      = args.min_pct_change,
        min_delivery_qty    = args.min_delivery_qty,
        min_delivery_times  = args.min_delivery_times,
        delivery_lookback   = args.delivery_lookback,
        min_vol_ratio_1d    = args.min_vol_ratio_1d,
        require_above_200ema= args.require_above_200ema,
        min_market_cap_cr   = args.min_market_cap_cr,
        large_cap_cr        = args.large_cap_cr,
        require_breakout    = args.require_breakout,
        breakout_lookback   = args.breakout_lookback,
        breakout_tolerance  = args.breakout_tolerance,
        skip_circuit        = args.skip_circuit,
        min_turnover_cr     = args.min_turnover_cr,
        min_price           = args.min_price,
        trade_budget        = args.trade_budget,
        bhavcopy_days       = args.bhavcopy_days,
        cache_dir           = args.cache_dir,
        top_n               = args.top_n,
        no_cache            = args.no_cache,
        as_of               = args.as_of,
    )

    bot_token = chat_id = None
    try:
        cfg = load_config(args.config)
        bot_token = cfg.telegram.bot_token
        chat_id = cfg.telegram.chat_id
    except Exception as e:
        log.warning("Could not load Telegram config (%s): %s", args.config, e)

    if args.dry_run:
        print("\n" + text)
    else:
        send_telegram(text, bot_token, chat_id)
        log.info("Report sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
