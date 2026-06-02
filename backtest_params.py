"""
Backtest two more parameters, holding N=30 (the winning lookback) fixed:

  TEST A — breakout tolerance:  0.00 / 0.01 / 0.02 / 0.03
           (how far below the prior-30 high a close still counts as breakout)

  TEST B — exit target:  fixed +30%  vs  market-cap tiered (large→+20%, small→+30%)
           large-cap = current market cap >= LARGE_CAP_CR (10,000 cr)

Same signal core and trade sim as backtest_breakout.py:
  signal = pctchg>0 AND close>=200EMA AND vol>=3x prev AND close>=prior-30 high*(1-tol)
  entry  = next open, stop = signal-candle low, horizon = 60 trading days.

Run:  python backtest_params.py            # full universe, 3y
      python backtest_params.py 200         # first 200 symbols (faster)
"""
from __future__ import annotations
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import yfinance as yf

from swingscanner.universe import load_universe
from swingscanner.ohlc import fetch_market_cap_cr

N            = 30
TOLERANCES   = [0.00, 0.01, 0.02, 0.03]
MIN_VOL_RAT  = 3.0
HORIZON      = 60
LARGE_CAP_CR = 10000.0
PERIOD       = "3y"
BATCH        = 100


def fetch_all(symbols: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), BATCH):
        batch = symbols[i:i + BATCH]
        tickers = [f"{s}.NS" for s in batch]
        try:
            data = yf.download(tickers, period=PERIOD, progress=False,
                               group_by="ticker", threads=True, auto_adjust=False)
        except Exception:
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


def sim_one(df_np, sig_idx: int, target: float) -> float | None:
    """Realized return for one signal at one target. df_np = (o, hi, lo, cl)."""
    o, hi, lo, cl = df_np
    n = len(cl)
    if sig_idx + HORIZON >= n:
        return None
    entry = o[sig_idx + 1]
    if entry <= 0:
        return None
    stop = lo[sig_idx]
    tgt = entry * (1 + target)
    for j in range(sig_idx + 1, sig_idx + 1 + HORIZON):
        if lo[j] <= stop:
            return stop / entry - 1
        if hi[j] >= tgt:
            return target
    return cl[sig_idx + HORIZON] / entry - 1


def run() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    symbols = load_universe("nifty500.csv")
    if limit:
        symbols = symbols[:limit]
    print(f"Universe: {len(symbols)} | N={N} | period={PERIOD} | "
          f"stop=candle-low horizon={HORIZON}d\n")

    t0 = time.time()
    data = fetch_all(symbols)
    print(f"Fetched {len(data)} usable symbols in {time.time() - t0:.0f}s")

    # market caps for the tier test (parallel)
    syms = list(data)
    with ThreadPoolExecutor(max_workers=8) as pool:
        caps = dict(zip(syms, pool.map(fetch_market_cap_cr, syms)))
    n_known = sum(1 for v in caps.values() if v)
    print(f"Market caps: {n_known}/{len(syms)} known\n")

    prepared = []
    for sym, df in data.items():
        c = df["Close"]
        ema200 = c.ewm(span=200, adjust=False).mean()
        vol_rat = df["Volume"] / df["Volume"].shift(1)
        base_ok = ((c.pct_change() > 0) & (c >= ema200) &
                   (vol_rat >= MIN_VOL_RAT)).to_numpy()
        prior_high = df["High"].shift(1).rolling(N).max().to_numpy()
        df_np = (df["Open"].to_numpy(), df["High"].to_numpy(),
                 df["Low"].to_numpy(), df["Close"].to_numpy())
        prepared.append((sym, df_np, base_ok, prior_high, c.to_numpy()))

    # ---------- TEST A: tolerance sweep (fixed +30% target) ----------
    print("TEST A — breakout tolerance (N=30, target=+30%)")
    print(f"{'tol':>5} | {'#trades':>7} | {'win%':>6} | {'expectancy':>10}")
    print("-" * 40)
    for tol in TOLERANCES:
        rs = []
        for sym, df_np, base_ok, prior_high, c in prepared:
            brk = c >= prior_high * (1 - tol)
            sig = base_ok & brk
            cd = -1
            for i in np.nonzero(sig)[0]:
                if i <= cd or i < N:
                    continue
                r = sim_one(df_np, int(i), 0.30)
                if r is None:
                    continue
                rs.append(r)
                cd = i + HORIZON
        r = np.array(rs)
        win = (r >= 0.30 - 1e-9).mean() * 100
        print(f"{tol:>5.2f} | {len(rs):>7} | {win:>5.1f}% | {r.mean():>+9.2%}")

    # ---------- TEST B: fixed 30% vs market-cap tiered ----------
    # collect per-trade (r@20%, r@30%, is_large) at tol=0.02
    rows = []
    for sym, df_np, base_ok, prior_high, c in prepared:
        mc = caps.get(sym)
        is_large = (mc is not None and mc >= LARGE_CAP_CR)
        brk = c >= prior_high * (1 - 0.02)
        sig = base_ok & brk
        cd = -1
        for i in np.nonzero(sig)[0]:
            if i <= cd or i < N:
                continue
            r20 = sim_one(df_np, int(i), 0.20)
            r30 = sim_one(df_np, int(i), 0.30)
            if r20 is None or r30 is None:
                continue
            rows.append((r20, r30, is_large, mc is not None))
            cd = i + HORIZON

    arr = np.array([(a, b, c, d) for a, b, c, d in rows], dtype=float)
    r20, r30, large, known = arr[:, 0], arr[:, 1], arr[:, 2] > 0.5, arr[:, 3] > 0.5
    fixed30 = r30.mean()
    # tiered: large-cap uses 20% exit, everyone else 30%
    tiered = np.where(large, r20, r30).mean()
    n_large = int(large.sum())

    print("\nTEST B — exit target  (N=30, tol=0.02)")
    print(f"  trades: {len(rows)}  |  large-cap (>= {LARGE_CAP_CR:,.0f} cr): "
          f"{n_large}  |  small/mid + unknown: {len(rows) - n_large}")
    print(f"  {'fixed +30% for all':<34}: expectancy {fixed30:>+7.2%}")
    print(f"  {'tiered (large +20%, rest +30%)':<34}: expectancy {tiered:>+7.2%}")
    # split detail
    if n_large:
        print(f"\n  large-cap only:  +20% exit {r20[large].mean():>+7.2%}  vs  "
              f"+30% exit {r30[large].mean():>+7.2%}  ({n_large} trades)")
    sm = ~large
    print(f"  small/mid only:  +20% exit {r20[sm].mean():>+7.2%}  vs  "
          f"+30% exit {r30[sm].mean():>+7.2%}  ({int(sm.sum())} trades)")


if __name__ == "__main__":
    run()
