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
    # Reconciled to the real twak 0.19.1 CLI: no invalid --sell flag, and the
    # swap destination/source token is the GOLD CONTRACT, not the wallet address.
    assert "--sell" not in text
    assert 'swap 1 USDC "$GOLD_CONTRACT" --chain bsc --quote-only --json' in text
    assert 'swap 1 "$GOLD_CONTRACT" USDC --chain bsc --quote-only --json' in text
    assert 'swap 1 USDC "$WALLET_ADDRESS"' not in text


def test_systemd_unit_is_disabled_skeleton_with_env_file():
    text = Path("deploy/midas-agent.service").read_text()

    assert "EnvironmentFile=/etc/midas/agent.env" in text
    assert "ExecStart=/opt/midas/.venv/bin/magic-agent run --executor twak" in text
    assert text.count("[Service]") == 1
    assert "live ports must exist" in text


def test_canonical_live_unit_runs_twak_with_live_data_flags():
    # The canonical LIVE unit MUST scan the real universe, not the offline fixture:
    # --executor twak AND both live-data flags, plus restart-on-failure and the live env.
    text = Path("deploy/midas-agent.service").read_text()

    assert "--executor twak --live-frames --live-cmc" in text
    assert "EnvironmentFile=/etc/midas/agent.env" in text
    assert "Restart=on-failure" in text
    assert "aster" not in text.lower()


def test_paper_unit_is_unambiguously_paper_only_no_aster():
    # The repurposed run unit must be PAPER-ONLY and never confusable with the live unit.
    text = Path("deploy/systemd/magic-agent-run.service").read_text()
    execstart = next(
        line for line in text.splitlines() if line.startswith("ExecStart=")
    )

    assert "--executor paper" in execstart
    assert "twak" not in execstart  # the ExecStart itself is paper, not the live unit
    assert "aster" not in text.lower()
    assert "BNB/USDT" not in text and "BNB-USDT" not in text
    assert "PAPER" in text


def test_serve_unit_points_at_live_per_mode_status_path():
    text = Path("deploy/systemd/magic-agent-serve.service").read_text()

    assert "--status .magic_agent/twak/status.json" in text
    assert "--host 127.0.0.1" in text


def test_env_templates_list_required_live_vars_and_no_dead_names():
    required = (
        "TWAK_ACCESS_ID",
        "TWAK_HMAC_SECRET",
        "TWAK_WALLET_PASSWORD",
        "BSC_RPC_URL",
        "CMC_API_KEY",
        "WALLET_ADDRESS",
    )
    dead = (
        "TWAK_WALLET_PATH",
        "MAGIC_AGENT_BSC_RPC",
        "MAGIC_AGENT_CMC_API_KEY",
        "ASTER_PRIVATE_KEY",
        "ASTER_TESTNET",
    )
    for path in ("deploy/.env.example", ".env.example"):
        text = Path(path).read_text()
        for name in required:
            assert f"{name}=" in text, f"{path} missing required var {name}"
        for name in dead:
            assert name not in text, f"{path} still references dead var {name}"
        assert "aster" not in text.lower(), f"{path} still references aster"


def test_required_live_vars_match_app_require_live_env():
    # The env templates must list EXACTLY the names app.py _require_live_env enforces.
    from magic_agent.app import _LIVE_REQUIRED_ENV

    text = Path("deploy/.env.example").read_text()
    for name in _LIVE_REQUIRED_ENV:
        assert f"{name}=" in text, f"deploy/.env.example missing _require_live_env var {name}"


def test_deploy_readme_uses_canonical_live_unit_and_per_mode_status():
    text = Path("deploy/README.md").read_text()

    assert "midas-agent.service" in text
    assert ".magic_agent/twak/status.json" in text
    # No Aster executor usage or env vars (the prose may note "there is no aster
    # executor", but must not instruct using one).
    assert "--executor aster" not in text
    assert "ASTER_" not in text
