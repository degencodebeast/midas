def test_agent_package_imports():
    import magic_agent
    assert magic_agent.__doc__ is not None


def test_scanner_dependency_is_importable():
    from magic_scanner.scan import scan_symbols  # editable path dep resolves
    assert callable(scan_symbols)


def test_runbook_has_supervised_live_canary_gate():
    from pathlib import Path

    text = Path("docs/track1-spot-runbook.md").read_text(encoding="utf-8")

    assert "Supervised live canary gate" in text
    assert "operator approval" in text
    assert "quote-only smoke" in text
    assert "confirmed and reconciled" in text
    assert "do not enable systemd" in text
