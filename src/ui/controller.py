"""UI controller that calls only the runtime facade."""

from __future__ import annotations

from src.runtime.facade import RuntimeConfig, SplitterRuntimeFacade

from .view_models import UiPanelModel


class UiController:
    def __init__(self, facade: SplitterRuntimeFacade) -> None:
        self._facade: SplitterRuntimeFacade = facade

    def apply_config(self, config: RuntimeConfig) -> None:
        self._facade.update_config(config)

    def start_runtime(self) -> str:
        return self._facade.start_runtime()

    def stop_runtime(self) -> str:
        return self._facade.stop_runtime()

    def sync_project(self) -> str:
        return self._facade.sync_project()

    def clean_workers(self) -> str:
        return self._facade.clean_workers()

    def start_render(self) -> str:
        return self._facade.start_render()

    def cancel_render(self) -> str:
        return self._facade.cancel_render()

    def kick_all_workers(self) -> str:
        return self._facade.kick_all_workers()

    def force_start_server(self) -> bool:
        return self._facade.force_start_server()

    def reset_runtime(self, hard: bool = False) -> bool:
        return self._facade.reset_runtime(hard=hard)

    def run_integrity_check(self, timeout_s: float = 5.0) -> bool:
        return self._facade.run_integrity_check(timeout_s=timeout_s)

    def auto_install_requirements(self, modules: list[str] | None = None) -> bool:
        return self._facade.auto_install_requirements(modules=modules)

    def get_effective_mode(self) -> str:
        return self._facade.get_effective_mode()

    def last_error(self) -> str:
        return self._facade.last_error

    def panel_model(self) -> UiPanelModel:
        return UiPanelModel.from_status(self._facade.get_status())
