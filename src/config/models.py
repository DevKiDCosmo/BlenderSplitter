"""Configuration models for runtime defaults and overlays."""

from __future__ import annotations

from dataclasses import dataclass, field

ConfigValue = str | int | float | bool
ConfigDict = dict[str, ConfigValue]


def _network_defaults() -> ConfigDict:
    return {
        "host": "0.0.0.0",
        "server_port": 8765,
        "discovery_port": 8766,
    }


def _render_defaults() -> ConfigDict:
    return {
        "overlap_percent": 3.0,
        "max_retries": 3,
        "auto_sync_project": True,
        "show_render_window": True,
        "server_render_tiles": True,
        "tile_coefficient": 1,
        "output_dir": "",
    }


def _external_scheduler_defaults() -> ConfigDict:
    return {
        "enabled": False,
        "script": "scheduler_app.py",
        "host": "127.0.0.1",
        "port": 9876,
    }


@dataclass
class AppConfig:
    mode: str = "master_worker"
    user_mode: str = "master_worker"
    always: list[str] = field(default_factory=lambda: ["NETWORK"])
    network: ConfigDict = field(default_factory=_network_defaults)
    render: ConfigDict = field(default_factory=_render_defaults)
    external_scheduler: ConfigDict = field(default_factory=_external_scheduler_defaults)
