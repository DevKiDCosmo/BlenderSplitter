from dataclasses import dataclass


@dataclass
class ReconnectPolicy:
    rediscover_after: int = 3
    self_host_after: int = 8
    max_sleep: float = 3.0


class ReconnectController:
    def __init__(self, policy: ReconnectPolicy | None = None):
        self.policy = policy or ReconnectPolicy()
        self.failures = 0

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
        return min(self.policy.max_sleep, 0.4 + 0.35 * float(self.failures))
