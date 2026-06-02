"""
Backtest: which breakout lookback (N) is best — 15, 30, 45, 60, or 90?

Isolates ONE variable: the breakout lookback. Everything else is held fixed
to the scanner's OHLC-derivable signal (delivery data isn't available
historically, so we use the technical core of the live filter):

  signal day =  close > prev close            (pct_change > 0)
            AND close >= 200 EMA              (uptrend)
            AND volume >= 3x prev day volume  (volume spike)
            AND close >= prior-N-session high * (1 - tol)   (BREAKOUT  <-- the variable)

For each signal we simulate the strategy from the videos:
  entry  = next day's open (no lookahead)
  stop   = low of the signal candle
  target = entry * (1 + TARGET_PCT)
  walk forward up to HORIZON trading days; if both stop & target hit on the
  same bar, assume STOP first (conservative).

A per-symbol cooldown prevents stacking overlapping entries on one run-up.

Run:  python backtest_breakout.py            # full universe, 3y
      python backtest_breakout.py 150         # first 150 symbols (faster)
"""
from __future__ import annotations
import sys
import time

import numpy as np
import pandas as pd
import yfinance as yf

from swingscanner.universe import load_universe

LOOKBACKS   = [15, 30, 45, 60, 90]
TOL         = 0.02      # breakout tolerance (matches scanner default)
MIN_VOL_RAT = 3.0       # volume >= 3x prev day
TARGET_PCT  = 0.30      # fixed +30% target (compare N on equal footing)
HORIZON     = 60        # max trading days to hold
PERIOD      = "3y"
BATCH       = 100


def fetch_all(symbols: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), BATCH):
        batch = symbols[i:i + BATCH]
        tickers = [f"{s}.NS" for s in batch]
        try:
            data = yf.download(tickers, period=PERIOD, progress=False,
                               group_by="ticker", threads=True, auto_adjust=False)
        except Exception as e:
            print(f"  batch {i} failed: {e}")
            continue
        if data is None or data.empty:
            continue
        for s, t in zip(batch, tickers):
            try:
                df = data[t].dropna() if len(batch) > 1 else data.dropna()
            except (KeyError, AttributeError):
                continue
            if df is None or len(df) < 260:
                continue
            out[s] = df
        print(f"  fetched {min(i + BATCH, len(symbols))}/{len(symbols)} "
              f"(kept {len(out)})")
    return out


FWD = [20, 40]  # fixed-horizon lookaheads for the exit-rule-independent lens


def simulate(df: pd.DataFrame, sig_idx: int) -> dict | None:
    """Return realized return (target/stop sim) + raw fwd returns for a signal."""
    n = len(df)
    if sig_idx + 1 >= n or sig_idx + HORIZON >= n:
        return None  # need an entry bar + a full forward window
    o = df["Open"].to_numpy(); lo_a = df["Low"].to_numpy()
    hi_a = df["High"].to_numpy(); cl = df["Close"].to_numpy()
    entry = float(o[sig_idx + 1])
    if entry <= 0:
        return None
    stop = float(lo_a[sig_idx])                     # signal-candle low
    target = entry * (1 + TARGET_PCT)
    realized = None
    for j in range(sig_idx + 1, sig_idx + 1 + HORIZON):
        if lo_a[j] <= stop:                         # stop first (conservative)
            realized = stop / entry - 1
            break
        if hi_a[j] >= target:
            realized = TARGET_PCT
            break
    if realized is None:
        realized = cl[sig_idx + HORIZON] / entry - 1
    out = {"r": realized, "stop_dist": 1 - stop / entry}
    for h in FWD:
        out[f"fwd{h}"] = cl[sig_idx + h] / entry - 1
    return out


def run() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    symbols = load_universe("nifty500.csv")
    if limit:
        symbols = symbols[:limit]
    print(f"Universe: {len(symbols)} symbols | period={PERIOD} | "
          f"target={TARGET_PCT:.0%} stop=candle-low horizon={HORIZON}d")

    t0 = time.time()
    data = fetch_all(symbols)
    print(f"Fetched {len(data)} usable symbols in {time.time() - t0:.0f}s\n")

    # pre-compute the N-independent parts once per symbol
    prepared = []
    for sym, df in data.items():
        df = df.copy()
        c = df["Close"]
        df["ema200"] = c.ewm(span=200, adjust=False).mean()
        df["vol_rat"] = df["Volume"] / df["Volume"].shift(1)
        df["pctchg"] = c.pct_change()
        base_ok = (df["pctchg"] > 0) & (c >= df["ema200"]) & (df["vol_rat"] >= MIN_VOL_RAT)
        prepared.append((sym, df, base_ok.to_numpy()))

    results: dict[int, list[dict]] = {N: [] for N in LOOKBACKS}
    for N in LOOKBACKS:
        for sym, df, base_ok in prepared:
            c = df["Close"].to_numpy()
            hi = df["High"].to_numpy()
            prior_high = pd.Series(hi).shift(1).rolling(N).max().to_numpy()
            brk = c >= prior_high * (1 - TOL)
            sig = base_ok & brk
            cooldown_until = -1
            for i in np.nonzero(sig)[0]:
                if i <= cooldown_until or i < N:
                    continue
                o = simulate(df, int(i))
                if o is None:
                    continue
                results[N].append(o)
                cooldown_until = i + HORIZON  # block re-entry during the hold

    print("TARGET/STOP SIM  (entry=next open, stop=candle low, target=+30%, "
          f"{HORIZON}d max)")
    print(f"{'N':>4} | {'#trades':>7} | {'win%':>6} | {'expectancy':>10} | "
          f"{'avg stop':>8}")
    print("-" * 50)
    for N in LOOKBACKS:
        rs = results[N]
        if not rs:
            print(f"{N:>4} |       0 |    -   |     -      |    -")
            continue
        r = np.array([x["r"] for x in rs])
        sd = np.array([x["stop_dist"] for x in rs])
        win = (r >= TARGET_PCT - 1e-9).mean() * 100
        print(f"{N:>4} | {len(rs):>7} | {win:>5.1f}% | {r.mean():>+9.2%} | "
              f"{sd.mean():>7.1%}")

    print("\nRAW FORWARD RETURN  (exit-rule-independent: buy next open, hold "
          "fixed days)")
    hdr = "".join(f" | fwd{h:>2}d avg/med" for h in FWD)
    print(f"{'N':>4} | {'#trades':>7}{hdr}")
    print("-" * (18 + 17 * len(FWD)))
    for N in LOOKBACKS:
        rs = results[N]
        if not rs:
            continue
        cells = f"{N:>4} | {len(rs):>7}"
        for h in FWD:
            v = np.array([x[f"fwd{h}"] for x in rs])
            cells += f" | {v.mean():>+6.2%}/{np.median(v):>+6.2%}"
        print(cells)
    print("\nwin% = hit +30% before the candle-low stop. Higher expectancy & "
          "higher fwd avg = better N.")


if __name__ == "__main__":
    run()
