"""
NSE bhavcopy (daily delivery data) fetcher with on-disk caching.

The bhavcopy is a single CSV per trading day containing OHLC + traded
quantity + DELIVERABLE QUANTITY for every NSE-listed stock. This is
the *exact* data Stockedge uses for its "high delivery quantity" scan
that Chandan demonstrates in the videos.

Why bhavcopy and not per-symbol API calls?
  - One HTTP request gives us 2000+ stocks
  - Caching to disk means daily reruns are instant
  - Survives NSE's per-IP rate limits
"""

from __future__ import annotations
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)


# Bhavcopy column names (NSE format) — known good as of 2024
COL_SYMBOL    = "SYMBOL"
COL_SERIES    = " SERIES"
COL_CLOSE     = " CLOSE_PRICE"
COL_TRADED_QTY = " TTL_TRD_QNTY"
COL_DELIV_QTY = " DELIV_QTY"
COL_DELIV_PCT = " DELIV_PER"
COL_DATE      = " DATE1"


class BhavcopyStore:
    """Downloads, caches, and queries NSE bhavcopies."""

    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------
    # Single-day fetch with caching
    # -----------------------------------------------------------------
    def get_day(self, date: datetime) -> Optional[pd.DataFrame]:
        """Return one day's bhavcopy. Cached after first download."""
        date_str = date.strftime("%d%m%Y")
        cache_file = self.cache_dir / f"bhav_{date_str}.csv"

        if cache_file.exists():
            try:
                return pd.read_csv(cache_file)
            except Exception as e:
                log.warning("Cache read failed for %s: %s", cache_file, e)

        # Try the live download
        df = self._download_bhavcopy(date)
        if df is not None and not df.empty:
            df.to_csv(cache_file, index=False)
            return df
        return None

    @staticmethod
    def _download_bhavcopy(date: datetime) -> Optional[pd.DataFrame]:
        """Download bhavcopy for a single date via nsepython."""
        try:
            from nsepython import get_bhavcopy
            date_str = date.strftime("%d-%m-%Y")
            df = get_bhavcopy(date_str)
            if df is None or df.empty:
                return None
            return df
        except Exception as e:
            log.debug("Bhavcopy download failed for %s: %s",
                      date.strftime("%d-%m-%Y"), e)
            return None

    # -----------------------------------------------------------------
    # Multi-day fetch (used to build delivery-ratio averages)
    # -----------------------------------------------------------------
    def get_history(
        self,
        end_date: datetime,
        lookback_days: int,
    ) -> dict[datetime, pd.DataFrame]:
        """
        Walk backward from end_date and collect up to `lookback_days`
        valid trading-day bhavcopies. Skips weekends and missing days
        (holidays).
        """
        results: dict[datetime, pd.DataFrame] = {}
        cursor = end_date
        attempts = 0
        max_attempts = lookback_days * 2  # account for weekends + holidays

        while len(results) < lookback_days and attempts < max_attempts:
            # Skip weekends
            if cursor.weekday() < 5:
                df = self.get_day(cursor)
                if df is not None and not df.empty:
                    results[cursor] = df
            cursor -= timedelta(days=1)
            attempts += 1

        log.info("Bhavcopy history: %d days collected (target %d)",
                 len(results), lookback_days)
        return results


# ---------------------------------------------------------------------
# Delivery-ratio computation (the "delivery times" filter)
# ---------------------------------------------------------------------

def compute_delivery_ratios(
    bhavs: dict[datetime, pd.DataFrame],
) -> Optional[pd.DataFrame]:
    """
    Given a dict of {date: bhavcopy_df}, return a DataFrame keyed by
    SYMBOL with columns:
       deliv_today      : today's deliverable quantity
       deliv_avg_prior  : average over the prior days
       deliv_ratio      : today / avg  (the "delivery times" number)
       close            : today's close
       deliv_pct        : today's delivery percentage
    """
    if not bhavs:
        return None

    # Most-recent date is "today"
    sorted_dates = sorted(bhavs.keys(), reverse=True)
    today_date = sorted_dates[0]
    prior_dates = sorted_dates[1:]

    today_df = bhavs[today_date].copy()
    today_df = today_df[today_df[COL_SERIES].str.strip() == "EQ"]

    # Coerce numeric columns (NSE sometimes uses "-" for missing)
    for col in (COL_DELIV_QTY, COL_TRADED_QTY, COL_DELIV_PCT, COL_CLOSE):
        today_df[col] = pd.to_numeric(today_df[col], errors="coerce")
    today_df = today_df.dropna(subset=[COL_DELIV_QTY, COL_CLOSE])

    # Build a wide table of historical delivery quantity per symbol
    history_frames = []
    for d in prior_dates:
        df = bhavs[d]
        df = df[df[COL_SERIES].str.strip() == "EQ"]
        df = df[[COL_SYMBOL.strip() if COL_SYMBOL == "SYMBOL" else COL_SYMBOL,
                 COL_DELIV_QTY]].copy()
        df.columns = ["SYMBOL", "deliv"]
        df["deliv"] = pd.to_numeric(df["deliv"], errors="coerce")
        history_frames.append(df.set_index("SYMBOL")["deliv"])

    if not history_frames:
        return None

    hist_wide = pd.concat(history_frames, axis=1)
    avg_prior = hist_wide.mean(axis=1)

    out = pd.DataFrame({
        "deliv_today":     today_df.set_index(COL_SYMBOL)[COL_DELIV_QTY],
        "deliv_pct":       today_df.set_index(COL_SYMBOL)[COL_DELIV_PCT],
        "close":           today_df.set_index(COL_SYMBOL)[COL_CLOSE],
        "deliv_avg_prior": avg_prior,
    })
    out["deliv_ratio"] = out["deliv_today"] / out["deliv_avg_prior"]
    out = out.dropna(subset=["deliv_ratio"])
    return out
