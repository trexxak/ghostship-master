from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone

from forum.models import Agent, SessionActivity

DEFAULT_WINDOW_SECONDS = getattr(settings, "SESSION_ACTIVITY_WINDOW_SECONDS", 180)
DEFAULT_PRUNE_PROBABILITY = getattr(settings, "SESSION_ACTIVITY_PRUNE_PROBABILITY", 0.08)

DEFAULT_SCALING_TIERS = [
    {"min": 0, "max": 0, "tier": "dormant", "factor": 0.1},
    {"min": 1, "max": 1, "tier": "calm", "factor": 0.45},
    {"min": 2, "max": 3, "tier": "steady", "factor": 0.7},
    {"min": 4, "max": None, "tier": "busy", "factor": 1.0},
]

SCALING_TIERS = getattr(settings, "SESSION_ACTIVITY_SCALING", DEFAULT_SCALING_TIERS)


@dataclass
class SessionSnapshot:
    total: int
    organic: int
    window: int
    tier: str
    factor: float


def _ensure_session_key(request) -> Optional[str]:
    session = getattr(request, "session", None)
    if session is None:
        return None
    if not session.session_key:
        session.save()
    return session.session_key


def touch_session(request) -> None:
    """Mark the current session as active for adaptive scaling."""
    session_key = _ensure_session_key(request)
    if not session_key:
        return
    now = timezone.now()
    agent = getattr(request, "oi_agent", None)
    agent_ref = agent if isinstance(agent, Agent) else None
    acting_as_organic = bool(getattr(request, "oi_active", False))
    path = getattr(request, "path", "") or ""
    try:
        SessionActivity.objects.update_or_create(
            session_key=session_key,
            defaults={
                "agent": agent_ref,
                "acting_as_organic": acting_as_organic,
                "last_path": path[:255],
                "last_seen": now,
            },
        )
    except Exception:
        # Tracking should never break the request cycle.
        return
    # Opportunistic pruning to keep the table tidy.
    if random.random() < DEFAULT_PRUNE_PROBABILITY:
        prune_stale_sessions(now=now)


def prune_stale_sessions(*, now: Optional[timezone.datetime] = None, window_seconds: Optional[int] = None) -> int:
    """Remove activity rows older than the configured window."""
    window = int(window_seconds or DEFAULT_WINDOW_SECONDS)
    reference = now or timezone.now()
    cutoff = reference - timedelta(seconds=window)
    deleted, _ = SessionActivity.objects.filter(last_seen__lt=cutoff).delete()
    return deleted


def _scaling_for_sessions(total_sessions: int) -> tuple[str, float]:
    for tier in SCALING_TIERS:
        minimum = tier.get("min", 0)
        maximum = tier.get("max")
        if maximum is None:
            if total_sessions >= minimum:
                return str(tier.get("tier", "busy")), float(tier.get("factor", 1.0))
        else:
            if minimum <= total_sessions <= maximum:
                return str(tier.get("tier", "busy")), float(tier.get("factor", 1.0))
    return "busy", 1.0


def session_snapshot(window_seconds: Optional[int] = None) -> SessionSnapshot:
    window = int(window_seconds or DEFAULT_WINDOW_SECONDS)
    cutoff = timezone.now() - timedelta(seconds=window)
    active_qs = SessionActivity.objects.filter(last_seen__gte=cutoff)
    total = active_qs.count()
    organic = active_qs.filter(acting_as_organic=True).count()
    tier, factor = _scaling_for_sessions(total)
    return SessionSnapshot(total=total, organic=organic, window=window, tier=tier, factor=factor)


def apply_activity_scaling(allocation, snapshot: SessionSnapshot):
    """Downshift allocation counts based on active human presence."""
    factor = float(snapshot.factor)
    tier = snapshot.tier
    if factor >= 0.99:
        allocation.notes.append(f"activity:{tier} (sessions={snapshot.total}, factor={factor:.2f})")
        return allocation

    def _scaled(value: int) -> int:
        if value <= 0:
            return value
        scaled = int(round(value * factor))
        if factor >= 0.5 and scaled == 0 and value > 0:
            return 1
        return max(0, scaled)

    allocation.registrations = _scaled(allocation.registrations)
    allocation.threads = _scaled(allocation.threads)
    allocation.replies = _scaled(allocation.replies)
    allocation.private_messages = _scaled(allocation.private_messages)
    allocation.moderation_events = _scaled(allocation.moderation_events)
    allocation.notes.append(f"activity:{tier} (sessions={snapshot.total}, factor={factor:.2f})")
    return allocation
