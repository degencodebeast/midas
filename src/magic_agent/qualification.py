from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil


@dataclass(frozen=True)
class QualificationConfig:
    minimum_trade_count: int
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class QualificationPace:
    minimum_trade_count: int
    completed_trade_count: int
    required_by_now: int
    behind_pace: bool
    warning: str | None
    can_force_trade: bool = False

    def as_dict(self) -> dict:
        return {
            "minimum_trade_count": self.minimum_trade_count,
            "completed_trade_count": self.completed_trade_count,
            "required_by_now": self.required_by_now,
            "behind_pace": self.behind_pace,
            "warning": self.warning,
            "can_force_trade": self.can_force_trade,
        }


def evaluate_qualification_pace(
    *,
    completed_trade_count: int,
    now: datetime,
    config: QualificationConfig,
) -> QualificationPace:
    if config.minimum_trade_count < 0:
        raise ValueError("minimum_trade_count must not be negative")
    total_seconds = max(1.0, (config.window_end - config.window_start).total_seconds())
    elapsed_seconds = min(max(0.0, (now - config.window_start).total_seconds()), total_seconds)
    required = min(
        config.minimum_trade_count,
        ceil(config.minimum_trade_count * elapsed_seconds / total_seconds),
    )
    behind = completed_trade_count < required
    return QualificationPace(
        minimum_trade_count=config.minimum_trade_count,
        completed_trade_count=completed_trade_count,
        required_by_now=required,
        behind_pace=behind,
        warning="minimum_trade_count_behind_pace" if behind else None,
    )
