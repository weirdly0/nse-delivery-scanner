"""
Azure Functions entry point — timer-triggered daily scanner.

Reads filter thresholds + Telegram credentials from App Settings (env vars),
runs the scanner, and posts the report to Telegram.

App settings expected:
  TELEGRAM_BOT_TOKEN   (required)
  TELEGRAM_CHAT_ID     (required)
  MIN_PCT_CHANGE       (optional, default 0.0)
  MIN_DELIVERY_QTY     (optional, default 10000)
  MIN_DELIVERY_TIMES   (optional, default 3.0)
  MIN_VOL_RATIO_1D     (optional, default 3.0)
  TOP_N                (optional, default 25)
  REQUIRE_ABOVE_200EMA (optional, "true"/"false", default true)
  UNIVERSE_CSV         (optional, default "nifty500.csv")
  MIN_MARKET_CAP_CR    (optional, default 100.0)
  LARGE_CAP_CR         (optional, default 10000.0)
  REQUIRE_BREAKOUT     (optional, "true"/"false", default true)
  BREAKOUT_LOOKBACK    (optional, default 30)
  BREAKOUT_TOLERANCE   (optional, default 0.01)
  SKIP_CIRCUIT         (optional, "true"/"false", default true)

All new filter settings have safe defaults, so no App Settings change is
required to deploy this update.
"""

from __future__ import annotations
import logging
import os
import sys
import traceback

import azure.functions as func

from delivery_scanner import run_scan, send_telegram


app = func.FunctionApp()


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# NCRONTAB: {sec} {min} {hour} {day} {month} {day-of-week}
# 0 30 5 * * 1-5 = 05:30 UTC = 11:00 AM IST, Mon-Fri
@app.function_name(name="DailyScanner")
@app.timer_trigger(
    schedule="0 30 5 * * 1-5",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def daily_scanner(timer: func.TimerRequest) -> None:
    # Configure root logger to print everything to stderr so the Azure
    # platform's stdout collector picks it up even if App Insights ingest
    # is lagging.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        force=True,
        stream=sys.stderr,
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    log = logging.getLogger("daily_scanner")
    log.info("Daily scanner triggered. past_due=%s", timer.past_due)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID app settings required.")
        return

    cache_dir = "/tmp/bhavcopy_cache"

    try:
        candidates, text = run_scan(
            universe_csv         = os.getenv("UNIVERSE_CSV", "nifty500.csv"),
            min_pct_change       = _env_float("MIN_PCT_CHANGE",      0.0),
            min_delivery_qty     = _env_int("MIN_DELIVERY_QTY",      10000),
            min_delivery_times   = _env_float("MIN_DELIVERY_TIMES",  3.0),
            min_vol_ratio_1d     = _env_float("MIN_VOL_RATIO_1D",    3.0),
            require_above_200ema = _env_bool("REQUIRE_ABOVE_200EMA", True),
            min_market_cap_cr    = _env_float("MIN_MARKET_CAP_CR",   100.0),
            large_cap_cr         = _env_float("LARGE_CAP_CR",        10000.0),
            require_breakout     = _env_bool("REQUIRE_BREAKOUT",     True),
            breakout_lookback    = _env_int("BREAKOUT_LOOKBACK",     30),
            breakout_tolerance   = _env_float("BREAKOUT_TOLERANCE",  0.01),
            skip_circuit         = _env_bool("SKIP_CIRCUIT",         True),
            bhavcopy_days        = _env_int("BHAVCOPY_DAYS",         25),
            top_n                = _env_int("TOP_N",                 25),
            cache_dir            = cache_dir,
        )
        log.info("Scanner produced %d candidates.", len(candidates))
        send_telegram(text, bot_token, chat_id)
        log.info("Telegram dispatch complete.")

    except Exception as e:
        # Log full traceback for diagnostics, and also ping Telegram so
        # we know the run failed instead of just silently not getting a
        # message.
        tb = traceback.format_exc()
        log.error("Scanner FAILED with %s: %s", type(e).__name__, e)
        log.error("Full traceback:\n%s", tb)
        try:
            fail_text = (
                "❌ <b>Scanner failed</b>\n\n"
                f"<b>Error:</b> {type(e).__name__}: {str(e)[:300]}\n\n"
                "<i>Check Azure logs for traceback.</i>"
            )
            send_telegram(fail_text, bot_token, chat_id)
        except Exception as send_err:
            log.error("Also failed to send error alert: %s", send_err)
        raise
