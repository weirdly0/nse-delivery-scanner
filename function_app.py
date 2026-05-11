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
"""

from __future__ import annotations
import logging
import os

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
    logging.info("Daily scanner triggered. past_due=%s", timer.past_due)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logging.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID app settings required.")
        return

    # On Azure Functions Consumption, the working dir is the function app
    # root. /tmp is writable and survives within an invocation but not
    # across cold starts — fine because we run once a day.
    cache_dir = "/tmp/bhavcopy_cache"

    candidates, text = run_scan(
        universe_csv         = os.getenv("UNIVERSE_CSV", "nifty500.csv"),
        min_pct_change       = _env_float("MIN_PCT_CHANGE",      0.0),
        min_delivery_qty     = _env_int("MIN_DELIVERY_QTY",      10000),
        min_delivery_times   = _env_float("MIN_DELIVERY_TIMES",  3.0),
        min_vol_ratio_1d     = _env_float("MIN_VOL_RATIO_1D",    3.0),
        require_above_200ema = _env_bool("REQUIRE_ABOVE_200EMA", True),
        bhavcopy_days        = _env_int("BHAVCOPY_DAYS",         25),
        top_n                = _env_int("TOP_N",                 25),
        cache_dir            = cache_dir,
    )

    logging.info("Scanner produced %d candidates.", len(candidates))
    send_telegram(text, bot_token, chat_id)
    logging.info("Telegram dispatch complete.")
