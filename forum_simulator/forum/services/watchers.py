from __future__ import annotations

import time
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import OperationalError, transaction
from django.utils import timezone

from forum.models import Agent, Thread, ThreadWatch
from forum.services import configuration as config_service

_DEFAULT_WINDOW = getattr(settings, "THREAD_WATCH_WINDOW", 300)
_MAX_RETRIES = 3
_RETRY_DELAY = 0.05


def _active_window_seconds() -> int:
    return config_service.get_int("THREAD_WATCH_WINDOW", _DEFAULT_WINDOW)


def _get_session_key(request) -> str:
    session = getattr(request, "session", None)
    if session is None:
        raise RuntimeError("Session middleware required for watcher tracking")
    if not session.session_key:
        session.save()
    return session.session_key


def touch_thread_watch(request, thread: Thread, *, agent: Optional[Agent] = None) -> None:
    """Record that the current session is watching the given thread."""

    session_key = _get_session_key(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]
    defaults = {"agent": agent, "user_agent": user_agent}

    for attempt in range(_MAX_RETRIES):
        try:
            with transaction.atomic():
                ThreadWatch.objects.update_or_create(
                    thread=thread,
                    session_key=session_key,
                    defaults=defaults,
                )
            break
        except OperationalError:
            if attempt + 1 == _MAX_RETRIES:
                return
            time.sleep(_RETRY_DELAY * (attempt + 1))

    prune_stale_watches()
    _refresh_thread_cache(thread)


def clear_session_watches(session_key: str) -> None:
    affected_threads = list(ThreadWatch.objects.filter(
        session_key=session_key).values_list("thread_id", flat=True))
    ThreadWatch.objects.filter(session_key=session_key).delete()
    for thread_id in affected_threads:
        thread = Thread.objects.filter(pk=thread_id).first()
        if thread:
            _refresh_thread_cache(thread)


def prune_stale_watches() -> int:
    cutoff = timezone.now() - timedelta(seconds=_active_window_seconds())
    deleted, _ = ThreadWatch.objects.filter(last_seen__lt=cutoff).delete()
    return int(deleted)


def _refresh_thread_cache(thread: Thread) -> None:
    window = _active_window_seconds()
    cutoff = timezone.now() - timedelta(seconds=window)
    watches = ThreadWatch.objects.filter(
        thread=thread, last_seen__gte=cutoff).select_related("agent")
    watch_records = list(watches)
    agent_map: dict[str, dict[str, object]] = {}
    for watch in watch_records:
        if watch.agent_id and watch.agent:
            name = watch.agent.name
            if name not in agent_map:
                agent_map[name] = {
                    "name": name,
                    "role": watch.agent.role,
                    "is_organic": watch.agent.role == Agent.ROLE_ORGANIC,
                }
    guests = sum(1 for watch in watch_records if not watch.agent_id)
    agents_detail = sorted(agent_map.values(), key=lambda item: item["name"].lower())
    agent_names = [detail["name"] for detail in agents_detail]
    now = timezone.now()
    thread.watchers = {
        "agents": agent_names,
        "agent_details": agents_detail,
        "guests": guests,
        "total": guests + len(agent_names),
        "updated_at": now.isoformat(),
        "window": window,
    }
    thread.save(update_fields=["watchers"])
    # If a ghost (agent) is watching a fresh thread with zero replies for longer than window*0.5,
    # open a lightweight moderation ticket suggesting follow-up/duplicate check.
    try:
        from forum.models import ModerationTicket, Post
        now = timezone.now()
        # fresh threshold: created within window seconds
        fresh_cutoff = thread.created_at is not None and (
            now - thread.created_at).total_seconds() <= (window * 1.5)
        if fresh_cutoff:
            post_count = Post.objects.filter(thread=thread).count()
            if post_count == 0 and (len(agents) + guests) > 0:
                # if there are agent watchers but no replies, create a ticket for moderators
                ModerationTicket.objects.create(
                    title=f"Needs follow-up or duplicate? {thread.title}",
                    description=(f"Thread '{thread.title}' has {len(agents)} agent watchers and {guests} guest watchers but no replies."
                                 " Consider checking for duplicates or seeding the thread."),
                    reporter=None,
                    reporter_name="system",
                    thread=thread,
                    source=ModerationTicket.SOURCE_SYSTEM,
                    status=ModerationTicket.STATUS_OPEN,
                    priority=ModerationTicket.PRIORITY_LOW,
                    tags=["auto-followup"],
                    metadata={"watchers": {"agents": agents,
                                           "guests": guests}, "tick_window": window},
                )
    except Exception:
        # don't fail the watcher refresh if ticketing isn't available
        pass
