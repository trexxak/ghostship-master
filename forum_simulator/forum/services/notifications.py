from __future__ import annotations

import re
from datetime import timedelta
from typing import Iterable, List

from django.urls import reverse
from django.utils import timezone

from forum.models import Agent, Post, PrivateMessage, ModerationEvent, AgentGoal


def _mention_regex(handle: str) -> re.Pattern[str]:
    escaped = re.escape(handle)
    return re.compile(rf"(?<![@\w])@{escaped}\b", re.IGNORECASE)


def _base_window() -> timezone.datetime:
    return timezone.now() - timedelta(days=7)


def collect(agent: Agent, *, since: timezone.datetime) -> List[dict[str, object]]:
    """
    Return recent notification payloads for the organic agent.

    Notifications include mentions, private messages, and role changes.
    """
    window_start = max(since, _base_window())
    mention_re = _mention_regex(agent.name)
    notifications: list[dict[str, object]] = []

    mention_lookup = f"@{agent.name}"
    posts = (
        Post.objects.select_related("thread", "author")
        .filter(
            created_at__gt=window_start,
            content__icontains=mention_lookup,
        )
        .order_by("-created_at")[:250]
    )
    for post in posts:
        if not post.thread_id:
            continue
        content = post.content or ""
        if "@" not in content:
            continue
        if not mention_re.search(content):
            continue
        actor = post.author.name if post.author else "A ghost"
        preview = " ".join((content or "").split())[:200]
        notifications.append(
            {
                "id": f"mention:{post.pk}",
                "type": "mention",
                "created": post.created_at,
                "actor": actor,
                "message": f"{actor} mentioned you in {post.thread.title}",
                "preview": preview,
                "url": f"{reverse('forum:thread_detail', args=[post.thread_id])}#post-{post.pk}",
            }
        )

    achievements = (
        AgentGoal.objects.select_related("goal")
        .filter(agent=agent, unlocked_at__gt=window_start)
        .order_by("-unlocked_at")[:30]
    )
    for award in achievements:
        goal = award.goal
        notifications.append(
            {
                "id": f"achievement:{award.pk}",
                "type": "achievement",
                "created": award.unlocked_at,
                "actor": goal.name if goal else "Achievement unlocked",
                "message": f"You unlocked {goal.name if goal else 'a new badge'}",
                "preview": goal.description if goal else "",
                "url": reverse("forum:oi_control_panel") + "#achievements",
            }
        )

    messages = (
        PrivateMessage.objects.select_related("sender")
        .filter(recipient=agent, sent_at__gt=window_start)
        .order_by("-sent_at")[:50]
    )
    for message in messages:
        actor = message.sender.name if message.sender else "Unknown ghost"
        dm_preview = " ".join((message.content or "").split())[:200]
        notifications.append(
            {
                "id": f"pm:{message.pk}",
                "type": "message",
                "created": message.sent_at,
                "actor": actor,
                "message": f"{actor} sent you a DM",
                "preview": dm_preview,
                "url": reverse("forum:oi_control_panel") + "#messages:inbox",
            }
        )

    role_events = (
        ModerationEvent.objects.select_related("actor")
        .filter(
            target_agent=agent,
            action_type__startswith="set-role",
            created_at__gt=window_start,
        )
        .order_by("-created_at")[:20]
    )
    for event in role_events:
        metadata = event.metadata or {}
        actor = event.actor.name if event.actor else "System"
        new_role = metadata.get("new_role") or event.action_type.split(":", 1)[-1]
        reason = metadata.get("reason") or event.reason or ""
        message = f"{actor} set your role to {new_role}"
        if reason:
            message = f"{message} — {reason}"
        notifications.append(
            {
                "id": f"role:{event.pk}",
                "type": "role",
                "created": event.created_at,
                "actor": actor,
                "message": message,
                "preview": "",
                "url": reverse("forum:oi_control_panel"),
            }
        )

    notifications.sort(key=lambda item: item["created"], reverse=True)
    for item in notifications:
        created = item["created"]
        if hasattr(created, "isoformat"):
            item["created"] = created.isoformat()
    return notifications[:60]


def latest_timestamp(payload: Iterable[dict[str, object]]) -> timezone.datetime | None:
    from django.utils.dateparse import parse_datetime

    latest: timezone.datetime | None = None
    for item in payload:
        created = item.get("created")
        if isinstance(created, str):
            dt = parse_datetime(created)
            if dt is None:
                continue
        elif isinstance(created, timezone.datetime):
            dt = created
        else:
            continue
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        if latest is None or dt > latest:
            latest = dt
    return latest
