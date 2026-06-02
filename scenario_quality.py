"""
Does SELECTIVITY (liquidity / relative strength) flip the strategy from losing
to winning? Re-uses backtest_full's engine, caches the fetched data so the
scenarios run instantly after the first fetch.

Runs the DEPLOYED config (N=30, candle stop, +30%) over the full 2y under
increasing quality floors.
"""
from __future__ import annotations
import os
import pickle
from dataclasses import replace

import numpy as np

import backtest_full as bf

CACHE = ".cache/backtest_nse_equity_4y.pkl"


def load_data():
    if os.path.exists(CACHE):
        print(f"Loading cached data {CACHE}")
        with open(CACHE, "rb") as f:
            return pickle.load(f)
    syms = bf.load_universe("nse_equity.csv")
    print(f"Fetching {len(syms)} symbols (first run only)...")
    data = bf.fetch_all(syms)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "wb") as f:
        pickle.dump(data, f)
    print(f"Cached → {CACHE}")
    return data


def quick_stats(trades, eq, cfg):
    if not trades:
        return None
    rets = np.array([t.ret for t in trades])
    gw = sum(t.pnl for t in trades if t.pnl > 0)
    gl = -sum(t.pnl for t in trades if t.pnl < 0)
    pf = gw / gl if gl > 0 else float("inf")
    total = eq.iloc[-1] / cfg.capital - 1
    roll = eq.cummax(); mdd = ((eq - roll) / roll).min()
    return dict(n=len(trades), win=(rets > 0).mean(), exp=rets.mean(),
                pf=pf, total=total, mdd=mdd)


def main():
    data = load_data()
    nifty = data.get("^NSEI")
    cal = bf.calendar(data)
    full = cal[max(0, len(cal) - bf.SIG_DAYS):]
    nwin = nifty["Close"].reindex(full).ffill()
    print(f"Window {full[0].date()}→{full[-1].date()} | "
          f"Nifty B&H {nwin.iloc[-1]/nwin.iloc[0]-1:+.1%}\n")

    base = bf.Config()
    scenarios = [
        ("turnover>=1cr (deployed)",          dict(min_turnover_cr=1)),
        ("turnover>=10cr",                    dict(min_turnover_cr=10)),
        ("turnover>=25cr",                    dict(min_turnover_cr=25)),
        ("turnover>=50cr",                    dict(min_turnover_cr=50)),
        ("turnover>=25cr + RS>0",             dict(min_turnover_cr=25, min_rs=0.0)),
        ("turnover>=25cr + RS>+5%",           dict(min_turnover_cr=25, min_rs=0.05)),
        ("turnover>=50cr + RS>+5% + price>=50", dict(min_turnover_cr=50, min_rs=0.05, min_price=50)),
    ]
    print(f"{'scenario':<40} | {'trades':>6} | {'win%':>5} | {'exp':>6} | "
          f"{'PF':>5} | {'P&L 2y':>7} | {'maxDD':>6}")
    print("-" * 92)
    for name, over in scenarios:
        cfg = replace(base, **over)
        sigs = bf.build_signals(data, nifty, cfg)
        tr, eq = bf.run_portfolio(data, sigs, cfg, full)
        s = quick_stats(tr, eq, cfg)
        if not s:
            print(f"{name:<40} | no trades")
            continue
        print(f"{name:<40} | {s['n']:>6} | {s['win']:>4.0%} | {s['exp']:>+5.1%} | "
              f"{s['pf']:>5.2f} | {s['total']:>+6.1%} | {s['mdd']:>5.0%}")


if __name__ == "__main__":
    main()
