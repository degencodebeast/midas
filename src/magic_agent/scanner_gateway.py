# src/magic_agent/scanner_gateway.py
"""The ONLY module that imports scanner internals (`magic_scanner.*`).

Downstream agent code depends solely on the agent ``Setup``. If scanner types change,
this is the one file to fix. ``scan_fn`` defaults to the live scanner but is injected
in tests so no scanner/network is needed.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from magic_agent.models import Setup
from magic_agent.setup_view import from_scan_result


class ScannerGateway:
    def __init__(
        self,
        *,
        scan_fn: Callable[..., list[Any]] | None = None,
        stop_buffer_pct: float = 0.005,
        min_rr: float = 3.0,
    ) -> None:
        if scan_fn is None:
            from magic_scanner.scan import scan_symbols  # the sole scanner import

            scan_fn = scan_symbols
        self._scan_fn = scan_fn
        self._stop_buffer_pct = stop_buffer_pct
        self._min_rr = min_rr

    def scan(self, symbol: str) -> Setup | None:
        results = self._scan_fn([symbol]) or []
        for result in results:
            setup = from_scan_result(
                result, stop_buffer_pct=self._stop_buffer_pct, min_rr=self._min_rr
            )
            if setup is not None:
                return setup
        return None
