# tests/test_status_store.py
"""Unit tests for the cross-process status snapshot store (F4).

``write_status_snapshot``/``read_status_snapshot`` are the pure, testable file
boundary shared by ``run`` (writer) and ``serve`` (reader). No network.
"""
from __future__ import annotations

from magic_agent.status_store import read_status_snapshot, write_status_snapshot


def test_write_then_read_roundtrips_the_dict(tmp_path):
    path = tmp_path / "status.json"
    status = {
        "mode": "paper",
        "venue": "binance",
        "halted": False,
        "equity": 1000.0,
        "available": 1000.0,
        "currency": "USDT",
        "realized_pnl": 0.0,
        "open_pnl": 0.0,
        "positions": [],
    }
    write_status_snapshot(str(path), status)
    assert path.exists()
    assert read_status_snapshot(str(path)) == status


def test_write_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "deeper" / "status.json"
    write_status_snapshot(str(path), {"mode": "paper"})
    assert path.exists()
    assert read_status_snapshot(str(path)) == {"mode": "paper"}


def test_read_absent_file_returns_none(tmp_path):
    path = tmp_path / "does-not-exist.json"
    assert read_status_snapshot(str(path)) is None


def test_read_unreadable_or_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "status.json"
    path.write_text("{ not valid json")
    assert read_status_snapshot(str(path)) is None
