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


# ---------------------------------------------------------------------------
# Operator-doc drift guards.
#
# These read the committed operator docs (no network) and FAIL if a stale
# claim reappears, or if a load-bearing correct fact disappears. They exist
# because reviewers kept finding stale doc spots after targeted fixes:
#   - cadence is paced to CLOSED H1 BARS (live.py bar-close-aligned clock),
#     NOT a 5-minute candle / ~15s tick;
#   - the paper runtime is fixed-margin off a 10000 default session (live
#     sizes off the real wallet), NOT PaperExecutor(starting_equity=1000.0);
#   - the canonical live unit is `--executor twak --live-frames --live-cmc`,
#     there is no operational `--executor aster`;
#   - autonomy items #98 are DONE: live sell idempotency (submit_sell) and
#     chain-truth restart rebuild (rebuild_positions_from_chain) — so the
#     "pending" phrasings must not reappear.
#
# Scoping note: a legitimate "there is no aster executor" / "x402 deferred"
# mention must NOT trip these — only OPERATIONAL stale instructions are banned.
# ---------------------------------------------------------------------------

_OPERATOR_DOCS = (
    "README.md",
    "docs/track1-spot-runbook.md",
    "deploy/ROLLOUT.md",
    "deploy/README.md",
)


def _doc_text(name: str) -> str:
    return Path(name).read_text()


def test_operator_docs_have_no_stale_cadence_claim():
    # Real cadence = one cycle per CLOSED H1 BAR (live.py bar-close-aligned
    # clock). No 5-minute candle / ~15s tick wording may survive anywhere.
    banned = ("5-minute", "5 min", "5-min", "every ~15 s", "every ~15s", "~15 s")
    for name in _OPERATOR_DOCS:
        text = _doc_text(name)
        for token in banned:
            assert token not in text, f"{name} still has stale cadence claim {token!r}"


def test_rollout_states_closed_h1_cadence():
    text = _doc_text("deploy/ROLLOUT.md")
    assert "closed H1" in text, "ROLLOUT must state the closed-H1-bar cadence"


def test_operator_docs_have_no_obsolete_paper_executor_shape():
    # The real paper runtime is fixed-margin off a 10000 default session; live
    # sizes off the wallet. The old PaperExecutor(starting_equity=1000.0) shape
    # never existed in code and must not reappear in any doc.
    banned = ("starting_equity=1000", "PaperExecutor(")
    for name in _OPERATOR_DOCS:
        text = _doc_text(name)
        for token in banned:
            assert token not in text, f"{name} still has obsolete paper shape {token!r}"


def test_operator_docs_have_no_operational_aster_executor():
    # A prose "there is no aster executor" note is fine; an operational
    # `--executor aster` instruction is not. Also forbid the dead
    # AsterRestExecutor as an operational reference — but the runbook keeps a
    # grep-example asserting AsterRestExecutor is ABSENT, which is legitimate.
    for name in _OPERATOR_DOCS:
        text = _doc_text(name)
        assert "--executor aster" not in text, f"{name} instructs --executor aster"


def test_operator_docs_have_no_pending_autonomy_phrasings():
    # Autonomy items #98 are DONE in code (submit_sell + rebuild_positions_from_chain).
    # The exact stale "pending" phrasings must not reappear.
    banned = (
        "sell path has no execution-journal idempotency",
        "still loads the paper position store",
        "is not yet wired",
        "chain-position-rebuild on restart is not yet",
    )
    for name in _OPERATOR_DOCS:
        text = _doc_text(name)
        for token in banned:
            assert token not in text, f"{name} still has pending-autonomy phrasing {token!r}"


def test_runbook_and_rollout_mark_autonomy_items_closed():
    # The two #98 items must be described as DONE/CLOSED with the real mechanism.
    runbook = _doc_text("docs/track1-spot-runbook.md")
    rollout = _doc_text("deploy/ROLLOUT.md")
    for text in (runbook, rollout):
        assert "submit_sell" in text, "doc must cite submit_sell (sell idempotency DONE)"
        assert "rebuild_positions_from_chain" in text
    # The gate must no longer claim the code cannot run unattended.
    assert "do not\nrun autonomously" not in runbook
    assert "do not run autonomously" not in runbook.replace("\n", " ")
    assert "keep the unit disabled" not in runbook


def test_canonical_live_unit_phrasing_present_in_docs():
    # The canonical live unit string must survive in every operator doc.
    unit = "--executor twak --live-frames --live-cmc"
    for name in _OPERATOR_DOCS:
        assert unit in _doc_text(name), f"{name} missing canonical live unit {unit!r}"


def test_operator_docs_keep_fixed_margin_markers():
    # The fixed-margin risk model markers must remain (no stop-derived-sizing drift).
    for name in ("README.md", "docs/track1-spot-runbook.md", "deploy/ROLLOUT.md"):
        text = _doc_text(name)
        assert "a_grade_margin_fraction = 0.05" in text or "A-grade margin | 5 %" in text, (
            f"{name} missing fixed-margin A-grade marker"
        )
    # ROLLOUT and runbook must name the model explicitly.
    assert "fixed-MARGIN" in _doc_text("deploy/ROLLOUT.md")
    assert "FIXED-MARGIN" in _doc_text("docs/track1-spot-runbook.md")


def test_deferred_items_stay_honestly_deferred_not_overclaimed():
    # x402-as-central, the LLM advisor, and held_tokens() enumeration remain
    # future work. Guard that the docs do not silently claim them DONE.
    runbook = _doc_text("docs/track1-spot-runbook.md")
    assert "x402 budget hardening is pending" in runbook
    # The advisor stays OFF-by-default / deferred wording somewhere.
    assert "deferred" in runbook
