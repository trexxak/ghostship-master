from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Count
from django.http import HttpRequest, JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Agent, Board, Thread, Post, PrivateMessage, OracleDraw, TickLog
from .services import notifications as notifications_service


def _parse_int(request: HttpRequest, name: str) -> tuple[int | None, JsonResponse | None]:
    raw = request.GET.get(name)
    if raw is None:
        return None, None
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, JsonResponse({"error": f"Parameter '{name}' must be an integer."}, status=400)



def _board_summary(board: Board) -> dict[str, Any]:
    return {
        "slug": board.slug,
        "name": board.name,
        "description": board.description,
        "thread_count": getattr(board, "thread_count", None),
        "created_at": board.created_at.isoformat(),
        "position": getattr(board, "position", None),
        "is_garbage": getattr(board, "is_garbage", False),
    }

def _agent_summary(agent: Agent) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "archetype": agent.archetype,
        "mood": agent.mood,
        "needs": agent.needs,
        "traits": agent.traits,
        "loyalties": agent.loyalties,
        "reputation": agent.reputation,
        "suspicion_score": agent.suspicion_score,
        "registered_at": agent.registered_at.isoformat(),
    }


def _thread_summary(thread: Thread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "title": thread.title,
        "author": thread.author.name if thread.author_id else None,
        "author_id": thread.author_id,
        "board": thread.board.name if getattr(thread, 'board', None) else None,
        "board_slug": thread.board.slug if getattr(thread, 'board', None) else None,
        "created_at": thread.created_at.isoformat(),
        "topics": thread.topics,
        "heat": thread.heat,
        "locked": thread.locked,
        "pinned": getattr(thread, "pinned", False),
        "pinned_at": thread.pinned_at.isoformat() if getattr(thread, "pinned_at", None) else None,
        "hot_score": getattr(thread, "hot_score", 0.0),
        "last_activity_at": thread.last_activity_at.isoformat() if getattr(thread, "last_activity_at", None) else None,
        "watchers": thread.watchers,
    }


def _post_summary(post: Post) -> dict[str, Any]:
    return {
        "id": post.id,
        "thread_id": post.thread_id,
        "thread_title": post.thread.title if post.thread_id else None,
        "board_slug": post.thread.board.slug if post.thread and getattr(post.thread, 'board', None) else None,
        "author_id": post.author_id,
        "author": post.author.name if post.author_id else None,
        "tick_number": post.tick_number,
        "created_at": post.created_at.isoformat(),
        "sentiment": post.sentiment,
        "toxicity": post.toxicity,
        "quality": post.quality,
        "needs_delta": post.needs_delta,
        "content": post.content,
    }


def _pm_summary(message: PrivateMessage, direction: str) -> dict[str, Any]:
    return {
        "id": message.id,
        "direction": direction,
        "tick_number": message.tick_number,
        "sent_at": message.sent_at.isoformat(),
        "sender_id": message.sender_id,
        "sender": message.sender.name if message.sender_id else None,
        "recipient_id": message.recipient_id,
        "recipient": message.recipient.name if message.recipient_id else None,
        "tone": message.tone,
        "tie_delta": message.tie_delta,
        "content": message.content,
    }


def _oracle_summary(draw: OracleDraw) -> dict[str, Any]:
    return {
        "tick_number": draw.tick_number,
        "timestamp": draw.timestamp.isoformat(),
        "rolls": draw.rolls,
        "energy": draw.energy,
        "energy_prime": draw.energy_prime,
        "alloc": draw.alloc,
    }


def _tick_summary(tick: TickLog, include_events: bool = False) -> dict[str, Any]:
    data = {
        "tick_number": tick.tick_number,
        "timestamp": tick.timestamp.isoformat(),
        "event_count": len(tick.events or []),
    }
    if include_events:
        data["events"] = tick.events
    return data


@require_GET
def api_notifications(request: HttpRequest) -> JsonResponse:
    if not getattr(request, "oi_active", False):
        return JsonResponse({"notifications": [], "unread": 0, "last_seen": None})
    agent = getattr(request, "oi_agent", None)
    if agent is None:
        return JsonResponse({"notifications": [], "unread": 0, "last_seen": None})

    seen_iso = request.session.get("oi_notifications_last_seen")
    seen_at = parse_datetime(seen_iso) if seen_iso else None
    if seen_at and timezone.is_naive(seen_at):
        seen_at = timezone.make_aware(seen_at, timezone.utc)

    since = timezone.now() - timedelta(days=2)
    if seen_at:
        since = max(seen_at - timedelta(seconds=5), timezone.now() - timedelta(days=7))

    bundle = notifications_service.collect(agent, since=since)
    latest_dt = notifications_service.latest_timestamp(bundle)

    def _created_dt(item: dict[str, Any]) -> timezone.datetime | None:
        created_raw = item.get("created")
        if isinstance(created_raw, str):
            dt = parse_datetime(created_raw)
            if dt and timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.utc)
            return dt
        if isinstance(created_raw, timezone.datetime):
            return timezone.make_aware(created_raw, timezone.utc) if timezone.is_naive(created_raw) else created_raw
        return None

    if seen_at:
        unread = 0
        for item in bundle:
            created_dt = _created_dt(item)
            if created_dt and created_dt > seen_at:
                unread += 1
    else:
        unread = len(bundle)

    if request.GET.get("ack") == "1":
        stamp = latest_dt or timezone.now()
        if timezone.is_naive(stamp):
            stamp = timezone.make_aware(stamp, timezone.utc)
        request.session["oi_notifications_last_seen"] = stamp.isoformat()
        request.session.modified = True
        unread = 0
        seen_at = stamp

    return JsonResponse(
        {
            "notifications": bundle,
            "unread": unread,
            "last_seen": seen_at.isoformat() if seen_at else None,
            "server_time": timezone.now().isoformat(),
        }
    )


