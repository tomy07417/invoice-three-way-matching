"""Pure retry scheduling; callers persist the returned instant, never sleep.

Provider/window floors apply before independent jitter, including beyond the cap.
The cap bounds local exponential growth, not a provider's mandatory minimum.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite


@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: float = 2
    cap_seconds: float = 60
    jitter_ratio: float = .5
    max_attempts: int = 3

    def __post_init__(self):
        if (not all(isfinite(x) for x in (self.base_seconds, self.cap_seconds, self.jitter_ratio))
                or self.base_seconds <= 0 or self.cap_seconds < self.base_seconds
                or not 0 <= self.jitter_ratio <= 1
                or type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 100):
            raise ValueError('Invalid retry configuration')

    def delay(self, attempt: int, *, random_value: float, minimum_seconds: float = 0) -> float:
        if type(attempt) is not int or attempt < 1:
            raise ValueError('Attempt must be positive')
        if not isfinite(random_value) or not 0 <= random_value <= 1:
            raise ValueError('Random value must be in [0, 1]')
        if not isfinite(minimum_seconds) or minimum_seconds < 0:
            raise ValueError('Minimum delay must be finite and nonnegative')
        # Saturate by iteration: arbitrary attempt counts cannot overflow 2**n.
        scaled = self.base_seconds
        for _ in range(min(attempt - 1, 1024)):
            scaled = min(self.cap_seconds, scaled * 2)
            if scaled == self.cap_seconds:
                break
        return max(scaled, minimum_seconds) + random_value * scaled * self.jitter_ratio

    def next_at(self, now: datetime, attempt: int, *, random_value: float,
                retryable: bool = True, floor: datetime | None = None) -> datetime | None:
        if not retryable or attempt >= self.max_attempts:
            return None
        return now + timedelta(seconds=self.delay(
            attempt, random_value=random_value,
            minimum_seconds=max(0, (floor - now).total_seconds()) if floor else 0))
