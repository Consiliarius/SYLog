"""Configuration: load, validate, and first-run copy.

Reads config.json (JSON via the stdlib — never YAML, which would break the
stdlib-only rule). On first run, copies config.example.json to config.json so
the tool starts with sane defaults. Expands ``~`` in paths.

Mirrors ``engine_hours_baseline`` into the ``meta`` table and warns if the two
ever disagree — config can be lost or copied to another machine, and cumulative
hours must not change silently.

Also mirrors the vessel's IDENTITY into ``meta`` (§15.4), so the export never
depends on config, which is not archived (§8). That mirror carries the OPPOSITE
rule — config wins, quietly — because identity is not a derived figure. The two
semantics are deliberate; see sync_vessel_identity().

Build order: with the core (step 2 area).
Spec: §7 (configuration), §15 (vessel reference).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from logbook import db

# (section, key) pairs that must be present for the tool to run at all.
_REQUIRED = (
    ("paths", "database"),
    ("paths", "backup_dir"),
    ("vessel", "sails"),
)

# Vessel reference fields (§15.2). Dimensions are NUMBERS in metres (rendered to
# at most 1 dp); identity fields are STRINGS — they are identifiers, not
# quantities, so leading zeros and formatting survive. All are optional.
VESSEL_DIMENSIONS = ("length", "beam", "draught", "air_draught")
VESSEL_IDENTITY = ("ssr", "callsign", "mmsi")

# Identity mirrored into meta so the export never reads config (§8, §15.4).
_VESSEL_META_KEYS = {
    "name": "vessel_name", "ssr": "vessel_ssr",
    "callsign": "vessel_callsign", "mmsi": "vessel_mmsi",
}


class ConfigError(RuntimeError):
    """Raised when configuration is missing or malformed."""


class Config:
    """Typed, read-only view over config.json."""

    def __init__(self, data: dict, path: Path) -> None:
        self._data = data
        self.path = path

    # -- the Settings editor's write surface (§15.5) --------------------------

    @property
    def data(self) -> dict:
        """The raw loaded config — live and mutable.

        The Settings editor edits *this dict in place* and calls save(). Mutating
        the loaded document, rather than reconstructing one from known keys, is
        precisely what preserves any key this build does not know about.
        """
        return self._data

    def save(self) -> Path:
        """Write config.json atomically, keeping the previous file as ``.bak``.

        The tool has only ever READ config; writing it is a new capability, so it
        borrows export.py's discipline — a temp file in the same directory, then
        ``os.replace``. A power cut mid-write must not be able to leave a
        half-written config, because that would stop the tool starting at all.

        **The write is verified before this returns.** The file is read back and
        compared to what was written; a save that silently did not take — a full
        disk, a filesystem that lies, an ``os.replace`` that did nothing — RAISES
        rather than reporting a false success. The Settings editor's "Saved"
        message rides on that guarantee: it must never appear over a write that
        did not land (a checklist built after an earlier save, lost on restart).
        """
        path = Path(self.path)
        text = json.dumps(self._data, indent=2, ensure_ascii=False) + "\n"
        if path.exists():                       # last-known-good, cheap insurance
            shutil.copyfile(path, path.with_name(path.name + ".bak"))
        handle, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        os.close(handle)
        try:
            Path(tmp).write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        if path.read_text(encoding="utf-8") != text:   # the write MUST have landed
            raise ConfigError(f"{path} did not persist as written")
        return path

    # -- paths (``~`` expanded) ----------------------------------------------

    @property
    def database_path(self) -> Path:
        return Path(self._data["paths"]["database"]).expanduser()

    @property
    def backup_dir(self) -> Path:
        return Path(self._data["paths"]["backup_dir"]).expanduser()

    # -- companion apps (§17) -------------------------------------------------

    @property
    def moorwatch_dir(self) -> Path | None:
        """Where Moorwatch is installed — the directory its module is run from.

        None when absent or empty, and that is what HIDES the launcher button
        rather than showing one that cannot work: a boat without Moorwatch
        installed is a normal boat, not a misconfigured one. Same "absent rather
        than blank" rule as vessel_reference (§15.2).

        Under ``tools`` and not ``paths``: ``paths`` is the two locations the
        tool cannot start without, and the Settings editor leaves it out to keep
        invariant 11 (database never inside the backup directory) away from a
        text box. This key carries neither property, and is editable (§17.3).

        Only the DIRECTORY lives here; the command is a constant in
        ``companion.py`` (§17.2). Defaults to absent, so a config written before
        this key existed still loads.
        """
        value = self._data.get("tools", {}).get("moorwatch_dir", "")
        return Path(value).expanduser() if value else None

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

    @property
    def vessel_reference(self) -> dict:
        """Name + dimensions + identity, for the launch card and session bar (§15.3).

        Unset fields (absent, ``null`` or ``""``) come back ABSENT rather than
        empty, so a display simply omits them — and a vessel with nothing
        configured yields ``{}``, which hides both surfaces entirely rather than
        showing a grid of blanks.
        """
        vessel = self._data.get("vessel", {})
        out: dict = {}
        for key in ("name", *VESSEL_DIMENSIONS, *VESSEL_IDENTITY):
            value = vessel.get(key)
            if value is not None and value != "":
                out[key] = value
        return out

    # -- checklists (§14.4) ---------------------------------------------------

    @property
    def checklists(self) -> list[dict]:
        """Configured checklists — top-level, optional. Defaults to [] so a config
        predating the feature still loads, and 'none configured' is a valid state
        (the Checklists button simply shows an empty list)."""
        return list(self._data.get("checklists", []))

    @property
    def locations(self) -> list[str]:
        """Standing departure/arrival place names available on every passage — a
        home port or regular stop. Merged ahead of recent history in the
        Depart/Arrive picker. Top-level, optional; defaults to []."""
        return [str(x) for x in self._data.get("locations", [])]

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

    @property
    def backup_interval_min(self) -> float:
        """Minutes between automatic in-session snapshots (§3.6). 0 disables the
        periodic backup, leaving only the one taken on session close. Defaults if
        absent, so a config written before this key existed still loads."""
        return float(self._data.get("backup", {}).get("interval_min", 30))

    # -- export ---------------------------------------------------------------

    @property
    def html_export(self) -> bool:
        """Generate the HTML review pages beside the CSVs (§14.10.1).

        Per MACHINE, not per export: the netbook is the one that might want this
        off, and that is a standing property of the machine rather than a
        decision to take at each End Session.

        Defaults ON, so a config written before this key existed still loads and
        still gets the pages. Turning it off costs nothing archival — the CSVs
        are the record (§8) and are written either way; only the review view is
        skipped.
        """
        return bool(self._data.get("export", {}).get("html", True))


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


def sync_vessel_identity(cfg: Config, d: db.Database) -> None:
    """Mirror the vessel's identity into ``meta`` (§15.4).

    The export reads ``meta``, never config, because the archival artefact cannot
    depend on a file that is not itself archived (§8) — the same pattern
    engine-cumulative.csv already uses for the baseline.

    **CONFIG WINS, quietly — deliberately the opposite of sync_baseline() above.**
    There, ``meta`` is authoritative and drift only warns, because cumulative
    engine hours must never change silently. That reasoning does not extend to
    identity: it is not a derived figure, and a mistyped callsign should simply be
    correctable. So this overwrites ``meta`` from config on every start, without a
    warning. Two different mirror semantics live in ``meta``; the difference is
    intentional.

    A field cleared in config is mirrored as empty, so ``meta`` tracks config
    exactly rather than retaining a value the skipper deleted.
    """
    reference = cfg.vessel_reference
    for key, meta_key in _VESSEL_META_KEYS.items():
        d.set_meta(meta_key, reference.get(key, ""))


def _format_baseline(hours: float) -> str:
    # stable string form so 1800 and 1800.0 compare equal
    return f"{hours:g}"
