"""Tests for ExclusionJournal and DecisionJournal (runtime append-only JSONL writers).

TDD RED phase: all tests written before implementation exists.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from magic_agent.candidate_source import CandidateExclusion
from magic_agent.cmc_source import CmcExclusion
from magic_agent.decision_pipeline import PipelineDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
_TS2 = datetime(2026, 6, 21, 12, 5, 0, tzinfo=timezone.utc)


def _load_lines(path) -> list[dict]:
    """Read all JSONL records from *path*."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# ExclusionJournal – append_many (CmcExclusion rows)
# ---------------------------------------------------------------------------


class TestExclusionJournalAppendManyCmc:
    def test_writes_one_line_per_cmc_exclusion(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        rows = (
            CmcExclusion("elig-1", "BTC", "cmc_missing"),
            CmcExclusion("elig-2", "ETH", "non_directional_asset"),
        )
        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many(rows, _TS)

        lines = _load_lines(tmp_path / "excl.jsonl")
        assert len(lines) == 2

    def test_cmc_exclusion_record_has_symbol(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many((CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS)

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert rec["symbol"] == "BTC"

    def test_cmc_exclusion_record_has_reason_code(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many((CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS)

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert rec["reason_code"] == "cmc_missing"

    def test_cmc_exclusion_record_has_eligibility_id(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many((CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS)

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert rec["eligibility_id"] == "elig-1"

    def test_cmc_exclusion_record_has_observed_at_timestamp(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many((CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS)

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert rec["observed_at"] == _TS.isoformat()

    def test_cmc_exclusion_record_has_source_type(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many((CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS)

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert rec["source"] == "cmc"

    def test_empty_tuple_writes_nothing(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        path = tmp_path / "excl.jsonl"
        journal = ExclusionJournal(path)
        journal.append_many((), _TS)

        assert not path.exists()


# ---------------------------------------------------------------------------
# ExclusionJournal – append_many (CandidateExclusion rows)
# ---------------------------------------------------------------------------


class TestExclusionJournalAppendManyCandidate:
    def test_writes_one_line_per_candidate_exclusion(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        rows = (
            CandidateExclusion("elig-1", "BTC", "identity_unresolved"),
            CandidateExclusion("elig-2", "ETH", "discovery_not_due"),
        )
        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many(rows, _TS)

        lines = _load_lines(tmp_path / "excl.jsonl")
        assert len(lines) == 2

    def test_candidate_exclusion_source_tag(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many(
            (CandidateExclusion("elig-1", "BTC", "identity_unresolved"),), _TS
        )

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert rec["source"] == "candidate"

    def test_candidate_exclusion_fields(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many(
            (CandidateExclusion("elig-3", "SOL", "market_data_unscannable"),), _TS
        )

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert rec["eligibility_id"] == "elig-3"
        assert rec["symbol"] == "SOL"
        assert rec["reason_code"] == "market_data_unscannable"
        assert rec["observed_at"] == _TS.isoformat()


# ---------------------------------------------------------------------------
# ExclusionJournal – append_many with mixed row types
# ---------------------------------------------------------------------------


class TestExclusionJournalMixedRows:
    def test_mixed_cmc_and_candidate_rows_each_get_correct_source(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        rows = (
            CmcExclusion("elig-1", "BTC", "cmc_missing"),
            CandidateExclusion("elig-2", "ETH", "identity_unresolved"),
        )
        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many(rows, _TS)

        lines = _load_lines(tmp_path / "excl.jsonl")
        assert len(lines) == 2
        sources = {rec["source"] for rec in lines}
        assert sources == {"cmc", "candidate"}


# ---------------------------------------------------------------------------
# ExclusionJournal – append_code
# ---------------------------------------------------------------------------


class TestExclusionJournalAppendCode:
    def test_append_code_writes_single_line(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_code("BTC", "identity_not_gold", _TS)

        lines = _load_lines(tmp_path / "excl.jsonl")
        assert len(lines) == 1

    def test_append_code_record_fields(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_code("ETH", "cmc_stale_or_vetoed", _TS)

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert rec["symbol"] == "ETH"
        assert rec["reason_code"] == "cmc_stale_or_vetoed"
        assert rec["observed_at"] == _TS.isoformat()

    def test_append_code_source_tag_is_runtime(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_code("BTC", "identity_not_gold", _TS)

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert rec["source"] == "runtime"

    def test_append_code_has_no_eligibility_id(self, tmp_path):
        """Ad-hoc runtime exclusions have no eligibility_id field."""
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_code("BTC", "identity_not_gold", _TS)

        rec = _load_lines(tmp_path / "excl.jsonl")[0]
        assert "eligibility_id" not in rec


# ---------------------------------------------------------------------------
# ExclusionJournal – accumulation (no truncation)
# ---------------------------------------------------------------------------


class TestExclusionJournalAccumulation:
    def test_multiple_appends_accumulate(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many((CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS)
        journal.append_code("ETH", "identity_not_gold", _TS)
        journal.append_many((CandidateExclusion("elig-3", "SOL", "identity_unresolved"),), _TS)

        lines = _load_lines(tmp_path / "excl.jsonl")
        assert len(lines) == 3

    def test_second_instance_sees_prior_records(self, tmp_path):
        """A new ExclusionJournal instance on the same path reads existing lines."""
        from magic_agent.runtime_journals import ExclusionJournal

        path = tmp_path / "excl.jsonl"
        ExclusionJournal(path).append_many(
            (CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS
        )
        ExclusionJournal(path).append_code("ETH", "identity_not_gold", _TS2)

        lines = _load_lines(path)
        assert len(lines) == 2

    def test_append_does_not_truncate_file(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        path = tmp_path / "excl.jsonl"
        journal = ExclusionJournal(path)
        journal.append_many((CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS)
        first_size = path.stat().st_size
        journal.append_code("ETH", "identity_not_gold", _TS2)

        assert path.stat().st_size > first_size


# ---------------------------------------------------------------------------
# ExclusionJournal – records() read-back accessor
# ---------------------------------------------------------------------------


class TestExclusionJournalRecords:
    def test_records_returns_what_was_written(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many((CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS)
        journal.append_code("ETH", "identity_not_gold", _TS2)

        recs = journal.records()
        assert len(recs) == 2
        assert recs[0]["symbol"] == "BTC"
        assert recs[1]["symbol"] == "ETH"

    def test_records_empty_when_nothing_written(self, tmp_path):
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        assert journal.records() == []


# ---------------------------------------------------------------------------
# ExclusionJournal – deterministic field order
# ---------------------------------------------------------------------------


class TestExclusionJournalFieldOrder:
    def test_keys_are_sorted(self, tmp_path):
        """Each JSONL line must have its JSON keys in sorted order (deterministic)."""
        from magic_agent.runtime_journals import ExclusionJournal

        journal = ExclusionJournal(tmp_path / "excl.jsonl")
        journal.append_many((CmcExclusion("elig-1", "BTC", "cmc_missing"),), _TS)

        raw_line = (tmp_path / "excl.jsonl").read_text(encoding="utf-8").strip()
        keys = list(json.loads(raw_line).keys())
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# DecisionJournal – append
# ---------------------------------------------------------------------------


class TestDecisionJournalAppend:
    def _hold_decision(self) -> PipelineDecision:
        return PipelineDecision(
            action="hold",
            intent=None,
            reason="entries_halted",
            reason_codes=("entries_halted",),
        )

    def test_append_writes_one_line(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._hold_decision(), _TS)

        lines = _load_lines(tmp_path / "decisions.jsonl")
        assert len(lines) == 1

    def test_append_records_action(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._hold_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["action"] == "hold"

    def test_append_records_reason(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._hold_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["reason"] == "entries_halted"

    def test_append_records_reason_codes(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._hold_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["reason_codes"] == ["entries_halted"]

    def test_append_records_timestamp(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._hold_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["observed_at"] == _TS.isoformat()

    def test_append_null_intent_is_absent_or_null(self, tmp_path):
        """When decision.intent is None, the record captures intent absence."""
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._hold_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        # Either "intent_id" is absent or explicitly null — either is valid
        assert rec.get("intent_id") is None

    def test_append_with_intent_records_intent_id(self, tmp_path):
        """When decision.intent is set, its intent_id is serialized."""
        from magic_agent.runtime_journals import DecisionJournal
        from magic_agent.spot_models import ActionPurpose, AuthorizedSetup, SpotIntent

        setup = AuthorizedSetup.example()
        intent = SpotIntent(
            "intent:setup-1", setup, Decimal("10"), "buy", ActionPurpose.STRATEGY
        )
        decision = PipelineDecision(
            action="enter",
            intent=intent,
            reason="authorized",
            reason_codes=("authorized",),
        )
        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(decision, _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["intent_id"] == "intent:setup-1"

    def test_append_with_intent_records_symbol(self, tmp_path):
        """When decision.intent is set, its symbol (from setup) is serialized."""
        from magic_agent.runtime_journals import DecisionJournal
        from magic_agent.spot_models import ActionPurpose, AuthorizedSetup, SpotIntent

        setup = AuthorizedSetup.example(symbol="ZEC/USDT")
        intent = SpotIntent(
            "intent:setup-1", setup, Decimal("10"), "buy", ActionPurpose.STRATEGY
        )
        decision = PipelineDecision(
            action="enter",
            intent=intent,
            reason="authorized",
            reason_codes=("authorized",),
        )
        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(decision, _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["symbol"] == "ZEC/USDT"

    def test_append_records_exit_quantity_as_string(self, tmp_path):
        """exit_quantity (Decimal) is serialized as a string for exactness."""
        from magic_agent.runtime_journals import DecisionJournal

        decision = PipelineDecision(
            action="risk_exit",
            intent=None,
            reason="stop",
            reason_codes=("stop",),
            exit_quantity=Decimal("5.5"),
        )
        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(decision, _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["exit_quantity"] == "5.5"


# ---------------------------------------------------------------------------
# DecisionJournal – dashboard-renderable fields (frontend field names)
# ---------------------------------------------------------------------------


class TestDecisionJournalDashboardFields:
    """The dashboard ``Decision`` interface reads ``ts``/``setup_ref``/etc.

    These tests assert the enriched record builder emits the field NAMES the
    frontend (web/app/page.tsx) already reads, populated from the real
    decision/intent/setup attributes.
    """

    def _entry_decision(self):
        from magic_agent.spot_models import ActionPurpose, AuthorizedSetup, SpotIntent

        setup = AuthorizedSetup.example(
            symbol="ZEC/USDT",
            entry=Decimal("100"),
            structural_stop=Decimal("90"),
            campaign_dol=Decimal("120"),
            bias_alignment="aligned",
            raw_grade="B",
            grade="A",
            grade_promotion_reason="dol_confirmed",
        )
        intent = SpotIntent(
            "intent:setup-1", setup, Decimal("10"), "buy", ActionPurpose.STRATEGY
        )
        return PipelineDecision(
            action="enter",
            intent=intent,
            reason="authorized",
            reason_codes=("authorized",),
        )

    def _hold_decision(self) -> PipelineDecision:
        return PipelineDecision(
            action="hold",
            intent=None,
            reason="no_scanner_authorization",
            reason_codes=("no_scanner_authorization",),
        )

    def test_entry_row_has_ts_from_observed_at(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._entry_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["ts"] == _TS.isoformat()

    def test_entry_row_has_action(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._entry_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["action"] == "enter"

    def test_entry_row_allow_is_true(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._entry_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["allow"] is True

    def test_entry_row_has_setup_ref(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._entry_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["setup_ref"] == "setup-1"

    def test_entry_row_has_qty_as_number(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._entry_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["qty"] == 10.0
        assert isinstance(rec["qty"], float)

    def test_entry_row_has_entry_stop_target_as_numbers(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._entry_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["entry"] == 100.0
        assert rec["stop_loss"] == 90.0
        assert rec["take_profit"] == 120.0
        assert isinstance(rec["entry"], float)

    def test_entry_row_has_regime(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._entry_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["regime"] == "aligned"

    def test_entry_row_has_gate_reason_and_reasoning(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._entry_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["gate_reason"] == "authorized"
        assert "authorized" in rec["reasoning"]

    def test_entry_row_has_scanner_provenance(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._entry_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["raw_grade"] == "B"
        assert rec["effective_grade"] == "A"
        assert rec["grade_promotion_reason"] == "dol_confirmed"

    def test_hold_row_has_ts_action_gate_reason(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._hold_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["ts"] == _TS.isoformat()
        assert rec["action"] == "hold"
        assert rec["gate_reason"] == "no_scanner_authorization"

    def test_hold_row_allow_is_false(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._hold_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["allow"] is False

    def test_hold_row_setup_fields_are_null(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(self._hold_decision(), _TS)

        rec = _load_lines(tmp_path / "decisions.jsonl")[0]
        assert rec["setup_ref"] is None
        assert rec["qty"] is None
        assert rec["entry"] is None
        assert rec["stop_loss"] is None
        assert rec["take_profit"] is None
        assert rec["regime"] is None
        assert rec["raw_grade"] is None
        assert rec["effective_grade"] is None
        assert rec["grade_promotion_reason"] is None


# ---------------------------------------------------------------------------
# DecisionJournal – accumulation (no truncation)
# ---------------------------------------------------------------------------


class TestDecisionJournalAccumulation:
    def test_multiple_appends_accumulate(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        for i in range(3):
            journal.append(
                PipelineDecision("hold", None, "no_scanner_authorization", ("no_scanner_authorization",)),
                _TS,
            )

        lines = _load_lines(tmp_path / "decisions.jsonl")
        assert len(lines) == 3

    def test_second_instance_appends_to_existing(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        path = tmp_path / "decisions.jsonl"
        DecisionJournal(path).append(
            PipelineDecision("hold", None, "entries_halted", ("entries_halted",)), _TS
        )
        DecisionJournal(path).append(
            PipelineDecision("hold", None, "no_scanner_authorization", ("no_scanner_authorization",)),
            _TS2,
        )

        lines = _load_lines(path)
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# DecisionJournal – records() read-back accessor
# ---------------------------------------------------------------------------


class TestDecisionJournalRecords:
    def test_records_returns_what_was_written(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(
            PipelineDecision("hold", None, "entries_halted", ("entries_halted",)), _TS
        )
        journal.append(
            PipelineDecision("hold", None, "no_scanner_authorization", ("no_scanner_authorization",)),
            _TS2,
        )

        recs = journal.records()
        assert len(recs) == 2
        assert recs[0]["reason"] == "entries_halted"
        assert recs[1]["reason"] == "no_scanner_authorization"

    def test_records_empty_when_nothing_written(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        assert journal.records() == []


# ---------------------------------------------------------------------------
# DecisionJournal – deterministic field order
# ---------------------------------------------------------------------------


class TestDecisionJournalFieldOrder:
    def test_keys_are_sorted(self, tmp_path):
        from magic_agent.runtime_journals import DecisionJournal

        journal = DecisionJournal(tmp_path / "decisions.jsonl")
        journal.append(
            PipelineDecision("hold", None, "entries_halted", ("entries_halted",)), _TS
        )

        raw_line = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").strip()
        keys = list(json.loads(raw_line).keys())
        assert keys == sorted(keys)