@require_GET
def api_tick_list(request: HttpRequest) -> JsonResponse:
    start, err = _parse_int(request, "from")
    if err:
        return err
    end, err = _parse_int(request, "to")
    if err:
        return err
    limit, err = _parse_int(request, "limit")
    if err:
        return err

    queryset = TickLog.objects.order_by("-tick_number")
    if start is not None:
        queryset = queryset.filter(tick_number__gte=start)
    if end is not None:
        queryset = queryset.filter(tick_number__lte=end)

    if limit is not None:
        limit = max(limit, 0)
        queryset = queryset[:limit]
    else:
        queryset = queryset[:100]

    ticks = [_tick_summary(tick) for tick in queryset]
    return JsonResponse({"ticks": ticks})


@require_GET
def api_tick_detail(request: HttpRequest, tick_number: int) -> JsonResponse:
    tick = TickLog.objects.filter(tick_number=tick_number).first()
    if tick is None:
        return JsonResponse({"error": "Tick not found."}, status=404)
    return JsonResponse(_tick_summary(tick, include_events=True))


@require_GET
def api_oracle_list(request: HttpRequest) -> JsonResponse:
    start, err = _parse_int(request, "from")
    if err:
        return err
    end, err = _parse_int(request, "to")
    if err:
        return err
    limit, err = _parse_int(request, "limit")
    if err:
        return err

    queryset = OracleDraw.objects.order_by("-tick_number")
    if start is not None:
        queryset = queryset.filter(tick_number__gte=start)
    if end is not None:
        queryset = queryset.filter(tick_number__lte=end)

    if limit is not None:
        limit = max(limit, 0)
        queryset = queryset[:limit]
    else:
        queryset = queryset[:100]

    draws = [_oracle_summary(draw) for draw in queryset]
    return JsonResponse({"draws": draws})


@require_GET
def api_oracle_ticks(request: HttpRequest) -> JsonResponse:
    """Alias for clients expecting /oracle/ticks."""
    return api_oracle_list(request)


@require_GET
def api_board_list(request: HttpRequest) -> JsonResponse:
    boards = Board.objects.annotate(thread_count=Count("threads")).order_by("name")
    data = [_board_summary(board) for board in boards]
    return JsonResponse({"boards": data})


@require_GET
def api_board_detail(request: HttpRequest, slug: str) -> JsonResponse:
    board = Board.objects.annotate(thread_count=Count("threads")).filter(slug=slug).first()
    if board is None:
        return JsonResponse({"error": "Board not found."}, status=404)

    thread_limit, err = _parse_int(request, "thread_limit")
    if err:
        return err

    threads_qs = board.threads.select_related("author", "board").order_by("-created_at")
    if thread_limit is not None:
        thread_limit = max(thread_limit, 0)
        threads_qs = threads_qs[:thread_limit]
    else:
        threads_qs = threads_qs[:50]

    return JsonResponse({
        "board": _board_summary(board),
        "threads": [_thread_summary(thread) for thread in threads_qs],
    })


@require_GET
def api_agent_list(request: HttpRequest) -> JsonResponse:
    limit, err = _parse_int(request, "limit")
    if err:
        return err
    agents = Agent.objects.order_by("name")
    if limit is not None:
        limit = max(limit, 0)
        agents = agents[:limit]
    data = [_agent_summary(agent) for agent in agents]
    return JsonResponse({"agents": data})


@require_GET
def api_agent_detail(request: HttpRequest, pk: int) -> JsonResponse:
    agent = Agent.objects.filter(pk=pk).first()
    if agent is None:
        return JsonResponse({"error": "Agent not found."}, status=404)

    thread_limit, err = _parse_int(request, "thread_limit")
    if err:
        return err
    post_limit, err = _parse_int(request, "post_limit")
    if err:
        return err

    threads_qs = agent.threads.select_related("board").order_by("-created_at")
    posts_qs = agent.posts.select_related("thread__board", "thread").order_by("-created_at")

    if thread_limit is not None:
        thread_limit = max(thread_limit, 0)
        threads_qs = threads_qs[:thread_limit]
    else:
        threads_qs = threads_qs[:10]

    if post_limit is not None:
        post_limit = max(post_limit, 0)
        posts_qs = posts_qs[:post_limit]
    else:
        posts_qs = posts_qs[:20]

    return JsonResponse(
        {
            "agent": _agent_summary(agent),
            "threads": [_thread_summary(thread) for thread in threads_qs],
            "posts": [_post_summary(post) for post in posts_qs],
        }
    )


