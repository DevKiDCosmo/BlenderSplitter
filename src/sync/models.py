"""Sync dataclasses for bundle/chunk/ack handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ChunkEnvelope:
    chunk_index: int
    total_chunks: int
    payload: bytes
    transfer_id: str


@dataclass
class SyncBundleMeta:
    bundle_id: str
    total_bytes: int
    file_count: int
    checksum_sha256: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AckSummary:
    expected: int
    received: int = 0
    timed_out: int = 0
    per_worker: dict[str, dict[str, str | int | bool]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.received >= self.expected and self.timed_out == 0
