from __future__ import annotations

import json
from typing import Any, Dict, Optional

from django.utils import timezone

from . import configuration as config_service

FREEZE_STATE_KEY = "tick_freeze_state"
LAST_TICK_KEY = "tick_last_run"
MANUAL_OVERRIDE_KEY = "tick_manual_override"

_DEFAULT_STATE: Dict[str, Any] = {
    "frozen": False,
    "toggled_at": None,
    "actor": None,
    "reason": None,
}


def _load_state() -> Dict[str, Any]:
    raw = config_service.get_value(FREEZE_STATE_KEY, "")
    if not raw:
        return {"frozen": False, "actor": None, "reason": None}
    try:
        return json.loads(raw)
    except Exception:
        return {"frozen": False, "actor": None, "reason": None}


def _persist_state(state: Dict[str, Any]) -> None:
    clean = {key: state.get(key) for key in _DEFAULT_STATE}
    config_service.set_value(FREEZE_STATE_KEY, json.dumps(clean))


def describe_state() -> Dict[str, Any]:
    """Return the current freeze toggle metadata."""
    return _load_state()


def is_frozen() -> bool:
    """True when tick accumulation is explicitly frozen."""
    return bool(_load_state().get("frozen"))


def freeze(*, actor: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
    """Enable the freeze flag and persist metadata."""
    state = _load_state()
    state.update(
        {
            "frozen": True,
            "toggled_at": timezone.now().isoformat(),
            "actor": actor,
            "reason": reason,
        }
    )
    _persist_state(state)
    return describe_state()


def unfreeze(*, actor: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    """Disable the freeze flag and persist metadata."""
    state = _load_state()
    state.update(
        {
            "frozen": False,
            "toggled_at": timezone.now().isoformat(),
            "actor": actor,
            "reason": note,
        }
    )
    _persist_state(state)
    return describe_state()


def toggle(*, actor: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
    """Flip the freeze flag."""
    if is_frozen():
        return unfreeze(actor=actor, note=reason)
    return freeze(actor=actor, reason=reason)


def state_label() -> str:
    d = describe_state()
    return "FROZEN" if d.get("frozen") else "LIVE"


def record_tick_run(tick_number: int, *, origin: str) -> None:
    """Persist a breadcrumb for the most recent tick execution."""
    payload = {
        "tick_number": int(tick_number),
        "origin": origin,
        "recorded_at": timezone.now().isoformat(),
    }
    config_service.set_value(LAST_TICK_KEY, json.dumps(payload))


def last_tick_run() -> Dict[str, Any]:
    """Return the metadata for the most recent tick run, when available."""
    raw = config_service.get_value(LAST_TICK_KEY, "")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def queue_manual_override(
    *,
    seed: int | None = None,
    oracle_card: str | None = None,
    energy_multiplier: float | None = None,
    force: bool = False,
    note: str | None = None,
    origin: str | None = None,
) -> Dict[str, Any]:
    """Persist manual tick override parameters for the next scheduler run."""

    payload: Dict[str, Any] = {
        "seed": seed,
        "oracle_card": oracle_card,
        "energy_multiplier": energy_multiplier,
        "force": bool(force),
        "note": note,
        "origin": origin or "manual-override",
        "queued_at": timezone.now().isoformat(),
    }
    config_service.set_value(MANUAL_OVERRIDE_KEY, json.dumps(payload))
    return payload


def pending_manual_override() -> Dict[str, Any]:
    """Return the currently queued override without consuming it."""

    raw = config_service.get_value(MANUAL_OVERRIDE_KEY, "")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def consume_manual_override() -> Dict[str, Any]:
    """Fetch and clear the queued manual override parameters."""

    payload = pending_manual_override()
    if not payload:
        return {}
    config_service.set_value(MANUAL_OVERRIDE_KEY, "")
    return payload
