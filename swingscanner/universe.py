"""Universe loader — CSV or live fetch from NSE."""

from __future__ import annotations
import logging
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)


def load_universe(csv_path: str | Path, fetch_live: bool = False) -> list[str]:
    """Return a deduplicated list of NSE symbols (no .NS suffix)."""
    if fetch_live:
        symbols = _fetch_live_nifty500()
        if symbols:
            return symbols
        log.warning("Live fetch failed, falling back to CSV.")

    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Universe CSV not found: {path}\n"
            f"Set fetch_live_nifty500: true in config, or provide the file."
        )

    df = pd.read_csv(path)
    if "symbol" not in df.columns:
        raise ValueError(f"{path} must have a 'symbol' column.")

    symbols = (
        df["symbol"].dropna().astype(str).str.upper().str.strip().unique().tolist()
    )
    log.info("Loaded %d symbols from %s", len(symbols), path)
    return symbols


def _fetch_live_nifty500() -> list[str] | None:
    """Pull the official Nifty 500 list from NSE archives."""
    url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SwingScanner/1.0)"
        })
        if r.status_code != 200:
            return None
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        # NSE column is 'Symbol'
        col = next((c for c in df.columns if c.lower() == "symbol"), None)
        if col is None:
            return None
        symbols = df[col].dropna().astype(str).str.upper().str.strip().unique().tolist()
        log.info("Fetched live Nifty 500: %d symbols", len(symbols))
        return symbols
    except Exception as e:
        log.warning("Live Nifty 500 fetch failed: %s", e)
        return None
