"""The spot runtime cycle — one decision pass through the shared pipeline.

This is the single loop body shared by every runtime mode (paper / live / replay):
runner, live, and cli all route through it. The cycle wires the upstream modules
into one fail-closed pass and **never hand-builds** :class:`DecisionInputs` — every
decision input flows through ``LifecycleEvaluator -> DecisionInputs -> DecisionPipeline``
(the pipeline rejects foreign-source inputs, so this is the only legitimate path).

Load-bearing safety invariants preserved here:

* **Protective exits run first, unconditionally.** ``position_manager.process_exits``
  is called before any entry gating, so a halt (drawdown / consecutive-stop /
  concurrency) can never block a stop / campaign-DOL / risk-reduction exit.
* **An exit cycle blocks same-cycle re-entry (no churn).** ``process_exits`` reports how
  many protective actions it executed this cycle; if any fired, the entry-submission
  phase is skipped and new entries defer to the next cycle. The freed concurrency slot
  can never be re-used same-cycle, so a stop-out that still authorizes on the same bar
  cannot close the old position and open a fresh one in one cycle. The runtime owns this
  guard — it never relies on the scanner going silent on the stop bar.
* **Mandatory RiskPolicy.** Missing policy raises ``RuntimeError`` — but only *after*
  protective exits have been processed, so fail-closed entry behavior cannot strand an
  open position.
* **Scanner is the sole setup authority.** ``scanner_gateway.scan`` owns the setup
  decision; ``setup is None`` means no order (no campaign DOL / no stop -> no setup).
* **Gold identity for execution.** A monitored-but-not-gold candidate
  (``execution_eligible`` False) is journaled and skipped before any sizing.
* **RiskPolicy + a fresh exact-size quote before submit.** ``executability.prepare_order``
  delegates to ``prepare_exact_order``: it sizes via RiskPolicy and returns a fresh,
  unexpired quote bound to ``risk.final_qty``. Only an approved prepared order proceeds.
* **No optimistic booking.** Entries are submitted via ``execution_coordinator.submit``;
  positions are booked only on the coordinator's reconcile path, never optimistically.
"""
from __future__ import annotations

from magic_agent.cmc_selector import select_candidates
from magic_agent.risk_policy import MarketRiskContext
from magic_agent.spot_models import ActionPurpose


def run_cycle(app, now) -> None:
    app.reconcile_unfinished()
    # Protective exits run FIRST, unconditionally. ``process_exits`` returns how many
    # protective actions (full closes / partial risk reductions) it executed this cycle.
    exited = app.position_manager.process_exits(now)
    if app.state.blocks_new_exposure:
        return
    if app.risk_policy is None:
        raise RuntimeError("mandatory RiskPolicy is missing")
    # No-churn invariant: an exit cycle is an EXIT-ONLY cycle. When a protective exit
    # closed (or reduced) a position THIS cycle, the freed concurrency slot must NOT be
    # re-used by a same-cycle entry — otherwise a stop-out that still authorizes on the
    # same bar would close the old position AND open a fresh one in one cycle (instant
    # re-entry / churn). The runtime owns this guard; it never relies on the scanner
    # going silent on the stop bar. New entries defer to the NEXT cycle. The existing
    # position's exit side-effects already ran above, and the end-of-cycle persistence
    # (``state_journal.save`` + ``position_store.save``) still runs below so the close
    # is durable and the loop stays consistent.
    if exited:
        if app.watchlist.state.discovery_due(now):
            app.watchlist.mark_discovery(now)
        app.compliance.observe(app.execution_journal.confirmed_records(), now)
        app.state_journal.save(app.state.as_dict())
        app.position_store.save(app.position_manager.book)
        # Publish the live status snapshot alongside the persisted state so the
        # dashboard reflects the post-exit book this cycle (consistent with the save).
        app.publish_status()
        return
    # Operator kill-switch: halt NEW ENTRIES while preserving protective exits (which
    # already ran above via process_exits). Persist + publish the post-exit state, then
    # return WITHOUT entering the entry phase. Never placed before process_exits.
    if getattr(app, "kill_switch_path", None) is not None and app.kill_switch_path.exists():
        app.exclusion_journal.append_code("TRACK1", "kill_switch_halt_new_entries", now)
        app.compliance.observe(app.execution_journal.confirmed_records(), now)
        app.state_journal.save(app.state.as_dict())
        app.position_store.save(app.position_manager.book)
        app.publish_status()
        return
    cmc_batch = app.cmc_source.snapshot(now)
    snapshots = cmc_batch.snapshots
    app.exclusion_journal.append_many(cmc_batch.exclusions, now)
    batch = app.candidate_source.enumerate(
        now=now, watchlist=app.watchlist.state, snapshots=snapshots,
    )
    app.exclusion_journal.append_many(batch.exclusions, now)
    fresh = {row.identity_key: row for row in select_candidates(list(snapshots.values()), now=now)}
    for candidate in (*batch.monitoring, *batch.discovery):
        setup = app.scanner_gateway.scan(candidate)
        if setup is None:
            continue
        if candidate in batch.discovery:
            app.watchlist.promote(candidate.symbol)
        snapshot = candidate.snapshot
        if not candidate.execution_eligible:
            app.exclusion_journal.append_code(candidate.symbol, "identity_not_gold", now)
            continue
        if snapshot is None or fresh.get(snapshot.identity_key) is None:
            app.exclusion_journal.append_code(candidate.symbol, "cmc_stale_or_vetoed", now)
            continue
        market = MarketRiskContext(
            snapshot.momentum_7d, snapshot.momentum_7d_rank_pct,
            snapshot.macro_clamp, app.state.canary_mode,
        )
        prepared = app.executability.prepare_order(
            setup=setup, market=market,
            risk_state=app.state.risk_state(), risk_policy=app.risk_policy,
        )
        if not prepared.approved:
            continue
        inputs = app.lifecycle_evaluator.evaluate(
            app.observe_entry(setup, prepared.risk, now),
        )
        decision = app.pipeline.decide(inputs)
        app.decision_journal.append(decision, now)
        if decision.intent is not None:
            result = app.execution_coordinator.submit(
                decision.intent, quote=prepared.quote, policy=prepared.risk,
            )
            hook = getattr(app, "after_entry_submission", None)
            if hook is not None:
                hook(
                    intent=decision.intent,
                    result=result,
                    quote=prepared.quote,
                    risk=prepared.risk,
                    now=now,
                )
            break
    if app.watchlist.state.discovery_due(now):
        app.watchlist.mark_discovery(now)
    app.compliance.observe(app.execution_journal.confirmed_records(), now)
    app.state_journal.save(app.state.as_dict())
    # Persist the open position book alongside the runtime state so a booking
    # survives a restart (the next cycle's concurrency cap blocks re-entry) and an
    # exit that dropped a position from the book decrements the durable count too.
    # Paper-mode mechanism only — in live the open book is the chain's truth.
    app.position_store.save(app.position_manager.book)
    # Publish the live status snapshot once per cycle, consistent with the persisted
    # state, so the dashboard's /api/status shows real paper data (not the demo
    # fallback). An additional write — the ordering/invariants above are untouched.
    app.publish_status()
