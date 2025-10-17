from __future__ import annotations

import json
from typing import Any, Dict, Optional, Sequence, Tuple

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


class TickAllocationLimiter:
    """Constrain AI task allocation while reserving direct message capacity."""

    def __init__(
        self,
        *,
        max_tasks: Optional[int],
        fallback: int = 4,
        min_dm_quota: int = 1,
        priority: Sequence[str] = ("replies", "threads"),
    ) -> None:
        self._requested_limit = max_tasks
        self._fallback = fallback
        self._min_dm_quota = max(int(min_dm_quota), 0)
        self._priority: Tuple[str, ...] = tuple(priority)

    def _coerced_limit(self) -> int:
        try:
            value = int(self._requested_limit)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            value = None
        if value is None or value <= 0:
            try:
                value = int(self._fallback)
            except (TypeError, ValueError):
                value = 0
        return max(value or 0, 0)

    def limit(self, allocation: Any) -> Any:
        """Clamp thread/reply counts so at least one DM can be scheduled."""

        max_total = self._coerced_limit()
        if max_total <= 0:
            return allocation

        requested_dm = getattr(allocation, "private_messages", 0) or 0
        try:
            requested_dm = int(requested_dm)
        except (TypeError, ValueError):
            requested_dm = 0
        requested_dm = max(requested_dm, 0)

        reserved_for_dm = min(requested_dm, self._min_dm_quota)
        remaining = max_total

        for attr in self._priority:
            current = getattr(allocation, attr, 0) or 0
            try:
                current = int(current)
            except (TypeError, ValueError):
                current = 0
            current = max(current, 0)

            if remaining <= reserved_for_dm:
                allowed = 0
            else:
                allowed = min(current, max(remaining - reserved_for_dm, 0))

            setattr(allocation, attr, allowed)
            remaining = max(remaining - allowed, 0)

        dm_allowed = min(requested_dm, max(remaining, 0))
        setattr(allocation, "private_messages", dm_allowed)
        return allocation

    __call__ = limit
