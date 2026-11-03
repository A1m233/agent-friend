"""Wake intervals for autonomous companionship."""

import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CompanionshipConfig:
    interval_minutes: int = 60
    minimum_interval_minutes: int = 10

    def random_delay_seconds(self) -> int:
        # interval_minutes is the upper bound stored by existing desktop installations.
        return random.randint(self.minimum_interval_minutes * 60, self.interval_minutes * 60)

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "CompanionshipConfig":
        values = settings.get("companionshipLimits", {})
        if not isinstance(values, dict):
            values = {}
        limits = {
            "interval_minutes": (10, 10080),
            "minimum_interval_minutes": (1, 1440),
        }
        defaults = cls()
        normalized = {}
        for name, (low, high) in limits.items():
            value = values.get(name, getattr(defaults, name))
            normalized[name] = (
                value if type(value) is int and low <= value <= high else getattr(defaults, name)
            )
        normalized["interval_minutes"] = max(
            normalized["interval_minutes"], normalized["minimum_interval_minutes"]
        )
        return cls(**normalized)
