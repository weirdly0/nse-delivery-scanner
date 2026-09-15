from datetime import date, datetime, timezone

import pandas as pd

import delivery_scanner as scanner
import function_app
from test_session_freshness import bars, bhav
from swingscanner import nse_data


def test_azure_runs_in_requested_evening_window():
    bindings = function_app.app.get_functions()[0].get_bindings_dict()['bindings']
    timer = next(b for b in bindings if b['type'] == 'timerTrigger')
    sec, minute, hour, *_ = timer['schedule'].split()
    minute_ist = (int(hour) * 60 + int(minute) + 330) % 1440
    assert 22 * 60 <= minute_ist <= 23 * 60
    assert timer['runOnStartup'] is False


def test_after_midnight_defaults_to_ist_date_without_old_session(scan_env, monkeypatch):
    class Midnight(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 9, 15, 19, 0, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(scanner, 'datetime', Midnight)
    candidates, report = scanner.run_scan(**scan_env)
    assert candidates == []
    assert '16 Sep 2026' in report
    assert 'not closed' in report


def test_explicit_replay_uses_requested_date_and_discards_future_bar(scan_env, monkeypatch):
    scan_env.update(min_pct_change=-100, min_vol_ratio_1d=0.1)
    df = bars()
    df.loc[pd.Timestamp('2026-09-16')] = [105, 200, 104, 190, 10000000]
    monkeypatch.setattr(scanner, 'fetch_ohlc_bulk', lambda *a, **k: {'TEST': df})
    candidates, report = scanner.run_scan(**scan_env, as_of=date(2026, 9, 15))
    assert candidates[0]['price'] == 105
    assert '15 Sep 2026' in report


def test_matching_current_session_still_emits_valid_setup(scan_env, monkeypatch):
    df = bars()
    df.loc[pd.Timestamp('2026-09-15')] = [121, 140, 119, 135, 2000000]
    monkeypatch.setattr(scanner, 'fetch_ohlc_bulk', lambda *a, **k: {'TEST': df})
    def download(day):
        if day.date() == date(2026, 9, 14):
            return None
        if day.date() == date(2026, 9, 15):
            return bhav(day, 135, 2000000, 100000)
        return bhav(day, 120, 400000) if day.date() == date(2026, 9, 11) else bhav(day)
    monkeypatch.setattr(nse_data.BhavcopyStore, '_download_bhavcopy', staticmethod(download))
    candidates, report = scanner.run_scan(**scan_env)
    assert len(candidates) == 1
    assert candidates[0]['session_date'] == '2026-09-15'
    assert candidates[0]['price'] == 135
    assert candidates[0]['vol_ratio_1d'] == 5
    assert candidates[0]['deliv_times'] == 10
    assert 'Completed market session' in report


def test_missing_previous_ohlc_session_is_not_previous_day_volume(scan_env, monkeypatch):
    scan_env.update(min_pct_change=-100, min_vol_ratio_1d=0.1)
    df = bars().drop(pd.Timestamp('2026-09-11'))
    df.loc[pd.Timestamp('2026-09-15')] = [121, 140, 119, 135, 2000000]
    monkeypatch.setattr(scanner, 'fetch_ohlc_bulk', lambda *a, **k: {'TEST': df})
    candidates, report = scanner.run_scan(**scan_env)
    assert candidates == []
    assert 'incomplete' in report


def test_utc_index_converts_to_indian_trading_date(scan_env, monkeypatch):
    scan_env.update(min_pct_change=-100, min_vol_ratio_1d=0.1)
    df = bars()
    df.index = df.index.tz_localize('Asia/Kolkata').tz_convert('UTC')
    monkeypatch.setattr(scanner, 'fetch_ohlc_bulk', lambda *a, **k: {'TEST': df})
    candidates, report = scanner.run_scan(**scan_env)
    assert candidates[0]['price'] == 105
    assert 'stale/misaligned' not in report


def test_fresh_nse_breakout_survives_yahoo_latest_bar_delay(scan_env, monkeypatch):
    scan_env.update(require_above_200ema=True, require_breakout=True)
    monkeypatch.setattr(scanner, 'fetch_ohlc_bulk', lambda *a, **k: {'TEST': bars('2026-09-11')})
    def download(day):
        if day.date() == date(2026, 9, 14):
            return None
        if day.date() == date(2026, 9, 15):
            return bhav(day, 135, 2000000, 100000)
        return bhav(day, 120, 400000) if day.date() == date(2026, 9, 11) else bhav(day)
    monkeypatch.setattr(nse_data.BhavcopyStore, '_download_bhavcopy', staticmethod(download))
    candidates, report = scanner.run_scan(**scan_env)
    assert len(candidates) == 1
    assert candidates[0]['price'] == 135
    assert candidates[0]['pct_change'] == 12.5
    assert candidates[0]['vol_ratio_1d'] == 5
    assert candidates[0]['session_date'] == '2026-09-15'
    assert 'incomplete' not in report
