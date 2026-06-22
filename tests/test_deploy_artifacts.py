from pathlib import Path


def test_twak_vps_bringup_is_quote_only_and_existing_wallet_safe():
    text = Path("deploy/twak-vps-bringup.sh").read_text()

    assert "QUOTE-ONLY" in text
    assert "--quote-only" in text
    assert "twak wallet create" not in text
    assert "twak compete register" in text
    assert 'RUN_COMPETE_REGISTER:-0' in text
    assert "WALLET_ADDRESS" in text
    assert "No real swaps" in text


def test_systemd_unit_is_disabled_skeleton_with_env_file():
    text = Path("deploy/midas-agent.service").read_text()

    assert "EnvironmentFile=/etc/midas/agent.env" in text
    assert "ExecStart=/opt/midas/.venv/bin/magic-agent run --executor twak" in text
    assert text.count("[Service]") == 1
    assert "live ports must exist" in text
