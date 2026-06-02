"""
Realistic 2-year portfolio backtest of the breakout scanner — NO lookahead.

What it does (honest, point-in-time):
  - Signal uses only data up to & including the signal day's close.
  - Entry = NEXT day's open.  Stop = signal-day low.  Exits walked forward bar
    by bar (target hit / stop hit / max-hold timeout).
  - Capital-constrained PORTFOLIO: fixed starting capital, max N concurrent
    positions, equal fraction per slot. When more setups fire on a day than
    free slots, the strongest (highest volume ratio) are taken first — like a
    real trader picking the best setups.
  - Point-in-time-safe quality filters only (turnover floor, price floor,
    relative strength vs Nifty). Deep accounting fundamentals are NOT used
    (can't be reconstructed point-in-time for free → would be hindsight).

Reports: total return vs Nifty buy&hold, CAGR, win rate, avg win/loss,
expectancy, profit factor, max drawdown, # trades, avg hold.

Usage:  python backtest_full.py [universe_csv] [limit]
        python backtest_full.py nifty500.csv 200      # fast subset
        python backtest_full.py nse_equity.csv         # full production universe
"""
from __future__ import annotations
import sys
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yfinance as yf

from swingscanner.universe import load_universe

PERIOD   = "4y"      # fetch 4y; use last ~2y for signals, rest warms up 200 EMA
BATCH    = 100
SIG_DAYS = 504       # ~2 trading years of signal window (last N bars)


@dataclass
class Config:
    lookback: int = 30          # breakout base length
    tol: float = 0.01           # breakout tolerance
    vol_ratio: float = 3.0      # today vol >= x * prev day
    target: float = 0.30        # take-profit
    stop_mode: str = "candle"   # "candle" (signal low) or a float like 0.10
    max_hold: int = 60          # trading days
    require_200ema: bool = True
    # portfolio
    capital: float = 1_000_000
    max_positions: int = 8
    # point-in-time-safe quality overlay
    min_turnover_cr: float = 1.0   # avg 20d (price*vol) >= this many ₹cr
    min_price: float = 20.0
    max_price: float = 1e9         # affordability cap for small capital
    min_rs: float = 0.0            # stock 60d return minus Nifty 60d return >= this


@dataclass
class Trade:
    sym: str
    entry_date: pd.Timestamp
    entry: float
    stop: float
    target: float
    shares: int
    exit_date: pd.Timestamp = None
    exit: float = 0.0
    reason: str = ""

    @property
    def ret(self) -> float:
        return self.exit / self.entry - 1

    @property
    def pnl(self) -> float:
        return (self.exit - self.entry) * self.shares


def fetch_all(symbols, extra=("^NSEI",)):
    out = {}
    syms = list(symbols) + list(extra)
    for i in range(0, len(syms), BATCH):
        batch = syms[i:i + BATCH]
        tickers = [s if s.startswith("^") else f"{s}.NS" for s in batch]
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
            if df is not None and len(df) >= 300:
                out[s] = df
        print(f"  fetched {min(i + BATCH, len(syms))}/{len(syms)} (kept {len(out)})")
    return out


