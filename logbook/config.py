"""Configuration: load, validate, and first-run copy.

Reads config.json (JSON via the stdlib — never YAML, which would break the
stdlib-only rule). On first run, copies config.example.json to config.json so
the tool starts with sane defaults. Expands ``~`` in paths.

Mirrors ``engine_hours_baseline`` into the ``meta`` table and warns if the two
ever disagree — config can be lost or copied to another machine, and cumulative
hours must not change silently.

Build order: with the core (step 2 area).
Spec: §7 (configuration).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from logbook import db

# (section, key) pairs that must be present for the tool to run at all.
_REQUIRED = (
    ("paths", "database"),
    ("paths", "backup_dir"),
    ("vessel", "sails"),
)


class ConfigError(RuntimeError):
    """Raised when configuration is missing or malformed."""


class Config:
    """Typed, read-only view over config.json."""

    def __init__(self, data: dict, path: Path) -> None:
        self._data = data
        self.path = path

    # -- paths (``~`` expanded) ----------------------------------------------

    @property
    def database_path(self) -> Path:
        return Path(self._data["paths"]["database"]).expanduser()

    @property
    def backup_dir(self) -> Path:
        return Path(self._data["paths"]["backup_dir"]).expanduser()

    # -- vessel ---------------------------------------------------------------

    @property
    def vessel_name(self) -> str:
        return self._data["vessel"].get("name", "")

    @property
    def sails(self) -> list[dict]:
        return list(self._data["vessel"]["sails"])

    @property
    def engine_hours_baseline(self) -> float:
        return float(self._data["vessel"].get("engine_hours_baseline", 0) or 0)

    @property
    def engine_hours_baseline_note(self) -> str:
        return self._data["vessel"].get("engine_hours_baseline_note", "none")

    # -- logging thresholds (defaults mirror config.example.json) --------------

    def _logging(self, key: str, default: float) -> float:
        return float(self._data.get("logging", {}).get(key, default))

    @property
    def autolog_interval_min(self) -> float:
        return self._logging("autolog_interval_min", 30)

    @property
    def distance_sample_sec(self) -> float:
        return self._logging("distance_sample_sec", 30)

    @property
    def distance_persist_min(self) -> float:
        return self._logging("distance_persist_min", 5)

    @property
    def speed_gate_kn(self) -> float:
        return self._logging("speed_gate_kn", 0.5)

    @property
    def backdate_tolerance_sec(self) -> float:
        return self._logging("backdate_tolerance_sec", 60)

    @property
    def clock_offset_warn_sec(self) -> float:
        return self._logging("clock_offset_warn_sec", 60)

    # -- ui -------------------------------------------------------------------

    @property
    def theme(self) -> str:
        """'light' (daylight, the default) or 'dark' (night mode). F2 toggles."""
        return str(self._data.get("ui", {}).get("theme", "light"))

    # -- backup ---------------------------------------------------------------

    @property
    def backup_retention(self) -> int:
        """How many timestamped snapshots to keep (§3.6). Defaults if absent, so
        a config written before this key existed still loads."""
        return int(self._data.get("backup", {}).get("retention", 10))


def load(config_path: str | Path, *, example_path: str | Path) -> Config:
    """Load config.json, copying the example on first run."""
    config_path = Path(config_path)
    if not config_path.exists():
        example_path = Path(example_path)
        if not example_path.exists():
            raise ConfigError(
                f"no config at {config_path} and no example at {example_path}")
        shutil.copyfile(example_path, config_path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{config_path} is not valid JSON: {exc}") from exc
    _validate(data, config_path)
    return Config(data, config_path)


def _validate(data: dict, path: Path) -> None:
    for section, key in _REQUIRED:
        if not isinstance(data.get(section), dict) or key not in data[section]:
            raise ConfigError(f"{path} is missing required '{section}.{key}'")
    if not isinstance(data["vessel"]["sails"], list):
        raise ConfigError(f"{path}: vessel.sails must be a list")


def sync_baseline(cfg: Config, d: db.Database) -> list[str]:
    """Mirror the engine-hours baseline into ``meta`` on first run; warn on drift.

    ``meta`` is authoritative once written: cumulative hours must not change
    silently (§7). A config that later disagrees is surfaced as a warning, never
    applied over the stored value.
    """
    warnings: list[str] = []
    cfg_val = _format_baseline(cfg.engine_hours_baseline)
    stored = d.get_meta("engine_hours_baseline")
    if stored is None:
        d.set_meta("engine_hours_baseline", cfg_val)
        d.set_meta("engine_hours_baseline_note", cfg.engine_hours_baseline_note)
    elif stored != cfg_val:
        warnings.append(
            f"config engine_hours_baseline ({cfg_val} h) disagrees with the stored "
            f"value ({stored} h); keeping the stored value — cumulative hours must "
            f"not change silently")
    return warnings


def _format_baseline(hours: float) -> str:
    # stable string form so 1800 and 1800.0 compare equal
    return f"{hours:g}"
