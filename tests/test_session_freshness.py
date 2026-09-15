"""Regression tests for the September mixed-session scanner alert."""
from datetime import datetime, timedelta

import pandas as pd
import pytest

import delivery_scanner as scanner
from swingscanner import nse_data, ohlc


class Evening(datetime):
    @classmethod
    def now(cls, tz=None):
        # 22:30 IST is still September 15 in UTC.
        value = cls(2026, 9, 15, 17, 0)
        if tz is not None:
            from datetime import timezone
            return value.replace(tzinfo=timezone.utc).astimezone(tz)
        return value


def bars(last='2026-09-15'):
    dates = pd.bdate_range(end='2026-09-11', periods=220)
    df = pd.DataFrame({'Open': 100., 'High': 102., 'Low': 99.,
                       'Close': 100., 'Volume': 100000.}, index=dates)
    # Old session looks like a buy. Current session reverses it.
    df.loc[pd.Timestamp('2026-09-11')] = [100, 121, 99, 120, 400000]
    if last == '2026-09-15':
        df.loc[pd.Timestamp(last)] = [121, 122, 104, 105, 200000]
    return df


def bhav(day, close=100., volume=100000, delivery=10000):
    return pd.DataFrame({'SYMBOL': ['TEST'], ' SERIES': [' EQ'],
                         ' DATE1': [day.strftime('%d-%b-%Y')],
                         ' OPEN_PRICE': [close], ' HIGH_PRICE': [close + 2],
                         ' LOW_PRICE': [close - 2], ' PREV_CLOSE': [120.],
                         ' CLOSE_PRICE': [close], ' TTL_TRD_QNTY': [volume],
                         ' DELIV_QTY': [delivery], ' DELIV_PER': [50.]})


@pytest.fixture
def scan_env(monkeypatch, tmp_path):
    monkeypatch.setattr(scanner, 'datetime', Evening)
    monkeypatch.setattr(ohlc, 'datetime', Evening)
    monkeypatch.setattr(scanner, 'load_universe', lambda _: ['TEST'])
    monkeypatch.setattr(scanner, 'enrich_sectors', lambda _: None)
    monkeypatch.setattr(scanner, 'enrich_market_caps', lambda _: None)
    monkeypatch.setattr(scanner, 'fetch_ohlc_bulk', lambda *a, **k: {'TEST': bars()})

    def download(day):
        if day.date() == datetime(2026, 9, 14).date():
            return None  # exchange holiday
        if day.date() == datetime(2026, 9, 15).date():
            return bhav(day, 105, 200000, 100000)
        return bhav(day)

    monkeypatch.setattr(nse_data.BhavcopyStore, '_download_bhavcopy', staticmethod(download))
    return dict(cache_dir=str(tmp_path), min_market_cap_cr=0, min_turnover_cr=0,
                require_above_200ema=False, require_breakout=False, skip_circuit=False)


def test_bulk_download_includes_current_session(monkeypatch):
    monkeypatch.setattr(ohlc, 'datetime', Evening)
    def download(*args, **kwargs):
        df = bars()
        # Yahoo's actual exclusive-end contract.
        return df[df.index < pd.Timestamp(kwargs['end'])]
    monkeypatch.setattr(ohlc.yf, 'download', download)
    result = ohlc.fetch_ohlc_bulk(['TEST'])
    assert result['TEST'].index[-1] == pd.Timestamp('2026-09-15')


def test_bulk_one_ticker_normalizes_yahoo_multiindex(monkeypatch):
    data = bars()
    data.columns = pd.MultiIndex.from_product([['TEST.NS'], data.columns])
    monkeypatch.setattr(ohlc.yf, 'download', lambda *a, **k: data)
    result = ohlc.fetch_ohlc_bulk(['TEST'])
    assert list(result['TEST'].columns) == ['Open', 'High', 'Low', 'Close', 'Volume']


