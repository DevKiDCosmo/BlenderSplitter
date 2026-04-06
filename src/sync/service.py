"""Sync service scaffold.

The real implementation will incrementally absorb logic from worker.py.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid

from .models import AckSummary, ChunkEnvelope, SyncBundleMeta


def _to_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


class SyncService:
    def __init__(self, chunk_size: int = 256 * 1024) -> None:
        self.chunk_size: int = max(1024, int(chunk_size))
        self._bundle_store: dict[str, bytes] = {}
        self._pending_acks: dict[str, bool] = {}

    def build_bundle(self, payload: dict[str, object] | None = None) -> SyncBundleMeta:
        data = _to_bytes(payload or {})
        bundle_id = uuid.uuid4().hex
        checksum = hashlib.sha256(data).hexdigest()
        self._bundle_store[bundle_id] = data
        return SyncBundleMeta(
            bundle_id=bundle_id,
            total_bytes=len(data),
            file_count=1,
            checksum_sha256=checksum,
        )

    def build_chunks(self, bundle_meta: SyncBundleMeta) -> list[ChunkEnvelope]:
        data = self._bundle_store.get(bundle_meta.bundle_id, b"")
        if not data:
            return [ChunkEnvelope(chunk_index=0, total_chunks=1, payload=b"", transfer_id=bundle_meta.bundle_id)]

        total_chunks = int(math.ceil(len(data) / float(self.chunk_size)))
        chunks: list[ChunkEnvelope] = []
        for idx in range(total_chunks):
            start = idx * self.chunk_size
            end = start + self.chunk_size
            chunks.append(
                ChunkEnvelope(
                    chunk_index=idx,
                    total_chunks=total_chunks,
                    payload=data[start:end],
                    transfer_id=bundle_meta.bundle_id,
                )
            )
        return chunks

    async def send_bundle(self, worker_ids: list[str], bundle_meta: SyncBundleMeta) -> list[ChunkEnvelope]:
        for worker_id in worker_ids:
            self._pending_acks[worker_id] = False
        return self.build_chunks(bundle_meta)

    def mark_ack(self, worker_id: str, ok: bool = True) -> None:
        if worker_id in self._pending_acks:
            self._pending_acks[worker_id] = bool(ok)

    async def await_acks(self, worker_ids: list[str], timeout_s: float) -> AckSummary:
        summary = AckSummary(expected=len(worker_ids))
        deadline = time.time() + max(0.1, float(timeout_s))

        while time.time() < deadline:
            received = 0
            for worker_id in worker_ids:
                ok = bool(self._pending_acks.get(worker_id, False))
                summary.per_worker[worker_id] = {"ok": ok}
                if ok:
                    received += 1
            summary.received = received
            if received >= summary.expected:
                break
            time.sleep(0.05)

        summary.timed_out = max(0, summary.expected - summary.received)
        return summary

    async def apply_bundle(self, bundle_meta: SyncBundleMeta, chunks: list[ChunkEnvelope]) -> bool:
        ordered = sorted(chunks, key=lambda ch: int(ch.chunk_index))
        data = b"".join(ch.payload for ch in ordered)
        digest = hashlib.sha256(data).hexdigest()
        if digest != bundle_meta.checksum_sha256:
            return False
        self._bundle_store[bundle_meta.bundle_id] = data
        return True
