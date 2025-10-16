from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone

from forum.models import AgentGoal, Goal, TickLog

DEFAULT_MODE = "bulletin"
DEFAULT_THEME = "midnight"
STATIC_VERSION = getattr(settings, "STATIC_VERSION", timezone.now().strftime("%Y%m%d%H%M%S"))


def ui_mode(request: HttpRequest) -> dict[str, object]:
    as_organic = False
    if hasattr(request, "session"):
        if request.session.get("ui_mode") != DEFAULT_MODE:
            request.session["ui_mode"] = DEFAULT_MODE
            request.session.modified = True
        if request.session.get("ui_theme") != DEFAULT_THEME:
            request.session["ui_theme"] = DEFAULT_THEME
            request.session.modified = True
        as_organic = bool(request.session.get("act_as_oi"))

    latest_tick = (
        TickLog.objects.order_by("-tick_number")
        .values_list("tick_number", flat=True)
        .first()
    )

    return {
        "ui_mode": DEFAULT_MODE,
        "is_bulletin_mode": True,
        "acting_as_organic": as_organic,
        "ui_mode_toggle": None,
        "ui_theme": DEFAULT_THEME,
        "ui_theme_toggle": None,
        "latest_tick_number": latest_tick,
        "static_version": STATIC_VERSION,
    }


def progress_notifications(request: HttpRequest) -> dict[str, object]:
    if not hasattr(request, "session"):
        return {"progress_toasts": [], "progress_ticker": []}
    session = request.session
    session_key = session.session_key
    if not session_key:
        session.save()
        session_key = session.session_key

    now = timezone.now()
    toast_seen_ids = set(session.get("progress_toasts_seen", []))
    toasts: list[dict[str, object]] = []
    if session_key:
        progression_toasts = (
            AgentGoal.objects.filter(
                goal__goal_type=Goal.TYPE_PROGRESS,
                unlocked_at__isnull=False,
                metadata__trigger_session_key=session_key,
            )
            .select_related("goal")
            .order_by("-unlocked_at")[:3]
        )
        for record in progression_toasts:
            if record.id in toast_seen_ids:
                continue
            goal = record.goal
            toasts.append(
                {
                    "slug": goal.slug,
                    "name": goal.name,
                    "emoji": goal.emoji or goal.icon_slug or "🏆",
                    "unlocked_at": record.unlocked_at,
                    "post_id": record.metadata.get("post_id"),
                    "thread_id": record.metadata.get("thread_id"),
                }
            )
            toast_seen_ids.add(record.id)
    if toasts:
        session["progress_toasts_seen"] = list(toast_seen_ids)
        session.modified = True

    ticker_window = now - timedelta(minutes=30)
    ticker_seen = set(session.get("progress_ticker_seen", []))
    ticker_records = (
        AgentGoal.objects.filter(unlocked_at__gte=ticker_window)
        .select_related("goal", "agent")
        .order_by("-unlocked_at")[:12]
    )
    ticker: list[dict[str, object]] = []
    for record in ticker_records:
        trigger_key = record.metadata.get("trigger_session_key")
        if trigger_key and trigger_key == session_key:
            continue
        if record.id in ticker_seen:
            continue
        goal = record.goal
        ticker.append(
            {
                "slug": goal.slug,
                "name": goal.name,
                "emoji": goal.emoji or goal.icon_slug or "🌟",
                "agent": record.agent.name if record.agent else "unknown",
                "unlocked_at": record.unlocked_at,
                "thread_id": record.metadata.get("thread_id"),
                "post_id": record.metadata.get("post_id"),
            }
        )
        ticker_seen.add(record.id)
    if ticker:
        session["progress_ticker_seen"] = list(ticker_seen)
        session.modified = True
    return {
        "progress_toasts": toasts,
        "progress_ticker": ticker,
    }