def test_previous_session_history_uses_current_nse_bar(scan_env, monkeypatch):
    monkeypatch.setattr(scanner, 'fetch_ohlc_bulk', lambda *a, **k: {'TEST': bars('2026-09-11')})
    candidates, report = scanner.run_scan(**scan_env)
    assert candidates == []
    assert 'incomplete' not in report.lower()
    assert '15 Sep 2026' in report


def test_missing_today_bhavcopy_does_not_relabel_old_signals(scan_env, monkeypatch):
    monkeypatch.setattr(nse_data.BhavcopyStore, '_download_bhavcopy',
                        staticmethod(lambda day: None if day.day == 15 else bhav(day)))
    candidates, report = scanner.run_scan(**scan_env)
    assert candidates == []
    assert 'pending' in report.lower()
    assert '15 Sep 2026' in report


def test_wrong_dated_download_is_not_cached(tmp_path, monkeypatch):
    requested = datetime(2026, 9, 15)
    monkeypatch.setattr(nse_data.BhavcopyStore, '_download_bhavcopy',
                        staticmethod(lambda day: bhav(day - timedelta(days=1))))
    store = nse_data.BhavcopyStore(tmp_path)
    assert store.get_day(requested) is None
    assert not list(tmp_path.glob('*.csv'))


def test_wrong_dated_cache_is_refetched(tmp_path, monkeypatch):
    requested = datetime(2026, 9, 15)
    bhav(requested - timedelta(days=1)).to_csv(tmp_path / 'bhav_15092026.csv', index=False)
    monkeypatch.setattr(nse_data.BhavcopyStore, '_download_bhavcopy',
                        staticmethod(lambda day: bhav(day, close=105)))
    result = nse_data.BhavcopyStore(tmp_path).get_day(requested)
    assert result[' CLOSE_PRICE'].iloc[0] == 105


def test_delivery_baseline_respects_requested_sessions():
    end = datetime(2026, 9, 15)
    data = {end: bhav(end, delivery=100000)}
    for offset in range(1, 5):
        day = end - timedelta(days=offset)
        data[day] = bhav(day, delivery=10000 if offset <= 2 else 50000)
    # Requested lookback must not silently become the entire cache history.
    result = nse_data.compute_delivery_ratios(data, lookback=2)
    assert result.loc['TEST', 'deliv_ratio'] == 10


def test_current_day_reversal_is_not_a_setup(scan_env):
    candidates, report = scanner.run_scan(**scan_env)
    assert candidates == []
    assert '15 Sep 2026' in report


def test_same_date_partial_yahoo_bar_is_replaced_by_final_nse_bar(scan_env, monkeypatch):
    df = bars()
    df.loc[pd.Timestamp('2026-09-15')] = [121, 140, 119, 135, 2000000]
    monkeypatch.setattr(scanner, 'fetch_ohlc_bulk', lambda *a, **k: {'TEST': df})
    # NSE's final close/volume are 105/200000; Yahoo's same-date bar is stale.
    candidates, report = scanner.run_scan(**scan_env)
    assert candidates == []
    assert 'incomplete' not in report


def test_missing_delivery_quantity_cannot_create_infinite_spike():
    end = datetime(2026, 9, 15)
    data = {end: bhav(end, delivery=100000),
            end - timedelta(days=1): bhav(end - timedelta(days=1), delivery=0)}
    result = nse_data.compute_delivery_ratios(data, lookback=1)
    assert result.empty


def test_nse_rejections_do_not_depend_on_yahoo_availability(scan_env, monkeypatch):
    def unavailable_yahoo(*args, **kwargs):
        pytest.fail('A negative NSE session must be rejected before requesting Yahoo history')
    monkeypatch.setattr(scanner, 'fetch_ohlc_bulk', unavailable_yahoo)
    candidates, report = scanner.run_scan(**scan_env)
    assert candidates == []
    assert 'incomplete' not in report
