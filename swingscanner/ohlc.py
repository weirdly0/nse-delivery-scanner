"""
OHLC + indicator computation.

Sources:
  - yfinance: fast bulk fetch, occasionally throttles
  - (extensible) nse: historical via nsepython.equity_history (slow, official)

We only need OHLCV here; delivery data comes from the bhavcopy module.
"""

from __future__ import annotations
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


def fetch_ohlc(
    symbol: str,
    days: int = 250,
    source: str = "yfinance",
) -> Optional[pd.DataFrame]:
    """Return DataFrame with [Open, High, Low, Close, Volume] indexed by date."""
    if source == "yfinance":
        return _fetch_yfinance(symbol, days)
    elif source == "nse":
        return _fetch_nse(symbol, days)
    else:
        raise ValueError(f"Unknown OHLC source: {source}")


def _fetch_yfinance(symbol: str, days: int) -> Optional[pd.DataFrame]:
    ticker = f"{symbol}.NS"
    end = datetime.now()
    start = end - timedelta(days=days + 50)
    try:
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if df is None or df.empty or len(df) < 60:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        return df.dropna()
    except Exception as e:
        log.debug("yfinance fetch failed for %s: %s", symbol, e)
        return None


def fetch_ohlc_bulk(
    symbols: list[str],
    days: int = 250,
    batch_size: int = 100,
) -> dict[str, pd.DataFrame]:
    """
    Bulk-download OHLC for many symbols in a few HTTP calls.
    Much friendlier to Yahoo than firing one request per ticker — avoids
    the rate-limit-induced empty responses you get from parallel singles.

    Returns {symbol: df} for symbols that came back with data.
    """
    end = datetime.now()
    start = end - timedelta(days=days + 50)
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    out: dict[str, pd.DataFrame] = {}

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        tickers = [f"{s}.NS" for s in batch]
        try:
            data = yf.download(
                tickers,
                start=start_s,
                end=end_s,
                progress=False,
                auto_adjust=False,
                threads=True,
                group_by="ticker",
            )
        except Exception as e:
            log.warning("Bulk OHLC fetch failed for batch %d-%d: %s",
                        i, i + len(batch), e)
            continue

        if data is None or data.empty:
            continue

        # yfinance returns a multi-level column DataFrame keyed by ticker
        # at the top level when group_by="ticker" and >1 ticker requested.
        if len(batch) == 1:
            df = data.dropna()
            if len(df) >= 60:
                out[batch[0]] = df
            continue

        for sym, tkr in zip(batch, tickers):
            try:
                df = data[tkr].dropna()
            except (KeyError, AttributeError):
                continue
            if df is None or df.empty or len(df) < 60:
                continue
            out[sym] = df

    log.info("Bulk OHLC: %d/%d symbols fetched", len(out), len(symbols))
    return out


def _fetch_nse(symbol: str, days: int) -> Optional[pd.DataFrame]:
    """Slow but official. Use only when yfinance is unreliable."""
    try:
        from nsepython import equity_history
        end = datetime.now()
        start = end - timedelta(days=days + 50)
        df = equity_history(
            symbol, "EQ",
            start.strftime("%d-%m-%Y"),
            end.strftime("%d-%m-%Y"),
        )
        if df is None or df.empty:
            return None
        # Normalize to yfinance-style columns
        rename = {
            "CH_OPENING_PRICE":   "Open",
            "CH_TRADE_HIGH_PRICE":"High",
            "CH_TRADE_LOW_PRICE": "Low",
            "CH_CLOSING_PRICE":   "Close",
            "CH_TOT_TRADED_QTY":  "Volume",
            "CH_TIMESTAMP":       "Date",
        }
        df = df.rename(columns=rename)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        log.debug("NSE fetch failed for %s: %s", symbol, e)
        return None


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """50/100/200 EMA + 20-day average volume + daily % change."""
    df = df.copy()
    df["ema50"]  = df["Close"].ewm(span=50,  adjust=False).mean()
    df["ema100"] = df["Close"].ewm(span=100, adjust=False).mean()
    df["ema200"] = df["Close"].ewm(span=200, adjust=False).mean()
    df["vol_avg20"] = df["Volume"].rolling(20).mean()
    # 20-day average daily turnover (price*volume) in ₹ crore — a liquidity
    # gauge. Backtest: a ~₹10cr floor flipped the strategy from losing to
    # winning by screening out thin/illiquid junk breakouts.
    df["turnover_cr"] = (df["Close"] * df["Volume"]).rolling(20).mean() / 1e7
    df["pct_change"] = df["Close"].pct_change() * 100
    return df


def fetch_market_cap_cr(symbol: str) -> Optional[float]:
    """Market cap in crores. Falls back gracefully if data is missing."""
    try:
        info = yf.Ticker(f"{symbol}.NS").fast_info
        mc = None
        # yfinance 0.2.x FastInfo: .get() returns None for these keys, but
        # subscript and attribute access both work. Try them in order.
        for key in ("market_cap", "marketCap"):
            try:
                v = info[key]
            except (KeyError, TypeError):
                v = None
            if v:
                mc = v
                break
        if mc is None:
            mc = getattr(info, "market_cap", None)
        if mc is None or mc <= 0:
            return None
        return float(mc) / 1e7
    except Exception:
        return None


def enrich_market_caps(candidates: list[dict], max_workers: int = 8) -> None:
    """Fetch market cap (₹ crore) in parallel for each candidate.

    Mutates the list in-place, setting 'market_cap_cr' to a float or None
    (None = couldn't fetch). Same parallel pattern as enrich_sectors.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not candidates:
        return
    syms = [c["symbol"] for c in candidates]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        caps = list(pool.map(fetch_market_cap_cr, syms))
    for c, mc in zip(candidates, caps):
        c["market_cap_cr"] = mc


def is_circuit_locked(df: pd.DataFrame, lookback: int = 20,
                      frozen_frac: float = 0.25, eps: float = 0.001) -> bool:
    """Heuristic detector for upper/lower-circuit (untradeable) stocks.

    A circuit-locked session has (almost) no intraday range: High ≈ Low.
    If today is frozen, or a large fraction of the last `lookback` sessions
    were frozen, the stock trades only by circuit (like the Shiv Om / GTX
    names in the video) and we skip it.
    """
    recent = df.tail(lookback)
    n = len(recent)
    if n == 0:
        return False
    rng_frac = (recent["High"] - recent["Low"]) / recent["Close"].clip(lower=1e-9)
    frozen = int((rng_frac < eps).sum())
    today_frozen = bool(rng_frac.iloc[-1] < eps)
    return today_frozen or (frozen / n >= frozen_frac)


def is_breakout(df: pd.DataFrame, lookback: int = 60,
                tolerance: float = 0.02) -> bool:
    """True when today closes at/above the prior `lookback`-session high.

    One rule covers both cases from the video: a *base breakout* (closing
    above a multi-week sideways range) and an *all-time-high breakout* (the
    same, when that range high is also the ATH). `tolerance` loosens 'above'
    to 'within tolerance below', so a stock sitting right at the breakout
    level still qualifies.
    """
    if len(df) < lookback + 1:
        return False
    prior_high = float(df["High"].iloc[-(lookback + 1):-1].max())
    if prior_high <= 0:
        return False
    today_close = float(df["Close"].iloc[-1])
    return today_close >= prior_high * (1.0 - tolerance)
