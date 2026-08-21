"""
config_manager.py - ConfigManager: loads framework config from YAML.

Every key here maps to a value that was previously hardcoded somewhere in
the framework (Logger's log_dir default, Executor's unbounded timeout, no
variables file at all). If config.yaml is missing or a key is absent,
defaults matching the old hardcoded values are used, so the framework still
runs with zero configuration.
"""

import os

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a required dependency
    yaml = None

_DEFAULTS = {
    "framework": {
        "log_directory": "logs",
        "log_level": "INFO",
    },
    "execution": {
        "command_timeout": 300,
    },
    "variables": {
        "file": "common_variables.json",
    },
}


class ConfigManager:
    """Read-only view over framework configuration for one run."""

    def __init__(self, config_path: str = None):
        self.config_path = config_path
        self._data = _deep_copy(_DEFAULTS)
        if config_path:
            if not os.path.exists(config_path):
                raise FileNotFoundError(f"Config file not found: {config_path}")
            if yaml is None:
                raise RuntimeError("PyYAML is required to load a --config file")
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            _merge(self._data, loaded)

    @property
    def log_directory(self) -> str:
        return self._data["framework"]["log_directory"]

    @property
    def log_level(self) -> str:
        return self._data["framework"]["log_level"]

    @property
    def command_timeout(self):
        return self._data["execution"]["command_timeout"]

    @property
    def variables_file(self) -> str:
        return self._data["variables"]["file"]


def _deep_copy(d):
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in d.items()}


def _merge(base: dict, override: dict):
    """Shallow-per-section merge: only keys present in override replace the
    default, everything else keeps its default value."""
    for section, values in override.items():
        if isinstance(values, dict) and isinstance(base.get(section), dict):
            base[section].update(values)
        else:
            base[section] = values
