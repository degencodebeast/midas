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


def test_runbook_documents_live_canary_to_scoring_process():
    from pathlib import Path

    text = Path("docs/track1-spot-runbook.md").read_text(encoding="utf-8")

    assert "mandatory first live canary" in text
    assert "canary_risk_fraction = 0.0025" in text
    assert "promote to normal scoring mode" in text
    assert "minimum trade-count pace" in text
    assert "cost viability" in text
    assert "smart-money and LLM supervisor are deferred" in text