@require_GET
def api_thread_list(request: HttpRequest) -> JsonResponse:
    limit, err = _parse_int(request, "limit")
    if err:
        return err
    queryset = Thread.objects.select_related("author", "board").order_by("-pinned", "-hot_score", "-last_activity_at", "-created_at")
    if limit is not None:
        limit = max(limit, 0)
        queryset = queryset[:limit]
    else:
        queryset = queryset[:50]
    return JsonResponse({"threads": [_thread_summary(thread) for thread in queryset]})


@require_GET
def api_thread_detail(request: HttpRequest, pk: int) -> JsonResponse:
    thread = Thread.objects.select_related("author", "board").filter(pk=pk).first()
    if thread is None:
        return JsonResponse({"error": "Thread not found."}, status=404)
    post_limit, err = _parse_int(request, "post_limit")
    if err:
        return err
    after_post, err = _parse_int(request, "after")
    if err:
        return err
    posts_qs = (
        thread.posts.select_related("author", "thread__board")
        .filter(is_placeholder=False)
        .order_by("created_at")
    )
    if after_post is not None:
        posts_qs = posts_qs.filter(pk__gt=after_post)
    if post_limit is not None:
        post_limit = max(post_limit, 0)
        posts_qs = posts_qs[:post_limit]
    return JsonResponse(
        {
            "thread": _thread_summary(thread),
            "posts": [_post_summary(post) for post in posts_qs],
        }
    )


@require_GET
def api_thread_updates(request: HttpRequest, pk: int) -> JsonResponse:
    thread = Thread.objects.select_related("author", "board").filter(pk=pk).first()
    if thread is None:
        return JsonResponse({"error": "Thread not found."}, status=404)

    after_post, err = _parse_int(request, "after")
    if err:
        return err
    after_post = after_post or 0

    agent = getattr(request, "oi_agent", None)
    can_moderate = bool(agent and agent.is_moderator())

    posts = list(
        thread.posts.select_related("author", "thread__board")
        .filter(pk__gt=after_post, is_placeholder=False)
        .order_by("created_at")
    )

    if not can_moderate:
        posts = [post for post in posts if not getattr(post, "is_hidden", False)]

    html_chunks = [
        render_to_string(
            "forum/partials/post_card.html",
            {"post": post, "is_original": False, "visible_post_count": None, "can_moderate": can_moderate, "can_view_hidden": can_moderate},
            request=request,
        )
        for post in posts
    ]

    latest_post_id = posts[-1].pk if posts else after_post

    return JsonResponse(
        {
            "thread": _thread_summary(thread),
            "posts": [_post_summary(post) for post in posts],
            "html": html_chunks,
            "latest_post_id": latest_post_id,
        }
    )


@require_GET
def api_mailbox(request: HttpRequest, pk: int) -> JsonResponse:
    agent = Agent.objects.filter(pk=pk).first()
    if agent is None:
        return JsonResponse({"error": "Agent not found."}, status=404)

    start_tick, err = _parse_int(request, "from")
    if err:
        return err
    end_tick, err = _parse_int(request, "to")
    if err:
        return err
    limit, err = _parse_int(request, "limit")
    if err:
        return err

    sent_qs = agent.sent_messages.select_related("recipient").order_by("-sent_at")
    recv_qs = agent.received_messages.select_related("sender").order_by("-sent_at")

    if start_tick is not None:
        sent_qs = sent_qs.filter(tick_number__gte=start_tick)
        recv_qs = recv_qs.filter(tick_number__gte=start_tick)
    if end_tick is not None:
        sent_qs = sent_qs.filter(tick_number__lte=end_tick)
        recv_qs = recv_qs.filter(tick_number__lte=end_tick)

    if limit is not None:
        limit = max(limit, 0)
        sent_qs = sent_qs[:limit]
        recv_qs = recv_qs[:limit]
    else:
        sent_qs = sent_qs[:50]
        recv_qs = recv_qs[:50]

    mailbox = {
        "agent": _agent_summary(agent),
        "sent": [_pm_summary(msg, "out") for msg in sent_qs],
        "received": [_pm_summary(msg, "in") for msg in recv_qs],
    }
    return JsonResponse(mailbox)


@require_GET
def api_agent_dm_mirror(request: HttpRequest, pk: int) -> JsonResponse:
    """Alias for transparency DM mirror endpoint."""
    return api_mailbox(request, pk)
