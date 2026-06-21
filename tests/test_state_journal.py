import json

import pytest

from magic_agent.state_journal import IntegrityError, StateJournal


def test_corrupt_state_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"payload":{},"sha256":"wrong"}')
    with pytest.raises(IntegrityError):
        StateJournal(path).load()


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    journal = StateJournal(path)
    payload = {"foo": "bar", "count": 42}
    journal.save(payload)
    loaded = journal.load()
    assert loaded == payload


def test_atomic_write_uses_temp_then_replace(tmp_path):
    """Verify atomic write: temp file is cleaned up and final file exists."""
    path = tmp_path / "state.json"
    journal = StateJournal(path)
    journal.save({"x": 1})
    # After save, the .tmp file must be gone (os.replace consumed it)
    assert not path.with_suffix(".tmp").exists()
    assert path.exists()


def test_truncated_file_detected_as_corrupt(tmp_path):
    """A truncated/partial write that has wrong sha256 raises IntegrityError."""
    path = tmp_path / "state.json"
    # Write a valid file first
    journal = StateJournal(path)
    journal.save({"key": "value"})
    # Now corrupt it by mangling the sha256 field
    raw = json.loads(path.read_text())
    raw["sha256"] = "deadbeef" * 8  # wrong hash
    path.write_text(json.dumps(raw))
    with pytest.raises(IntegrityError):
        journal.load()


def test_prior_good_state_survives_failed_write(tmp_path, monkeypatch):
    """If a write fails mid-way, the prior good state is still loadable."""
    path = tmp_path / "state.json"
    journal = StateJournal(path)
    journal.save({"generation": 1})

    import os as _os

    original_replace = _os.replace

    def failing_replace(src, dst):
        raise OSError("simulated disk full")

    monkeypatch.setattr(_os, "replace", failing_replace)
    with pytest.raises(OSError):
        journal.save({"generation": 2})

    monkeypatch.setattr(_os, "replace", original_replace)
    # Prior good state must still be intact
    loaded = journal.load()
    assert loaded == {"generation": 1}
