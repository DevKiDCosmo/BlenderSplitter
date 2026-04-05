"""Boundary tests for src/sync/service.py."""

import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.sync.service import SyncService
from src.sync.models import AckSummary, SyncBundleMeta


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestSyncBundleBuild:
    def test_build_bundle_returns_meta_with_correct_size(self):
        svc = SyncService()
        payload = {"key": "value", "number": 42}
        meta = svc.build_bundle(payload)

        assert meta.total_bytes > 0
        assert meta.checksum_sha256 != ""
        assert meta.bundle_id != ""

    def test_build_bundle_empty_payload(self):
        svc = SyncService()
        meta = svc.build_bundle({})

        assert meta.total_bytes > 0  # empty dict still encodes to `{}`
        assert meta.file_count == 1

    def test_build_bundle_checksum_is_deterministic(self):
        svc = SyncService()
        payload = {"a": 1}
        meta1 = svc.build_bundle(payload)
        meta2 = svc.build_bundle(payload)

        # Different bundle IDs but same checksum for identical content
        assert meta1.checksum_sha256 == meta2.checksum_sha256


class TestSyncChunking:
    def test_build_chunks_respects_chunk_size(self):
        # SyncService enforces a minimum chunk size of 1024 bytes; use a payload
        # that is bigger than 1024 to produce multiple chunks.
        svc = SyncService(chunk_size=1024)
        payload = {"data": "x" * 2000}
        meta = svc.build_bundle(payload)
        chunks = svc.build_chunks(meta)

        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.payload) <= 1024

    def test_build_chunks_single_chunk_for_small_payload(self):
        svc = SyncService(chunk_size=256 * 1024)
        meta = svc.build_bundle({"tiny": True})
        chunks = svc.build_chunks(meta)

        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0
        assert chunks[0].total_chunks == 1

    def test_build_chunks_all_same_transfer_id(self):
        svc = SyncService(chunk_size=8)
        meta = svc.build_bundle({"k": "v" * 20})
        chunks = svc.build_chunks(meta)

        transfer_ids = {c.transfer_id for c in chunks}
        assert transfer_ids == {meta.bundle_id}

    def test_chunks_reassemble_to_original(self):
        svc = SyncService(chunk_size=16)
        payload = {"payload": "hello world repeated multiple times"}
        meta = svc.build_bundle(payload)
        chunks = svc.build_chunks(meta)

        reassembled_ok = _run(svc.apply_bundle(meta, chunks))
        assert reassembled_ok is True


class TestSyncAck:
    def test_ack_all_workers_within_timeout(self):
        svc = SyncService()
        meta = svc.build_bundle({"x": 1})
        worker_ids = ["w1", "w2"]
        _run(svc.send_bundle(worker_ids, meta))

        svc.mark_ack("w1", ok=True)
        svc.mark_ack("w2", ok=True)

        summary = _run(svc.await_acks(worker_ids, timeout_s=1.0))

        assert summary.received == 2
        assert summary.timed_out == 0
        assert summary.ok is True

    def test_partial_ack_failure_reports_correctly(self):
        svc = SyncService()
        meta = svc.build_bundle({"x": 1})
        worker_ids = ["w1", "w2", "w3"]
        _run(svc.send_bundle(worker_ids, meta))

        # Only w1 and w2 acknowledge
        svc.mark_ack("w1", ok=True)
        svc.mark_ack("w2", ok=True)
        # w3 never acks → timeout

        summary = _run(svc.await_acks(worker_ids, timeout_s=0.1))

        assert summary.received == 2
        assert summary.timed_out == 1
        assert summary.ok is False

    def test_ack_timeout_with_no_acks(self):
        svc = SyncService()
        meta = svc.build_bundle({"x": 1})
        worker_ids = ["w1"]
        _run(svc.send_bundle(worker_ids, meta))

        summary = _run(svc.await_acks(worker_ids, timeout_s=0.05))

        assert summary.received == 0
        assert summary.timed_out == 1
        assert summary.ok is False

    def test_per_worker_status_in_summary(self):
        svc = SyncService()
        meta = svc.build_bundle({})
        worker_ids = ["w1", "w2"]
        _run(svc.send_bundle(worker_ids, meta))

        svc.mark_ack("w1", ok=True)
        svc.mark_ack("w2", ok=False)

        summary = _run(svc.await_acks(worker_ids, timeout_s=0.5))

        assert summary.per_worker["w1"]["ok"] is True
        assert summary.per_worker["w2"]["ok"] is False


class TestSyncApplyBundle:
    def test_apply_bundle_with_wrong_checksum_returns_false(self):
        svc = SyncService()
        meta = svc.build_bundle({"k": "v"})
        chunks = svc.build_chunks(meta)

        # Corrupt a chunk
        from src.sync.models import ChunkEnvelope
        bad_chunks = [
            ChunkEnvelope(
                chunk_index=c.chunk_index,
                total_chunks=c.total_chunks,
                payload=b"corrupted!",
                transfer_id=c.transfer_id,
            )
            for c in chunks
        ]

        result = _run(svc.apply_bundle(meta, bad_chunks))
        assert result is False

    def test_apply_bundle_unordered_chunks_still_reassemble(self):
        svc = SyncService(chunk_size=8)
        payload = {"data": "abcdefghij"}
        meta = svc.build_bundle(payload)
        chunks = svc.build_chunks(meta)

        # Reverse chunk order
        chunks_reversed = list(reversed(chunks))
        result = _run(svc.apply_bundle(meta, chunks_reversed))

        assert result is True
