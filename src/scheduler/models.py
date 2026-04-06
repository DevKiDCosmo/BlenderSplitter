"""Scheduler dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Job:
    job_id: str
    payload: dict[str, object] = field(default_factory=dict)
    retry_count: int = 0
    max_retries: int = 3
    assigned_worker: str | None = None
    status: str = "pending"


@dataclass
class WorkerState:
    worker_id: str
    active_jobs: int = 0
    capacity: int = 1
    online: bool = True


@dataclass
class DispatchDecision:
    job_id: str
    worker_id: str
    reason: str = ""
