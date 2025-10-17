"""Load and expose simulation configuration from TOML/JSON sources."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - fallback for Python <3.11
    import tomli as tomllib  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "simulation.toml"
DEFAULT_DECK_PATH = PROJECT_ROOT / "config" / "oracle_deck.json"

_CONFIG_CACHE: Dict[str, Any] | None = None
_CONFIG_PATH: Path | None = None
_CONFIG_MTIME: float | None = None
_DECK_CACHE: Dict[Path, Dict[str, Any]] = {}


def _resolve_path() -> Path:
    raw_path = os.getenv("SIM_CONFIG_PATH")
    if raw_path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_file():
            return candidate
        # allow relative paths inside repo even when file does not yet exist
        return (PROJECT_ROOT / candidate).resolve()
    return DEFAULT_CONFIG_PATH


def _read_toml(path: Path) -> Dict[str, Any]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Simulation config {path} must define a table at top level")
    return data


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Deck file {path} must contain an object at the root")
    return data


def _default_config() -> Dict[str, Any]:
    if DEFAULT_CONFIG_PATH.exists():
        try:
            return _read_toml(DEFAULT_CONFIG_PATH)
        except Exception:
            pass
    # Conservative minimal defaults mirroring the hand-crafted archetypes.
    return {
        "version": 1,
        "scheduler": {
            "interval_seconds": 60,
            "jitter_seconds": 12,
            "startup_delay_seconds": 10,
            "queue_burst": 10,
        },
        "cooldowns": {"thread": 12, "reply": 4, "post": 3, "dm": 6, "report": 8},
        "needs": {
            "floor": 0.1,
            "ceiling": 0.95,
            "drift_jitter": 0.025,
            "baseline": {
                "attention": 0.55,
                "status": 0.45,
                "belonging": 0.62,
                "novelty": 0.5,
                "catharsis": 0.48,
            },
            "drift": {
                "attention": -0.05,
                "status": -0.035,
                "belonging": -0.028,
                "novelty": -0.04,
                "catharsis": -0.045,
            },
        },
        "mood": {
            "suspicion_bias": 0.18,
            "bands": [
                {"label": "exhausted", "threshold": 0.25},
                {"label": "strained", "threshold": 0.45},
                {"label": "steady", "threshold": 0.65},
                {"label": "bright", "threshold": 0.82},
                {"label": "radiant", "threshold": 1.0},
            ],
        },
        "suspicion": {
            "decay": 0.035,
            "floor": 0.0,
            "ceiling": 1.0,
            "report_relief": 0.08,
            "dm_penalty": 0.03,
        },
        "reputation": {
            "decay": 0.02,
            "floor": -1.0,
            "ceiling": 1.0,
            "boost_per_report": 0.05,
        },
        "action_bias": {
            "reply": {
                "cooldown_penalty": 0.35,
                "needs": {"belonging": 0.45, "attention": 0.35, "novelty": 0.18},
            },
            "thread": {
                "cooldown_penalty": 0.45,
                "needs": {"status": 0.4, "novelty": 0.5, "attention": 0.32},
            },
            "dm": {
                "cooldown_penalty": 0.25,
                "needs": {"belonging": 0.55, "catharsis": 0.42},
            },
            "post": {
                "cooldown_penalty": 0.2,
                "needs": {"attention": 0.5, "belonging": 0.32, "catharsis": 0.28},
            },
            "report": {
                "cooldown_penalty": 0.4,
                "suspicion_weight": 0.65,
                "needs": {"status": 0.52, "catharsis": 0.2},
            },
        },
        "oracle": {
            "forum_capacity": 10_000,
            "omen_probability": 0.01,
            "seance_threshold": 12,
            "seance_probability": 0.12,
            "seance_reply_multiplier": 2.0,
            "seance_pm_multiplier": 1.6,
            "seance_thread_floor": 1,
            "deck_path": str(DEFAULT_DECK_PATH),
        },
        "archetypes": [],
    }


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    keys = set(base) | set(override)
    for key in keys:
        if key in override:
            ov = override[key]
            bv = base.get(key)
            if isinstance(bv, dict) and isinstance(ov, dict):
                result[key] = _deep_merge(bv, ov)
            else:
                result[key] = ov
        else:
            result[key] = base[key]
    return result


def _load_deck(path: Path) -> Dict[str, Any]:
    resolved = path
    if not resolved.is_absolute():
        resolved = (PROJECT_ROOT / path).resolve()
    if resolved in _DECK_CACHE:
        return _DECK_CACHE[resolved]
    if not resolved.exists():
        return {}
    deck = _read_json(resolved)
    _DECK_CACHE[resolved] = deck
    return deck


def load_config(*, force: bool = False) -> Dict[str, Any]:
    """Return the merged simulation configuration."""
    global _CONFIG_CACHE, _CONFIG_PATH, _CONFIG_MTIME
    cfg_path = _resolve_path()
    must_reload = force or _CONFIG_CACHE is None
    if not must_reload and _CONFIG_PATH == cfg_path and cfg_path.exists():
        current_mtime = cfg_path.stat().st_mtime
        if _CONFIG_MTIME != current_mtime:
            must_reload = True
    if not must_reload:
        return dict(_CONFIG_CACHE)  # type: ignore[arg-type]

    base = _default_config()
    override: Dict[str, Any] = {}
    if cfg_path.exists():
        override = _read_toml(cfg_path)
        _CONFIG_MTIME = cfg_path.stat().st_mtime
    else:
        _CONFIG_MTIME = None
    merged = _deep_merge(base, override)
    oracle_section = dict(merged.get("oracle", {}))
    deck_path = Path(str(oracle_section.get("deck_path", DEFAULT_DECK_PATH)))
    deck_payload = _load_deck(deck_path)
    if not deck_payload:
        deck_payload = _load_deck(DEFAULT_DECK_PATH)
    if deck_payload:
        oracle_section["deck"] = deck_payload
    merged["oracle"] = oracle_section
    _CONFIG_CACHE = merged
    _CONFIG_PATH = cfg_path
    return dict(merged)


def config_path() -> Path:
    return _CONFIG_PATH or _resolve_path()


def scheduler_settings() -> Dict[str, Any]:
    return dict(load_config().get("scheduler", {}))


def cooldowns() -> Dict[str, Any]:
    return dict(load_config().get("cooldowns", {}))


def needs_config() -> Dict[str, Any]:
    return dict(load_config().get("needs", {}))


def mood_config() -> Dict[str, Any]:
    return dict(load_config().get("mood", {}))


def suspicion_config() -> Dict[str, Any]:
    return dict(load_config().get("suspicion", {}))


def reputation_config() -> Dict[str, Any]:
    return dict(load_config().get("reputation", {}))


def action_bias() -> Dict[str, Any]:
    return dict(load_config().get("action_bias", {}))


def archetype_templates() -> list[Dict[str, Any]]:
    cfg = load_config()
    templates = cfg.get("archetypes")
    if isinstance(templates, list):
        return [dict(item) for item in templates]
    return []


def oracle_settings() -> Dict[str, Any]:
    oracle = dict(load_config().get("oracle", {}))
    deck = oracle.get("deck")
    if isinstance(deck, dict):
        oracle["deck"] = deck
    return oracle


def fingerprint() -> Dict[str, Any]:
    cfg = load_config()
    serialised = json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha1 = hashlib.sha1(serialised).hexdigest()
    return {
        "path": str(config_path()),
        "sha1": sha1,
        "version": cfg.get("version", 0),
    }


def snapshot() -> Dict[str, Any]:
    """Return a lightweight snapshot describing the active configuration."""
    cfg = load_config()
    return {
        "path": str(config_path()),
        "version": cfg.get("version", 0),
        "fingerprint": fingerprint()["sha1"],
        "scheduler": scheduler_settings(),
        "cooldowns": cooldowns(),
    }


def clear_cache() -> None:
    """Reset cached configuration to force a reload on next access."""
    global _CONFIG_CACHE, _CONFIG_MTIME, _CONFIG_PATH
    _CONFIG_CACHE = None
    _CONFIG_MTIME = None
    _CONFIG_PATH = None
    _DECK_CACHE.clear()
