"""Config loading/merging from config.json."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from .models import AppConfig, ConfigDict, ConfigValue


class ConfigStore:
    def __init__(self, config_path: str = "config.json") -> None:
        self._config_path: Path = Path(config_path)
        self._config: AppConfig = AppConfig()

    def load(self) -> AppConfig:
        if not self._config_path.exists():
            return self._config
        _ = self._config_path.read_text(encoding="utf-8")
        return self._config

    def get(self) -> AppConfig:
        return self._config

    def _merge(self, raw: dict[str, object]) -> AppConfig:
        def _get_str(value: object, fallback: str) -> str:
            return value if isinstance(value, str) else fallback

        def _get_str_list(value: object, fallback: list[str]) -> list[str]:
            if isinstance(value, list):
                value_list: list[object] = cast(list[object], value)
                if all(isinstance(v, str) for v in value_list):
                    return [cast(str, v) for v in value_list]
            return fallback

        def _to_config_value(value: object) -> ConfigValue | None:
            if isinstance(value, (str, int, float, bool)):
                return value
            return None

        def _get_section(value: object) -> ConfigDict:
            if isinstance(value, dict):
                raw_section = cast(dict[str, object], value)
                normalized: ConfigDict = {}
                for key, item in raw_section.items():
                    parsed = _to_config_value(item)
                    if parsed is not None:
                        normalized[key] = parsed
                return normalized
            return cast(ConfigDict, {})

        cfg = AppConfig()
        cfg.mode = _get_str(raw.get("mode"), cfg.mode)
        cfg.user_mode = _get_str(raw.get("user_mode"), cfg.user_mode)
        cfg.always = _get_str_list(raw.get("always"), cfg.always)
        cfg.network = {**cfg.network, **_get_section(raw.get("network"))}
        cfg.render = {**cfg.render, **_get_section(raw.get("render"))}
        cfg.external_scheduler = {**cfg.external_scheduler, **_get_section(raw.get("external_scheduler"))}
        return cfg
