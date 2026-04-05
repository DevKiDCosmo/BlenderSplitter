"""Core scheduling state machine scaffold."""

from __future__ import annotations

from .models import DispatchDecision, Job, WorkerState


class SchedulerCore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.workers: dict[str, WorkerState] = {}

    def plan(self, jobs: list[Job], workers: list[WorkerState]) -> list[DispatchDecision]:
        self.jobs = {j.job_id: j for j in jobs}
        self.workers = {w.worker_id: w for w in workers}
        decisions: list[DispatchDecision] = []

        candidates: list[WorkerState] = [w for w in workers if w.online and w.active_jobs < w.capacity]
        if not candidates:
            return decisions

        pointer = 0
        for job in jobs:
            if job.status != "pending":
                continue
            if not candidates:
                break

            worker: WorkerState = candidates[pointer % len(candidates)]
            if worker.active_jobs >= worker.capacity:
                pointer += 1
                continue

            job.status = "assigned"
            job.assigned_worker = worker.worker_id
            worker.active_jobs += 1
            decisions.append(DispatchDecision(job_id=job.job_id, worker_id=worker.worker_id, reason="planned"))
            pointer += 1

            candidates = [w for w in candidates if w.active_jobs < w.capacity and w.online]

        return decisions

    def next_for_worker(self, worker_id: str) -> Job | None:
        worker = self.workers.get(worker_id)
        if worker is None or (not worker.online) or worker.active_jobs >= worker.capacity:
            return None

        for job in self.jobs.values():
            if job.status == "pending":
                job.status = "assigned"
                job.assigned_worker = worker_id
                worker.active_jobs += 1
                return job
        return None

    def mark_result(self, job_id: str, worker_id: str, success: bool) -> None:
        job = self.jobs.get(job_id)
        if not job:
            return

        worker = self.workers.get(worker_id)
        if worker is not None:
            worker.active_jobs = max(0, worker.active_jobs - 1)

        if success:
            job.status = "completed"
        else:
            job.retry_count += 1
            if job.retry_count > job.max_retries:
                job.status = "failed"
            else:
                job.status = "pending"
                job.assigned_worker = None

    def reassign_lost_worker_jobs(self, worker_id: str) -> list[DispatchDecision]:
        decisions: list[DispatchDecision] = []
        available = [w for w in self.workers.values() if w.worker_id != worker_id and w.online and w.active_jobs < w.capacity]
        assign_idx = 0

        for job in self.jobs.values():
            if job.assigned_worker == worker_id and job.status == "assigned":
                job.status = "pending"
                job.assigned_worker = None

                if available:
                    replacement = available[assign_idx % len(available)]
                    assign_idx += 1
                    replacement.active_jobs += 1
                    job.status = "assigned"
                    job.assigned_worker = replacement.worker_id
                    decisions.append(DispatchDecision(job_id=job.job_id, worker_id=replacement.worker_id, reason="worker_lost_reassigned"))
                else:
                    decisions.append(DispatchDecision(job_id=job.job_id, worker_id="", reason="worker_lost_pending"))
        return decisions
