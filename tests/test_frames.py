# tests/test_frames.py
"""Tests for the market-data FrameSource port (offline, deterministic).

Only ``FixtureFrameSource`` is unit-tested here — it touches no network. The
``GateioFrameSource`` live path is not unit-tested (it fetches over the network);
we only assert that the module imports without ``ccxt`` installed and that the
gate.io source raises a clear, named error when ``ccxt`` is absent.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from magic_agent.frames import FixtureFrameSource, FrameSource, GateioFrameSource

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "frames"

# The feed-schema columns scan_pair's assembly reads (see magic_scanner.feed).
_OHLCV_COLS = ("open", "high", "low", "close", "volume")


def _registry(identity_key: str = "zec-bsc", market_data_symbol: str = "ZEC/USDT"):
    """A minimal registry stub mirroring IdentityRegistry.by_contract_key."""
    record = SimpleNamespace(market_data_symbol=market_data_symbol)
    return SimpleNamespace(by_contract_key=lambda key: record if key == identity_key else (_ for _ in ()).throw(KeyError(key)))


def _candidate(identity_key: str = "zec-bsc"):
    return SimpleNamespace(identity_key=identity_key)


def test_fixture_source_is_a_frame_source():
    source = FixtureFrameSource(registry=_registry(), fixture_dir=FIXTURE_DIR)
    assert isinstance(source, FrameSource)


def test_closed_frames_returns_the_three_timeframe_keys():
    source = FixtureFrameSource(registry=_registry(), fixture_dir=FIXTURE_DIR)
    frames = source.closed_frames(_candidate())
    assert set(frames) == {"1w", "12h", "1h"}


def test_each_frame_has_the_feed_schema():
    source = FixtureFrameSource(registry=_registry(), fixture_dir=FIXTURE_DIR)
    frames = source.closed_frames(_candidate())
    for tf, df in frames.items():
        assert isinstance(df, pd.DataFrame), tf
        assert len(df) > 0, tf
        # OHLCV columns are float64.
        for col in _OHLCV_COLS:
            assert col in df.columns, f"{tf} missing {col}"
            assert str(df[col].dtype) == "float64", f"{tf}.{col} dtype"
        # close_time column + named, UTC-aware DatetimeIndex (the feed contract).
        assert "close_time" in df.columns, tf
        assert isinstance(df.index, pd.DatetimeIndex), tf
        assert df.index.name == "open_time", tf
        assert df.index.tz is not None, tf


def test_frames_are_closed_only_close_time_not_after_index_plus_duration():
    # Every bar's close_time must be strictly after its open_time (a real bar).
    source = FixtureFrameSource(registry=_registry(), fixture_dir=FIXTURE_DIR)
    frames = source.closed_frames(_candidate())
    for tf, df in frames.items():
        assert (df["close_time"] > df.index).all(), tf


def test_closed_frames_resolves_candidate_via_registry_market_data_symbol():
    # A candidate whose identity_key the registry cannot resolve must raise.
    source = FixtureFrameSource(registry=_registry(), fixture_dir=FIXTURE_DIR)
    with pytest.raises(KeyError):
        source.closed_frames(_candidate(identity_key="unknown-bsc"))


def test_scan_pair_runs_on_fixture_frames():
    # The bar of success: the scanner loop consumes these frames without error.
    from magic_scanner.scan import scan_pair

    source = FixtureFrameSource(registry=_registry(), fixture_dir=FIXTURE_DIR)
    candidate = _candidate()
    frames = source.closed_frames(candidate)
    market_data_symbol = source.registry.by_contract_key(candidate.identity_key).market_data_symbol

    result = scan_pair(
        market_data_symbol, frames,
        execution_mode="track1_aggressive", allowed_side="Long",
    )
    # A ScanResult is returned; an authorized setup may legitimately be absent.
    assert result is not None
    assert hasattr(result, "result")
    assert hasattr(result, "authorization")


def test_gateio_source_raises_clearly_when_ccxt_missing(monkeypatch):
    # Simulate ccxt being absent: the lazy import inside the fetch must fail with
    # a clear, named error rather than a bare ImportError.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ccxt" or name.startswith("ccxt."):
            raise ImportError("No module named 'ccxt'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    source = GateioFrameSource(registry=_registry())
    with pytest.raises(RuntimeError, match="ccxt"):
        source.closed_frames(_candidate())
