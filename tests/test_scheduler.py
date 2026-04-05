"""Boundary tests for src/scheduler/core.py."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.scheduler.core import SchedulerCore
from src.scheduler.models import DispatchDecision, Job, WorkerState


def _make_job(job_id: str, max_retries: int = 3) -> Job:
    return Job(job_id=job_id, max_retries=max_retries)


def _make_worker(worker_id: str, capacity: int = 1, online: bool = True) -> WorkerState:
    return WorkerState(worker_id=worker_id, capacity=capacity, online=online)


class TestSchedulerPlan:
    def test_plan_assigns_pending_jobs_to_available_workers(self):
        scheduler = SchedulerCore()
        jobs = [_make_job("j1"), _make_job("j2")]
        workers = [_make_worker("w1"), _make_worker("w2")]

        decisions = scheduler.plan(jobs, workers)

        assert len(decisions) == 2
        assigned_workers = {d.worker_id for d in decisions}
        assert "w1" in assigned_workers
        assert "w2" in assigned_workers

    def test_plan_respects_worker_capacity(self):
        scheduler = SchedulerCore()
        jobs = [_make_job(f"j{i}") for i in range(4)]
        workers = [_make_worker("w1", capacity=2)]

        decisions = scheduler.plan(jobs, workers)

        # Worker with capacity=2 can take at most 2 jobs
        assert len(decisions) == 2
        assert all(d.worker_id == "w1" for d in decisions)

    def test_plan_skips_offline_workers(self):
        scheduler = SchedulerCore()
        jobs = [_make_job("j1")]
        workers = [_make_worker("w_offline", online=False), _make_worker("w_online")]

        decisions = scheduler.plan(jobs, workers)

        assert len(decisions) == 1
        assert decisions[0].worker_id == "w_online"

    def test_plan_returns_empty_when_no_workers(self):
        scheduler = SchedulerCore()
        jobs = [_make_job("j1")]

        decisions = scheduler.plan(jobs, [])

        assert decisions == []

    def test_plan_returns_empty_when_no_pending_jobs(self):
        scheduler = SchedulerCore()
        job = _make_job("j1")
        job.status = "completed"
        workers = [_make_worker("w1")]

        decisions = scheduler.plan([job], workers)

        assert decisions == []

    def test_plan_sets_job_status_to_assigned(self):
        scheduler = SchedulerCore()
        job = _make_job("j1")
        workers = [_make_worker("w1")]

        scheduler.plan([job], workers)

        assert job.status == "assigned"
        assert job.assigned_worker == "w1"

    def test_immediate_redispatch_after_completion(self):
        """Worker completion should allow immediate eligibility for next job."""
        scheduler = SchedulerCore()
        jobs = [_make_job("j1"), _make_job("j2")]
        workers = [_make_worker("w1", capacity=1)]

        scheduler.plan(jobs, workers)
        # Mark first job done — worker should be free again
        scheduler.mark_result("j1", "w1", success=True)

        next_job = scheduler.next_for_worker("w1")
        assert next_job is not None
        assert next_job.job_id == "j2"


class TestSchedulerMarkResult:
    def test_successful_job_marks_completed(self):
        scheduler = SchedulerCore()
        job = _make_job("j1")
        workers = [_make_worker("w1")]
        scheduler.plan([job], workers)

        scheduler.mark_result("j1", "w1", success=True)

        assert scheduler.jobs["j1"].status == "completed"

    def test_failed_job_below_retry_limit_returns_to_pending(self):
        scheduler = SchedulerCore()
        job = _make_job("j1", max_retries=2)
        workers = [_make_worker("w1")]
        scheduler.plan([job], workers)

        scheduler.mark_result("j1", "w1", success=False)

        j = scheduler.jobs["j1"]
        assert j.status == "pending"
        assert j.retry_count == 1
        assert j.assigned_worker is None

    def test_failed_job_above_retry_limit_marks_failed(self):
        scheduler = SchedulerCore()
        job = _make_job("j1", max_retries=1)
        workers = [_make_worker("w1")]
        scheduler.plan([job], workers)
        scheduler.mark_result("j1", "w1", success=False)  # retry_count = 1
        # Second failure exceeds max_retries
        job.status = "assigned"
        job.assigned_worker = "w1"
        scheduler.mark_result("j1", "w1", success=False)

        assert scheduler.jobs["j1"].status == "failed"

    def test_mark_result_decrements_active_jobs(self):
        scheduler = SchedulerCore()
        job = _make_job("j1")
        workers = [_make_worker("w1")]
        scheduler.plan([job], workers)

        scheduler.mark_result("j1", "w1", success=True)

        assert scheduler.workers["w1"].active_jobs == 0


class TestSchedulerReassign:
    def test_reassign_lost_worker_jobs(self):
        """Disconnected worker jobs should be reassigned to available worker."""
        scheduler = SchedulerCore()
        # w1 has j1, w3 is idle; w2 also has a job but has extra capacity
        jobs = [_make_job("j1")]
        workers = [_make_worker("w1"), _make_worker("w_idle", capacity=1)]
        scheduler.plan(jobs, workers)

        # w_idle has no jobs assigned; simulate w1 going offline
        scheduler.workers["w1"].online = False
        decisions = scheduler.reassign_lost_worker_jobs("w1")

        reassigned = [d for d in decisions if d.reason == "worker_lost_reassigned"]
        assert len(reassigned) == 1
        assert reassigned[0].worker_id == "w_idle"

    def test_reassign_with_no_available_workers_marks_pending(self):
        scheduler = SchedulerCore()
        jobs = [_make_job("j1")]
        workers = [_make_worker("w1")]
        scheduler.plan(jobs, workers)

        # No other worker available
        decisions = scheduler.reassign_lost_worker_jobs("w1")

        assert len(decisions) == 1
        assert decisions[0].reason == "worker_lost_pending"
        assert scheduler.jobs["j1"].status == "pending"

    def test_reassign_does_not_touch_completed_jobs(self):
        scheduler = SchedulerCore()
        job = _make_job("j1")
        workers = [_make_worker("w1"), _make_worker("w2")]
        scheduler.plan([job], workers)
        scheduler.mark_result("j1", "w1", success=True)

        decisions = scheduler.reassign_lost_worker_jobs("w1")

        assert decisions == []