def build_signals(data, nifty, cfg: Config):
    """Return a DataFrame of all signals: date, sym, entry/stop/target inputs,
    volume ratio (for ranking). Point-in-time; no future leakage."""
    nclose = nifty["Close"]
    nret60 = nclose.pct_change(60)
    rows = []
    for sym, df in data.items():
        if sym.startswith("^") or len(df) < 260:
            continue
        c = df["Close"]; h = df["High"]; v = df["Volume"]
        ema200 = c.ewm(span=200, adjust=False).mean()
        vol_rat = v / v.shift(1)
        turn_cr = (c * v).rolling(20).mean() / 1e7
        prior_high = h.shift(1).rolling(cfg.lookback).max()
        rs = c.pct_change(60) - nret60.reindex(c.index).ffill()
        cond = (
            (c.pct_change() > 0) &
            (vol_rat >= cfg.vol_ratio) &
            (c >= prior_high * (1 - cfg.tol)) &
            (c >= cfg.min_price) &
            (c <= cfg.max_price) &
            (turn_cr >= cfg.min_turnover_cr) &
            (rs >= cfg.min_rs)
        )
        if cfg.require_200ema:
            cond &= (c >= ema200)
        # restrict to last SIG_DAYS bars (the 2y window) but leave room to exit
        idx = np.nonzero(cond.to_numpy())[0]
        start = max(len(df) - SIG_DAYS, cfg.lookback + 1)
        for i in idx:
            if i < start or i + 1 >= len(df):
                continue
            rows.append({
                "date": df.index[i], "sym": sym, "i": int(i),
                "vol_rat": float(vol_rat.iloc[i]),
                "low": float(df["Low"].iloc[i]),
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def calendar(data) -> pd.DatetimeIndex:
    all_dates = sorted(set().union(*[set(df.index) for s, df in data.items()
                                     if not s.startswith("^")]))
    return pd.DatetimeIndex(all_dates)


def run_portfolio(data, signals, cfg: Config, win_dates: pd.DatetimeIndex):
    """Event-driven portfolio sim over the given window. Returns (trades, eq)."""
    if signals.empty:
        return [], pd.Series(dtype=float)
    all_dates = win_dates
    sig_by_date = {d: g for d, g in signals.groupby("date")}

    cash = cfg.capital
    open_pos: list[Trade] = []
    closed: list[Trade] = []
    equity = []

    for d in all_dates:
        # 1) manage exits on open positions (check today's bar)
        still = []
        for tr in open_pos:
            df = data[tr.sym]
            if d not in df.index:
                still.append(tr); continue
            row = df.loc[d]
            lo, hi, cl = float(row["Low"]), float(row["High"]), float(row["Close"])
            exited = False
            if lo <= tr.stop:                       # stop first (conservative)
                tr.exit, tr.reason = tr.stop, "stop"; exited = True
            elif hi >= tr.target:
                tr.exit, tr.reason = tr.target, "target"; exited = True
            else:
                tr._bars += 1
                if tr._bars >= cfg.max_hold:
                    tr.exit, tr.reason = cl, "timeout"; exited = True
            if exited:
                tr.exit_date = d
                cash += tr.exit * tr.shares
                closed.append(tr)
            else:
                still.append(tr)
        open_pos = still

        # 2) new entries: signals that fired YESTERDAY enter at today's open
        #    (we stored signal date = signal day; entry is next bar = today)
        di = all_dates.get_loc(d)
        if di == 0:
            equity.append((d, cash + sum(_mark(tr, data, d) for tr in open_pos)))
            continue
        prev_d = all_dates[di - 1]
        todays = sig_by_date.get(prev_d)
        if todays is not None and len(open_pos) < cfg.max_positions:
            # rank strongest setups first
            todays = todays.sort_values("vol_rat", ascending=False)
            for _, s in todays.iterrows():
                if len(open_pos) >= cfg.max_positions:
                    break
                df = data[s["sym"]]
                if d not in df.index:
                    continue
                entry = float(df.loc[d, "Open"])
                if entry <= 0:
                    continue
                stop = s["low"] if cfg.stop_mode == "candle" \
                    else entry * (1 - float(cfg.stop_mode))
                if stop >= entry:
                    continue
                slot_cash = cfg.capital / cfg.max_positions
                budget = min(slot_cash, cash)
                shares = int(budget // entry)
                if shares <= 0:
                    continue
                tr = Trade(sym=s["sym"], entry_date=d, entry=entry, stop=stop,
                           target=entry * (1 + cfg.target), shares=shares)
                tr._bars = 0
                cash -= entry * shares
                open_pos.append(tr)

        equity.append((d, cash + sum(_mark(tr, data, d) for tr in open_pos)))

    # close any still-open at last close
    last = all_dates[-1]
    for tr in open_pos:
        df = data[tr.sym]
        tr.exit = float(df["Close"].iloc[-1]); tr.exit_date = last
        tr.reason = "open_end"; cash += tr.exit * tr.shares
        closed.append(tr)

    eq = pd.Series({d: v for d, v in equity})
    return closed, eq


def _mark(tr, data, d):
    df = data[tr.sym]
    px = float(df.loc[d, "Close"]) if d in df.index else tr.entry
    return px * tr.shares


def stats(trades, equity, nifty, cfg: Config, label=""):
    if not trades:
        print(f"{label}: no trades"); return None
    rets = np.array([t.ret for t in trades])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    final = equity.iloc[-1]; start = cfg.capital
    total_ret = final / start - 1
    yrs = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (final / start) ** (1 / yrs) - 1 if yrs > 0 else 0
    roll_max = equity.cummax()
    mdd = ((equity - roll_max) / roll_max).min()
    gross_win = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    avg_hold = np.mean([(t.exit_date - t.entry_date).days for t in trades])
    # nifty buy&hold over same window
    nwin = nifty["Close"].reindex(equity.index).ffill()
    n_ret = nwin.iloc[-1] / nwin.iloc[0] - 1

    print(f"\n===== {label} =====")
    print(f"  config: N={cfg.lookback} tol={cfg.tol} vol≥{cfg.vol_ratio} "
          f"target={cfg.target:.0%} stop={cfg.stop_mode} hold={cfg.max_hold} "
          f"maxpos={cfg.max_positions} turn≥{cfg.min_turnover_cr}cr rs≥{cfg.min_rs}")
    print(f"  trades:      {len(trades)}   avg hold {avg_hold:.0f} days")
    print(f"  win rate:    {(rets > 0).mean():.1%}   "
          f"avg win {wins.mean() if len(wins) else 0:+.1%}   "
          f"avg loss {losses.mean() if len(losses) else 0:+.1%}")
    print(f"  expectancy:  {rets.mean():+.2%} / trade   profit factor {pf:.2f}")
    print(f"  TOTAL P&L:   {total_ret:+.1%}  (₹{final:,.0f} from ₹{start:,.0f})")
    print(f"  CAGR:        {cagr:+.1%}   max drawdown {mdd:.1%}")
    print(f"  Nifty B&H:   {n_ret:+.1%}  over same window")
    by_reason = pd.Series([t.reason for t in trades]).value_counts().to_dict()
    print(f"  exits:       {by_reason}")
    return {"total": total_ret, "cagr": cagr, "win": (rets > 0).mean(),
            "exp": rets.mean(), "pf": pf, "mdd": mdd, "n": len(trades)}


import itertools
from dataclasses import replace


def _score(trades, eq, cfg):
    """Train-window score for ranking configs: profit factor, gated on a
    minimum trade count so we don't pick a lucky 3-trade config."""
    if len(trades) < 20:
        return -1
    gw = sum(t.pnl for t in trades if t.pnl > 0)
    gl = -sum(t.pnl for t in trades if t.pnl < 0)
    pf = gw / gl if gl > 0 else 5.0
    return min(pf, 5.0)


def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else "nifty500.csv"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    syms = load_universe(csv)
    if limit:
        syms = syms[:limit]
    print(f"Universe {csv}: {len(syms)} symbols | period={PERIOD}\n")
    t0 = time.time()
    data = fetch_all(syms)
    nifty = data.get("^NSEI")
    print(f"Fetched {len(data)} ({time.time()-t0:.0f}s); "
          f"nifty={'ok' if nifty is not None else 'MISSING'}\n")

    cal = calendar(data)
    full_win = cal[max(0, len(cal) - SIG_DAYS):]
    half = len(full_win) // 2
    y1, y2 = full_win[:half], full_win[half:]   # year1 (train) / year2 (OOS test)
    print(f"Window: {full_win[0].date()} → {full_win[-1].date()} "
          f"({len(full_win)}d)\n  year1(train) {y1[0].date()}→{y1[-1].date()}  |  "
          f"year2(OOS)  {y2[0].date()}→{y2[-1].date()}\n")

    base = Config()

    # signal generation depends only on lookback (tol/vol/quality fixed), so
    # build once per lookback and reuse across all stop/target sims.
    LOOKBACKS = [20, 30, 45]
    print("Building signals per lookback...")
    sig_by_lb = {}
    for lb in LOOKBACKS:
        sig_by_lb[lb] = build_signals(data, nifty, replace(base, lookback=lb))
        print(f"  N={lb}: {len(sig_by_lb[lb])} signals")

    # ---- HEADLINE: actual deployed config over the FULL 2 years (no tuning) ----
    stats(*run_portfolio(data, sig_by_lb[30], base, full_win), nifty, base,
          label="DEPLOYED config (N=30, candle stop, +30%) — FULL 2y, NO tuning")

    # ---- grid search on YEAR 1, lock, then test OUT-OF-SAMPLE on YEAR 2 ----
    grid_stop = ["candle", 0.08, 0.12]
    grid_tgt = [0.20, 0.30, 0.40]
    combos = [(lb, sm, tg) for lb in LOOKBACKS
              for sm in grid_stop for tg in grid_tgt]
    print(f"\nGrid search: {len(combos)} configs trained on YEAR 1...")
    trained = []
    for lb, sm, tg in combos:
        cfg = replace(base, lookback=lb, stop_mode=sm, target=tg)
        tr, eq = run_portfolio(data, sig_by_lb[lb], cfg, y1)
        if not tr:
            continue
        rets = np.array([t.ret for t in tr])
        trained.append((_score(tr, eq, cfg), cfg, len(tr), (rets > 0).mean()))
    trained.sort(key=lambda x: x[0], reverse=True)

    print(f"\n{'rank':>4} | N  stop  tgt  | y1 trades | y1 win% | y1 PF")
    print("-" * 50)
    for r, (sc, cfg, n, win) in enumerate(trained[:8], 1):
        sm = cfg.stop_mode if cfg.stop_mode == "candle" else f"{cfg.stop_mode:.0%}"
        print(f"{r:>4} | {cfg.lookback:>2} {sm:>5} {cfg.target:>3.0%} | "
              f"{n:>9} | {win:>6.0%} | {sc:>5.2f}")

    if trained:
        best = trained[0][1]
        print("\n>>> Locking YEAR-1 winner, testing untouched on YEAR 2 <<<")
        stats(*run_portfolio(data, sig_by_lb[best.lookback], best, y2),
              nifty, best, label="YEAR-1 WINNER — OUT-OF-SAMPLE (year 2)")
        # for reference: how the deployed config did on the same OOS year
        stats(*run_portfolio(data, sig_by_lb[30], base, y2), nifty, base,
              label="DEPLOYED config — same OOS year 2 (apples-to-apples)")


if __name__ == "__main__":
    main()
