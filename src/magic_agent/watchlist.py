from dataclasses import dataclass, replace
from datetime import datetime, timedelta


PINNED_SYMBOLS = ("ZEC", "DEXE", "TRX", "APE", "LINK", "XRP")


@dataclass(frozen=True)
class WatchlistState:
    active_symbols: tuple[str, ...]
    last_discovery_at: datetime | None

    @classmethod
    def initial(cls) -> "WatchlistState":
        return cls(PINNED_SYMBOLS, None)

    def monitoring_due(self, now: datetime) -> bool:
        return True  # caller invokes only after a closed H1 bar

    def discovery_due(self, now: datetime) -> bool:
        return self.last_discovery_at is None or now - self.last_discovery_at >= timedelta(hours=4)

    def mark_discovery(self, now: datetime) -> "WatchlistState":
        return replace(self, last_discovery_at=now)

    def promote(self, symbol: str) -> "WatchlistState":
        return replace(self, active_symbols=tuple(dict.fromkeys((*self.active_symbols, symbol))))


class WatchlistManager:
    def __init__(self, state: WatchlistState) -> None:
        self.state = state

    def promote(self, symbol: str) -> None:
        self.state = self.state.promote(symbol)

    def mark_discovery(self, now: datetime) -> None:
        self.state = self.state.mark_discovery(now)
