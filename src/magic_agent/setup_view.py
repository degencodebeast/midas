# src/magic_agent/setup_view.py
"""Setup-view adapter — intentionally level-free.

Historically this module fabricated a stop (a fixed ``stop_buffer_pct`` beyond
the QML key level) and a target (a fixed ``min_rr`` multiple of risk). That
violated the load-bearing invariant that the **scanner is the sole authority**
for setups and levels: MIDAS consumes the scanner's structural stop and campaign
DOL, it never invents fixed-percentage levels.

All fixed-percentage stop/target paths have been removed. Setup construction now
lives in :mod:`magic_agent.scanner_gateway`
(``authorized_setup_from_scan``), which maps scanner-owned authorization and
levels onto :class:`magic_agent.spot_models.AuthorizedSetup`.

This module is retained only so existing imports stay valid; it deliberately
exposes no level-fabricating API.
"""
from __future__ import annotations

__all__: list[str] = []
