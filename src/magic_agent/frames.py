# src/magic_agent/frames.py
"""Market-data frame sources for the scanner gateway.

The :class:`~magic_agent.scanner_gateway.ScannerGateway` consumes *closed-bar*
multi-timeframe OHLC frames in the ``magic_scanner.feed`` schema. This module
provides the two concrete sources the runtime uses:

* :class:`FixtureFrameSource` — deterministic, NO NETWORK. Builds the 1w/12h/1h
  frames from a committed JSON fixture so ``magic-agent run --executor paper`` is
  runnable fully offline.
* :class:`GateioFrameSource` — the real read-only live path. Fetches closed
  OHLC via ``ccxt.gateio`` (mirroring ``scan_universe_bias.py``), dropping the
  still-forming bar. ``ccxt`` is imported LAZILY inside the fetch so this module
  imports — and the fixture path runs — without ccxt installed.

Both resolve a candidate to its market-data symbol exactly as the gateway does:
``registry.by_contract_key(candidate.identity_key).market_data_symbol``. Neither
source handles keys, funds, or orders — read-only market data only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from magic_scanner.feed import ohlcv_to_dataframe

# The closed-bar timeframes the gateway runs the scanner on (weekly bias ladder
# down to the H1 orderflow frame). scan_pair requires the weekly + H12 keys; the
# 1h frame is optional but expected on a real scan.
FRAME_TIMEFRAMES: tuple[str, ...] = ("1w", "12h", "1h")


@runtime_checkable
class FrameSource(Protocol):
    """Port: yields a candidate's closed-bar OHLC frames keyed by timeframe.

    Implementations return a ``dict[str, pandas.DataFrame]`` keyed exactly as
    ``scan_pair`` expects (``"1w"``, ``"12h"``, ``"1h"``), each frame in the
    ``magic_scanner.feed`` schema (named UTC ``open_time`` index; ``open``,
    ``high``, ``low``, ``close``, ``volume`` float columns; a ``close_time``
    column) and CLOSED-only (the still-forming bar dropped).
    """

    def closed_frames(self, candidate: Any) -> dict[str, pd.DataFrame]:
        """Return the candidate's closed 1w/12h/1h frames."""
        ...


def _market_data_symbol(registry: Any, candidate: Any) -> str:
    """Resolve a candidate to its market-data symbol via the identity registry.

    Mirrors :meth:`ScannerGateway.scan`: the candidate's ``identity_key`` is
    looked up through ``registry.by_contract_key`` and its ``market_data_symbol``
    returned. Resolution failures propagate (e.g. ``KeyError``) — failing closed
    is correct, never guessing a symbol.
    """
    return registry.by_contract_key(candidate.identity_key).market_data_symbol


class FixtureFrameSource:
    """Deterministic, offline :class:`FrameSource` backed by committed fixtures.

    Reads a per-symbol JSON fixture of raw ccxt-style OHLCV rows and converts
    them to the feed-schema DataFrame with :func:`magic_scanner.feed.ohlcv_to_dataframe`,
    so the resulting frames are byte-compatible with what ``scan_pair`` consumes.
    No network, no randomness — the same candidate always yields the same frames.
    """

    def __init__(self, *, registry: Any, fixture_dir: str | Path) -> None:
        """Initialize the fixture source.

        Args:
            registry: Identity registry resolving ``candidate.identity_key`` to a
                record carrying ``market_data_symbol``.
            fixture_dir: Directory of ``<symbol>.json`` OHLCV fixtures.
        """
        self.registry = registry
        self.fixture_dir = Path(fixture_dir)

    def closed_frames(self, candidate: Any) -> dict[str, pd.DataFrame]:
        """Return the candidate's closed 1w/12h/1h frames from its fixture."""
        symbol = _market_data_symbol(self.registry, candidate)
        payload = self._load_fixture(symbol)
        # `now` past every fixture bar's close_time so no bar is dropped as
        # still-forming — the fixture rows are authored as already-closed bars.
        now = pd.Timestamp.max.tz_localize("UTC")
        frames: dict[str, pd.DataFrame] = {}
        for tf in FRAME_TIMEFRAMES:
            spec = payload["frames"].get(tf)
            if spec is None:
                raise KeyError(f"fixture for {symbol!r} is missing timeframe {tf!r}")
            frames[tf] = ohlcv_to_dataframe(spec["rows"], tf, now=now)
        return frames

    def _load_fixture(self, symbol: str) -> dict[str, Any]:
        """Load and validate the JSON fixture for ``symbol``."""
        # "ZEC/USDT" -> "zec_usdt.json"; deterministic filename mapping.
        name = symbol.lower().replace("/", "_") + ".json"
        path = self.fixture_dir / name
        if not path.exists():
            raise FileNotFoundError(
                f"no frame fixture for {symbol!r} at {path} "
                f"(expected file name {name!r})"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "frames" not in payload:
            raise ValueError(f"frame fixture {path} is missing a 'frames' object")
        return payload


class GateioFrameSource:
    """Live, read-only :class:`FrameSource` backed by ``ccxt.gateio``.

    Fetches closed 1w/12h/1h OHLC bars via gate.io (Binance/Bybit are geo-blocked
    in the build environment), dropping the still-forming bar exactly as
    ``magic_scanner.feed.fetch_multi_tf`` does. Read-only public market data only:
    no keys, no funds, no orders.

    ``ccxt`` is imported lazily inside :meth:`closed_frames` so importing this
    module — and the offline fixture path — needs no ccxt dependency. ``ccxt`` is
    an OPTIONAL extra (see ``pyproject.toml``); if it is absent, a clear
    :class:`RuntimeError` naming the missing dependency is raised.
    """

    def __init__(self, *, registry: Any, limit: int = 900) -> None:
        """Initialize the gate.io source.

        Args:
            registry: Identity registry resolving ``candidate.identity_key`` to a
                record carrying ``market_data_symbol``.
            limit: Bars to request per timeframe (mirrors the scan-universe
                reference; the forming bar is dropped after fetch).
        """
        self.registry = registry
        self.limit = limit
        self._exchange: Any = None

    def _make_exchange(self) -> Any:
        """Build (once) a read-only gate.io ccxt client, importing ccxt lazily.

        Raises:
            RuntimeError: If ``ccxt`` is not installed, naming the optional dep.
        """
        if self._exchange is not None:
            return self._exchange
        try:
            import ccxt  # lazy: optional dependency, only needed on the live path
        except ImportError as exc:
            raise RuntimeError(
                "GateioFrameSource requires the optional 'ccxt' dependency for "
                "live market data. Install it with `uv add ccxt` (or the project's "
                "'live' extra: `uv sync --extra live`)."
            ) from exc
        self._exchange = ccxt.gateio({"enableRateLimit": True})
        return self._exchange

    def closed_frames(self, candidate: Any) -> dict[str, pd.DataFrame]:
        """Return the candidate's closed 1w/12h/1h frames fetched from gate.io."""
        symbol = _market_data_symbol(self.registry, candidate)
        exchange = self._make_exchange()
        now = pd.Timestamp.now(tz="UTC")
        frames: dict[str, pd.DataFrame] = {}
        for tf in FRAME_TIMEFRAMES:
            # Fetch one extra bar so we keep `limit` closed bars after dropping
            # the still-forming last bar (mirrors fetch_closed_ohlc).
            raw = exchange.fetch_ohlcv(symbol, tf, limit=self.limit + 1)
            frames[tf] = ohlcv_to_dataframe(raw, tf, now=now)
        return frames
