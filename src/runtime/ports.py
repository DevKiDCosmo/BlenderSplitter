"""Runtime-facing ports for infrastructure boundaries."""

from __future__ import annotations

from typing import Protocol


class TransportPort(Protocol):
    def send(self, target_id: str, payload: dict[str, object]) -> None: ...


class DiscoveryPort(Protocol):
    def discover(self) -> list[str]: ...


class BlenderOpsPort(Protocol):
    def render_tile(self, tile_payload: dict[str, object]) -> dict[str, object]: ...


class SyncStoragePort(Protocol):
    def save_bundle(self, bundle_id: str, payload: bytes) -> None: ...


class SchedulerCorePort(Protocol):
    def next_for_worker(self, worker_id: str) -> object | None: ...
