from __future__ import annotations

from typing import Any

from django.utils import timezone

from forum.models import Agent, ModerationTicket
from forum.services._safe import safe_save


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _persist_mind_state(agent: Agent, key: str, value: float) -> None:
    mind_state: dict[str, Any] = dict(agent.mind_state or {})
    mind_state[key] = round(value, 3)
    agent.mind_state = mind_state
    safe_save(agent, ["mind_state", "updated_at"])


def adjust_frustration(agent: Agent | None, delta: float) -> None:
    if agent is None:
        return
    current = float(agent.mind_state.get("frustration", 0.0)
                    if agent.mind_state else 0.0)
    _persist_mind_state(agent, "frustration", _clamp(current + delta))


def adjust_admin_stress(delta: float) -> None:
    admin = (
        Agent.objects.filter(role=Agent.ROLE_ADMIN)
        .order_by("id")
        .first()
    )
    if not admin:
        return
    current = float(admin.mind_state.get("stress", 0.2)
                    if admin.mind_state else 0.2)
    _persist_mind_state(admin, "stress", _clamp(current + delta))


def backlog_pressure() -> None:
    open_count = ModerationTicket.objects.filter(status__in=[
        ModerationTicket.STATUS_OPEN,
        ModerationTicket.STATUS_TRIAGED,
        ModerationTicket.STATUS_IN_PROGRESS,
    ]).count()
    threshold = 8
    if open_count > threshold:
        adjust_admin_stress(0.05)
    else:
        adjust_admin_stress(-0.02)


def record_report_feedback(ticket: ModerationTicket, *, actor: Agent | None, resolved: bool, note: str = "") -> None:
    reporter = ticket.reporter
    if resolved:
        adjust_frustration(reporter, -0.25)
        adjust_admin_stress(-0.03)
    else:
        adjust_frustration(reporter, 0.35)
        adjust_admin_stress(0.05)

    metadata = dict(ticket.metadata or {})
    feedback = list(metadata.get("report_feedback") or [])
    feedback.append({
        "ts": timezone.now().isoformat(),
        "actor": getattr(actor, "name", None),
        "result": "resolved" if resolved else "discarded",
        "note": note,
    })
    metadata["report_feedback"] = feedback[-20:]
    ticket.metadata = metadata
    safe_save(ticket, ["metadata", "updated_at"])
