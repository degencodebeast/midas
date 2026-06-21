from datetime import datetime, timedelta, timezone

from magic_agent.watchlist import PINNED_SYMBOLS, WatchlistState


def test_requested_six_are_pinned_for_h1_monitoring():
    assert PINNED_SYMBOLS == ("ZEC", "DEXE", "TRX", "APE", "LINK", "XRP")


def test_discovery_is_due_every_four_hours_but_h1_monitoring_is_always_due():
    now = datetime(2026, 6, 21, 8, tzinfo=timezone.utc)
    state = WatchlistState.initial()
    assert state.monitoring_due(now)
    assert state.discovery_due(now)
    state = state.mark_discovery(now)
    assert not state.discovery_due(now + timedelta(hours=3, minutes=59))
    assert state.discovery_due(now + timedelta(hours=4))
