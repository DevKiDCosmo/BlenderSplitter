"""Retry and reconnect policy helpers."""

from __future__ import annotations

from dataclasses import dataclass
import random


@dataclass
class RetryPolicy:
    rediscover_after: int = 3
    self_host_after: int = 8
    max_sleep: float = 3.0


class RetryController:
    def __init__(self, policy: RetryPolicy | None = None) -> None:
        self.policy: RetryPolicy = policy or RetryPolicy()
        self.failures: int = 0

    def reset(self) -> None:
        self.failures = 0

    def on_failure(self) -> int:
        self.failures += 1
        return self.failures

    def should_rediscover(self) -> bool:
        return self.failures >= self.policy.rediscover_after

    def should_self_host(self) -> bool:
        return self.failures >= self.policy.self_host_after

    def sleep_seconds(self) -> float:
        base: float = 0.3
        exponent: int = max(0, self.failures - 1)
        multiplier: int = 1 << exponent
        backoff: float = base * float(multiplier)
        jitter: float = random.random() * 0.25
        return min(self.policy.max_sleep, backoff + jitter)
