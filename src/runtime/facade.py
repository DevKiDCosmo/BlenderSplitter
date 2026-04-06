"""Public runtime facade used by UI and addon startup."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from types import ModuleType
from typing import Protocol, cast

from .orchestrator import RuntimeOperation, RuntimeOrchestrator

Scalar = str | int | float | bool | None


class LegacyManager(Protocol):
    started: bool
    role: str
    status: str
    pending_jobs: dict[str, object]
    completed_jobs: dict[str, object]
    connected_workers: dict[str, object]
    sync_progress: dict[str, Scalar]
    sync_active: bool
    auto_sync_project: bool
    last_error: str

    def configure(
        self,
        host: str,
        server_port: int,
        discovery_port: int,
        overlap_percent: float,
        max_retries: int,
        auto_sync_project: bool,
        show_render_window: bool,
        server_render_tiles: bool,
        tile_coefficient: int,
        output_dir: str,
    ) -> None: ...

    def configure_runtime_modes(self, startup_mode: str = "user", user_mode: str = "master_worker", always_flags: list[str] | None = None) -> None: ...

    def configure_external_scheduler(self, enabled: bool = False, script: str = "scheduler_app.py", host: str = "127.0.0.1", port: int = 9876) -> None: ...

    def set_force_server(self, enabled: bool) -> None: ...

    def start(self) -> bool: ...

    def stop(self) -> bool: ...

    def sync_project_files(self, timeout_seconds: float = 180.0) -> bool: ...

    def clean_worker_blends(self) -> bool: ...

    def start_distributed_render(self) -> bool: ...

    def cancel_render(self) -> bool: ...

    def kick_all_workers(self) -> bool: ...

    def effective_mode(self) -> str: ...

    def auto_install_requirements(self, only_modules: list[str] | None = None) -> bool: ...

    def run_integrity_check(self, timeout_seconds: float = 5.0) -> bool: ...

    def reset_runtime(self, hard: bool = False) -> bool: ...

    def force_start_server(self) -> bool: ...


class LegacyWorkerModule(Protocol):
    def manager(self) -> LegacyManager: ...


@dataclass
class RuntimeConfig:
    host: str = "0.0.0.0"
    server_port: int = 8765
    discovery_port: int = 8766
    startup_mode: str = "master_worker"
    output_dir: str = ""
    overlap_percent: float = 3.0
    tile_coefficient: int = 1
    max_retries: int = 3
    auto_sync_project: bool = True
    show_render_window: bool = True
    server_render_tiles: bool = True
    external_scheduler_enabled: bool = False
    external_scheduler_script: str = "scheduler_app.py"
    external_scheduler_host: str = "127.0.0.1"
    external_scheduler_port: int = 9876


@dataclass
class PanelStatus:
    started: bool = False
    role: str = "unassigned"
    status_line: str = "idle"
    workers_online: int = 0
    render_done: int = 0
    render_total: int = 0
    sync_active: bool = False
    sync_progress_0_1: float | None = None


class SplitterRuntimeFacade:
    """Small API surface for runtime operations.

    TODO: Wire calls incrementally to current `worker.py` manager.
    """

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self._config: RuntimeConfig = config or RuntimeConfig()
        self._status: PanelStatus = PanelStatus()
        self._orchestrator: RuntimeOrchestrator = RuntimeOrchestrator()
        self._legacy_error: str = ""

    def _get_legacy_manager(self) -> LegacyManager | None:
        module: ModuleType | None = None
        try:
            module = importlib.import_module("worker")
        except ImportError:
            module = None

        if module is None:
            try:
                package_name = __package__ or ""
                root_package = package_name.split(".")[0] if package_name else ""
                if root_package:
                    module = importlib.import_module(f"{root_package}.worker")
            except ImportError:
                module = None

        if module is None:
            self._legacy_error = "Legacy-Manager nicht importierbar"
            return None
        try:
            legacy_module = cast(LegacyWorkerModule, cast(object, module))
            return legacy_module.manager()
        except (AttributeError, TypeError, RuntimeError, ValueError) as exc:
            self._legacy_error = f"Legacy-Manager Aufruf fehlgeschlagen: {exc}"
            return None

    def _apply_config_to_legacy(self, mgr: LegacyManager) -> None:
        mgr.configure(
            self._config.host,
            self._config.server_port,
            self._config.discovery_port,
            self._config.overlap_percent,
            self._config.max_retries,
            self._config.auto_sync_project,
            self._config.show_render_window,
            self._config.server_render_tiles,
            self._config.tile_coefficient,
            self._config.output_dir,
        )
        mgr.configure_runtime_modes(startup_mode=self._config.startup_mode)
        mgr.configure_external_scheduler(
            enabled=self._config.external_scheduler_enabled,
            script=self._config.external_scheduler_script,
            host=self._config.external_scheduler_host,
            port=self._config.external_scheduler_port,
        )

    def _status_from_legacy(self, mgr: LegacyManager) -> PanelStatus:
        pending_jobs = mgr.pending_jobs
        completed_jobs = mgr.completed_jobs
        connected_workers = mgr.connected_workers
        sync_progress = mgr.sync_progress
        progress_raw = sync_progress.get("progress")
        progress_value = float(progress_raw) if isinstance(progress_raw, (int, float)) else None

        return PanelStatus(
            started=bool(mgr.started),
            role=str(mgr.role),
            status_line=str(mgr.status),
            workers_online=len(connected_workers),
            render_done=len(completed_jobs),
            render_total=len(completed_jobs) + len(pending_jobs),
            sync_active=bool(mgr.sync_active),
            sync_progress_0_1=progress_value,
        )

    def _update_status(self) -> None:
        mgr = self._get_legacy_manager()
        if mgr is None:
            if self._legacy_error:
                self._status.status_line = self._legacy_error
            return
        self._status = self._status_from_legacy(mgr)

    def boot(self, config: RuntimeConfig, auto_start: bool = True) -> None:
        self._config = config
        if auto_start:
            _ = self.start_runtime()

    def shutdown(self) -> None:
        _ = self.stop_runtime()

    def update_config(self, config: RuntimeConfig) -> None:
        self._config = config
        mgr = self._get_legacy_manager()
        if mgr is not None:
            self._apply_config_to_legacy(mgr)
            self._update_status()

    def start_runtime(self, force_server: bool = False) -> str:
        self._orchestrator.enqueue(RuntimeOperation("start_runtime", {"force_server": force_server}))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return "start_runtime"

        self._apply_config_to_legacy(mgr)
        if force_server:
            mgr.set_force_server(True)
        _ = bool(mgr.start())
        self._update_status()
        return "start_runtime"

    def stop_runtime(self) -> str:
        self._orchestrator.enqueue(RuntimeOperation("stop_runtime"))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return "stop_runtime"
        _ = bool(mgr.stop())
        self._update_status()
        return "stop_runtime"

    def sync_project(self, timeout_s: float = 180.0) -> str:
        self._orchestrator.enqueue(RuntimeOperation("sync_project", {"timeout_s": timeout_s}))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return "sync_project"
        _ = bool(mgr.sync_project_files(timeout_seconds=timeout_s))
        self._update_status()
        return "sync_project"

    def clean_workers(self, timeout_s: float = 12.0) -> str:
        self._orchestrator.enqueue(RuntimeOperation("clean_workers", {"timeout_s": timeout_s}))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return "clean_workers"
        _ = bool(mgr.clean_worker_blends())
        self._update_status()
        return "clean_workers"

    def start_render(self, auto_sync: bool | None = None) -> str:
        self._orchestrator.enqueue(RuntimeOperation("start_render", {"auto_sync": auto_sync}))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return "start_render"
        if auto_sync is not None:
            mgr.auto_sync_project = bool(auto_sync)
        _ = bool(mgr.start_distributed_render())
        self._update_status()
        return "start_render"

    def cancel_render(self) -> str:
        self._orchestrator.enqueue(RuntimeOperation("cancel_render"))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return "cancel_render"
        _ = bool(mgr.cancel_render())
        self._update_status()
        return "cancel_render"

    def kick_all_workers(self) -> str:
        self._orchestrator.enqueue(RuntimeOperation("kick_all_workers"))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return "kick_all_workers"
        _ = bool(mgr.kick_all_workers())
        self._update_status()
        return "kick_all_workers"

    def get_status(self) -> PanelStatus:
        self._update_status()
        return self._status

    @property
    def last_error(self) -> str:
        mgr = self._get_legacy_manager()
        if mgr is None:
            return self._legacy_error
        return str(mgr.last_error or self._legacy_error)

    def get_effective_mode(self) -> str:
        mgr = self._get_legacy_manager()
        if mgr is None:
            return "user"
        return str(mgr.effective_mode())

    def auto_install_requirements(self, modules: list[str] | None = None) -> bool:
        self._orchestrator.enqueue(RuntimeOperation("auto_install_requirements", {"modules": modules or []}))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return False
        result = bool(mgr.auto_install_requirements(only_modules=modules))
        self._update_status()
        return result

    def run_integrity_check(self, timeout_s: float = 5.0) -> bool:
        self._orchestrator.enqueue(RuntimeOperation("run_integrity_check", {"timeout_s": timeout_s}))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return False
        result = bool(mgr.run_integrity_check(timeout_seconds=timeout_s))
        self._update_status()
        return result

    def reset_runtime(self, hard: bool = False) -> bool:
        self._orchestrator.enqueue(RuntimeOperation("reset_runtime", {"hard": hard}))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return False
        result = bool(mgr.reset_runtime(hard=hard))
        self._update_status()
        return result

    def force_start_server(self) -> bool:
        self._orchestrator.enqueue(RuntimeOperation("force_start_server"))
        mgr = self._get_legacy_manager()
        if mgr is None:
            self._status.status_line = self._legacy_error
            return False
        self._apply_config_to_legacy(mgr)
        result = bool(mgr.force_start_server())
        self._update_status()
        return result

    def get_legacy_manager_for_display(self) -> LegacyManager | None:
        """Return the legacy manager for read-only display during migration.

        Callers must treat the returned object as opaque and must not mutate
        state through it.  Use the typed facade methods for all write operations.
        """
        return self._get_legacy_manager()
