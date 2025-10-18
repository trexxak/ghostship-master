from __future__ import annotations
from datetime import timedelta
import math
import re

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import IntegrityError
from django.db.models import Prefetch, Count, Sum, OuterRef, Subquery, Q, Case, When, Value, IntegerField
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    PostReportForm,
    ModerationTicketActionForm,
    AdminSettingsForm,
    OrganicDraftForm,
    OrganicThreadReplyForm,
    BoardCreateForm,
)
from .lore import ORGANIC_HANDLE

from .models import (
    Agent,
    Board,
    Thread,
    Post,
    PrivateMessage,
    OracleDraw,
    TickLog,
    GenerationTask,
    ModerationTicket,
    ModerationEvent,
    ThreadWatch,
    SessionActivity,
    OpenRouterUsage,
    Goal,
    AgentGoal,
    GoalProgress,
    OrganicInteractionLog,
    GoalEvaluation,
)
from .services import configuration as config_service
from .services import moderation as moderation_service
from .services import watchers as watcher_service
from .services import events as events_service
from .services import missions as missions_service
from .services import progress as progress_service
from .services import unlockables as unlockable_service
from .templatetags.forum_extras import format_post


THREADS_PER_PAGE = 20
POSTS_PER_PAGE = 15
DASHBOARD_THREADS_PAGE_SIZE = 6
DASHBOARD_POSTS_PAGE_SIZE = 8


# -----------------------------------------------------------------------------
# Organic Interface control panel
#
# The control panel offers a personalized dashboard for the operator piloting
# trexxak. It surfaces a mailbox (inbox/outbox), unlocked achievements,
# predetermined avatar selection, and stubs for future settings and tutorials.
# The panel requires the organic interface to be active; otherwise access is
# denied.


def _available_avatars(agent: Agent | None = None) -> list[dict[str, str]]:
    """Return a list of avatar options with their persisted values and URLs."""
    return unlockable_service.avatar_option_catalog(agent)


def _oi_agent(request: HttpRequest) -> Agent | None:
    return getattr(request, "oi_agent", None)


def _require_oi_moderator(request: HttpRequest) -> Agent | None:
    agent = _oi_agent(request)
    if not getattr(request, "oi_active", False):
        return None
    if agent is None:
        return None
    if not agent.is_moderator():
        return None
    return agent


@require_http_methods(["GET", "POST"])
def create_board(request: HttpRequest, parent_slug: str | None = None) -> HttpResponse:
    agent = _oi_agent(request) or _organic_agent()
    if agent is None:
        return HttpResponseForbidden()
    is_admin = agent.is_admin()
    is_moderator = agent.is_moderator()
    parent: Board | None = None
    if parent_slug:
        parent = Board.objects.filter(slug=parent_slug).first()
        if parent is None and parent_slug.isdigit():
            parent = Board.objects.filter(pk=int(parent_slug)).first()
        if parent is None:
            raise Http404("Parent board not found")
    if not is_admin and not (parent and is_moderator):
        return HttpResponseForbidden()
    form = BoardCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        board = form.save(commit=False)
        board.parent = parent
        board.slug = _unique_board_slug(board.name, parent)
        try:
            board.save()
        except IntegrityError:
            form.add_error("name", "A board with a similar name already exists.")
        else:
            messages.success(request, "Board created successfully.")
            return redirect("forum:board_detail", board.slug)
    return render(request, "forum/create_board.html", {"form": form, "parent": parent})

@require_http_methods(["GET", "POST"])
def compose_dm(request, recipient_id):
    organism = _organic_agent()
    if organism is None or not getattr(request, "oi_active", False):
        messages.error(request, "Flip the organic switch on before piloting trexxak.")
        return redirect("forum:mission_board")

    recipient = get_object_or_404(Agent, pk=recipient_id)

    # Build post_data so hidden/locked fields are present during validation
    if request.method == "POST":
        post_data = request.POST.copy()
        post_data["mode"] = OrganicDraftForm.MODE_DM
        post_data["recipient"] = str(recipient.pk)
    else:
        post_data = None

    form = OrganicDraftForm(
        post_data,
        initial={"mode": OrganicDraftForm.MODE_DM, "recipient": recipient},
    )

    # Hide & relax unrelated fields
    for fname in ("mode", "thread", "board", "title"):
        form.fields[fname].widget = forms.HiddenInput()
        form.fields[fname].required = False

    form.fields["recipient"].widget = forms.HiddenInput()
    form.fields["recipient"].required = True
    form.fields["recipient"].initial = recipient

    form.fields["content"].required = True  # must stay visible and required

    if request.method == "POST":
        if form.is_valid():
            _create_operator_dm(
                request,
                recipient=recipient,
                content=form.cleaned_data["content"],
                extra_metadata={"mode": "manual_composer"},
            )
            messages.success(request, f"Whisper sent; {recipient.name} will feel the chime.")
            return redirect("forum:agent_detail", pk=recipient.pk)
        else:
            # Optional: surface why it failed while you’re iterating
            messages.error(request, f"Could not send DM: {form.errors.as_text()}")

    return render(
        request,
        "forum/oi_manual_entry.html",
        {
            "form": form,
            "organism": organism,
            "preview": None,
            "oi_session_key": request.session.get("oi_session_key", ""),
        },
    )
@require_http_methods(["GET", "POST"])
def oi_control_panel(request: HttpRequest) -> HttpResponse:
    """
    Render the organic user's control panel. This dashboard exposes a high‑level
    view of the organic interface's personal communications (inbox/outbox),
    unlocked achievements, avatar customization, and placeholder areas for
    settings and tutorials. Avatar changes are processed via POST.
    """
    organism = _organic_agent()
    if organism is None:
        raise Http404("trexxak interface unavailable")
    # Access restricted to when the organic interface is toggled on
    if not getattr(request, "oi_active", False):
        messages.error(
            request,
            "Flip the organic switch on before accessing the control panel.",
        )
        return redirect("forum:mission_board")

    avatar_options = _available_avatars(organism)
    compose_redirect = f"{reverse('forum:oi_control_panel')}#messages:compose"

    if request.method == "POST" and request.POST.get("compose_pm"):
        raw_recipients = (request.POST.get("to") or "").strip()
        body = (request.POST.get("body") or "").strip()
        subject = (request.POST.get("subject") or "").strip()

        handles = [
            handle
            for handle in re.split(r"[\s,]+", raw_recipients)
            if handle
        ]

        if not handles:
            messages.error(request, "Specify at least one recipient.")
            return redirect(compose_redirect)

        if not body:
            messages.error(request, "Message body cannot be empty.")
            return redirect(compose_redirect)

        recipients: list[Agent] = []
        seen_recipient_ids: set[int] = set()
        for handle in handles:
            agent = _resolve_agent_handle(handle)
            if agent is None:
                messages.error(request, f"No agent found matching '{handle}'.")
                return redirect(compose_redirect)
            if agent.role == Agent.ROLE_ORGANIC:
                messages.error(request, "Cannot direct message the organic operator.")
                return redirect(compose_redirect)
            if agent.pk in seen_recipient_ids:
                continue
            recipients.append(agent)
            seen_recipient_ids.add(agent.pk)

        metadata = {"origin": "control_panel"}
        if subject:
            metadata["subject"] = subject

        for recipient in recipients:
            _create_operator_dm(
                request,
                recipient=recipient,
                content=body,
                extra_metadata=metadata,
            )

        recipient_names = ", ".join(agent.name for agent in recipients)
        messages.success(request, f"Message dispatched to {recipient_names}.")
        return redirect(compose_redirect)

    allowed_avatar_values: set[str] = {
        str(entry.get("value"))
        for entry in avatar_options
        if entry.get("value")
    }
    default_avatar_value = next(
        (
            entry.get("value")
            for entry in avatar_options
            if entry.get("slot") == "default"
        ),
        unlockable_service.default_avatar_option().get("value") or "",
    )
    session_avatar_value = (
        str(request.session.get("oi_avatar_override", "")).strip()
        if hasattr(request, "session")
        else ""
    )

    # Process avatar selection via POST. Store preference per session rather than
    # mutating the trexxak agent record.
    if request.method == "POST":
        selected_slug = (request.POST.get("avatar_slug") or "").strip()
        if selected_slug:
            if selected_slug in allowed_avatar_values:
                if hasattr(request, "session"):
                    request.session["oi_avatar_override"] = selected_slug
                    request.session.modified = True
                messages.success(request, "Avatar updated for this session.")
            else:
                messages.error(request, "Invalid avatar selection.")
        else:
            if hasattr(request, "session"):
                request.session.pop("oi_avatar_override", None)
                request.session.modified = True
            messages.success(request, "Avatar reset to the default baseline.")
        return redirect("forum:oi_control_panel")

    selected_avatar_value = ""
    if session_avatar_value in allowed_avatar_values:
        selected_avatar_value = session_avatar_value
    elif default_avatar_value and default_avatar_value in allowed_avatar_values:
        selected_avatar_value = default_avatar_value
    elif organism.avatar_slug and str(organism.avatar_slug) in allowed_avatar_values:
        selected_avatar_value = str(organism.avatar_slug)
    elif avatar_options:
        selected_avatar_value = str(avatar_options[0].get("value") or "")
    if selected_avatar_value:
        organism.avatar_slug = selected_avatar_value

    # Collect direct message history and group it into conversation threads so the
    # inbox/outbox views can display the surrounding context for each partner.
    dm_queryset = (
        PrivateMessage.objects.filter(Q(sender=organism) | Q(recipient=organism))
        .select_related("sender", "recipient")
        .order_by("-sent_at")
    )

    threads_by_partner: dict[int, dict[str, object]] = {}
    for message in dm_queryset:
        partner = message.recipient if message.sender_id == organism.id else message.sender
        if partner is None:
            continue
        direction = "outgoing" if message.sender_id == organism.id else "incoming"
        thread = threads_by_partner.get(partner.id)
        if thread is None:
            thread = {
                "partner": partner,
                "messages": [],
                "incoming_total": 0,
                "outgoing_total": 0,
                "last_sent_at": message.sent_at,
                "last_message": message,
                "last_direction": direction,
            }
            threads_by_partner[partner.id] = thread
        payload = {
            "message": message,
            "direction": direction,
        }
        thread_messages: list[dict[str, object]] = thread.setdefault("messages", [])  # type: ignore[assignment]
        thread_messages.append(payload)
        if direction == "incoming":
            thread["incoming_total"] = int(thread.get("incoming_total", 0)) + 1
        else:
            thread["outgoing_total"] = int(thread.get("outgoing_total", 0)) + 1
        last_sent_at = thread.get("last_sent_at")
        if last_sent_at is None or message.sent_at >= last_sent_at:  # type: ignore[operator]
            thread["last_sent_at"] = message.sent_at
            thread["last_message"] = message
            thread["last_direction"] = direction

    dm_threads: list[dict[str, object]] = []
    for thread in threads_by_partner.values():
        thread_messages = thread.get("messages", [])
        if isinstance(thread_messages, list):
            thread_messages.sort(key=lambda entry: entry["message"].sent_at)  # type: ignore[index]
        thread["message_count"] = len(thread_messages) if isinstance(thread_messages, list) else 0
        dm_threads.append(thread)

    dm_threads.sort(key=lambda entry: entry["last_sent_at"], reverse=True)

    inbox_threads_all = [thread for thread in dm_threads if int(thread.get("incoming_total", 0)) > 0]
    outbox_threads_all = [thread for thread in dm_threads if int(thread.get("outgoing_total", 0)) > 0]

    inbox_paginator = Paginator(inbox_threads_all, 10)
    outbox_paginator = Paginator(outbox_threads_all, 10)

    inbox_page_number = request.GET.get("inbox_page") or 1
    outbox_page_number = request.GET.get("outbox_page") or 1

    try:
        inbox_page_obj = inbox_paginator.page(inbox_page_number)
    except PageNotAnInteger:
        inbox_page_obj = inbox_paginator.page(1)
    except EmptyPage:
        inbox_page_obj = inbox_paginator.page(inbox_paginator.num_pages)

    try:
        outbox_page_obj = outbox_paginator.page(outbox_page_number)
    except PageNotAnInteger:
        outbox_page_obj = outbox_paginator.page(1)
    except EmptyPage:
        outbox_page_obj = outbox_paginator.page(outbox_paginator.num_pages)

    # Fetch all personal goal states for display in the achievements section.
    agent_goals = list(
        AgentGoal.objects.filter(agent=organism)
        .select_related("goal", "source_post")
        .order_by("goal__priority", "goal__name")
    )

    viewer_roles = _viewer_roles(request)
    can_moderate = _viewer_can_moderate(viewer_roles)
    is_admin = organism.is_admin() if hasattr(organism, "is_admin") else False

    moderator_ticket_queue: list[ModerationTicket] = []
    recent_reports: list[ModerationTicket] = []
    if can_moderate:
        active_statuses = [
            ModerationTicket.STATUS_OPEN,
            ModerationTicket.STATUS_TRIAGED,
            ModerationTicket.STATUS_IN_PROGRESS,
        ]
        moderator_ticket_queue = list(
            ModerationTicket.objects.filter(status__in=active_statuses)
            .select_related("thread", "post", "reporter", "assignee")
            .order_by("-priority", "opened_at")[:10]
        )
        recent_reports = [
            ticket
            for ticket in moderator_ticket_queue
            if ticket.source == ModerationTicket.SOURCE_REPORT
        ][:6]

    admin_metrics = {}
    admin_recent_usage: list[OpenRouterUsage] = []
    recent_goal_evaluations: list[GoalEvaluation] = []
    if is_admin:
        admin_metrics = {
            "agents": Agent.objects.count(),
            "threads": Thread.objects.count(),
            "posts": Post.objects.count(),
            "missions": Goal.objects.filter(goal_type=Goal.TYPE_MISSION).count(),
        }
        admin_recent_usage = list(OpenRouterUsage.objects.order_by("-day")[:8])
        recent_goal_evaluations = list(GoalEvaluation.objects.order_by("-created_at")[:5])

    def _query_without(param: str) -> str:
        params = request.GET.copy()
        if param in params:
            params.pop(param)
        return params.urlencode()

    context = {
        "organism": organism,
        "inbox": inbox_page_obj.object_list,
        "outbox": outbox_page_obj.object_list,
        "inbox_threads": inbox_page_obj.object_list,
        "outbox_threads": outbox_page_obj.object_list,
        "inbox_page_obj": inbox_page_obj,
        "outbox_page_obj": outbox_page_obj,
        "inbox_paginator": inbox_paginator,
        "outbox_paginator": outbox_paginator,
        "dm_thread_count": len(dm_threads),
        "dm_recipient_options": [
            {
                "id": agent.id,
                "name": agent.name,
                "archetype": agent.archetype,
                "role": agent.role,
            }
            for agent in Agent.objects.exclude(role=Agent.ROLE_ORGANIC).order_by("name")
        ],
        "agent_goals": agent_goals,
        "available_avatars": avatar_options,
        "selected_avatar": selected_avatar_value,
        "debug_role": (request.session.get("oi_debug_role") or ""),
        "can_moderate": can_moderate,
        "is_admin": is_admin,
        "moderator_ticket_queue": moderator_ticket_queue,
        "moderator_recent_reports": recent_reports,
        "ticket_action_form": ModerationTicketActionForm() if can_moderate else None,
        "admin_metrics": admin_metrics,
        "admin_recent_usage": admin_recent_usage,
        "recent_goal_evaluations": recent_goal_evaluations,
        "inbox_query_base": _query_without("inbox_page"),
        "outbox_query_base": _query_without("outbox_page"),
    }
    return render(request, "forum/oi_control_panel.html", context)


@require_http_methods(["POST"])
def oi_set_debug_role(request: HttpRequest) -> HttpResponse:
    next_url = request.POST.get("next") or reverse("forum:oi_control_panel")
    if not getattr(request, "oi_active", False):
        messages.error(request, "Activate trexxak mode before adjusting debug roles.")
        return redirect(next_url)
    role = (request.POST.get("role") or "").strip().lower()
    allowed = {"", Agent.ROLE_MEMBER, Agent.ROLE_MODERATOR, Agent.ROLE_ADMIN, Agent.ROLE_BANNED}
    if role not in allowed:
        messages.error(request, "Unsupported debug role selection.")
        return redirect(next_url)
    if not role:
        request.session.pop("oi_debug_role", None)
        messages.success(request, "trexxak debug role cleared.")
    else:
        request.session["oi_debug_role"] = role
        messages.success(request, f"trexxak debug role set to {role}.")
    request.session.modified = True
    return redirect(next_url)


def _resolve_agent_handle(handle: str | None) -> Agent | None:
    if not handle:
        return None
    return Agent.objects.filter(name__iexact=handle.strip()).first()


def _default_staff_actor() -> Agent | None:
    return (
        Agent.objects.filter(role=Agent.ROLE_ADMIN)
        .order_by("id")
        .first()
    )


def _organic_agent() -> Agent | None:
    return (
        Agent.objects.filter(role=Agent.ROLE_ORGANIC)
        .order_by("id")
        .first()
    )


def _normalized_roles(values: Iterable[str | None]) -> set[str]:
    return {str(value).strip().lower() for value in values if value}


def _viewer_roles(request: HttpRequest) -> set[str]:
    roles: set[str] = {"guest"}
    debug_role = str(request.session.get("oi_debug_role", "")) if hasattr(request, "session") else ""
    if debug_role:
        roles.add(debug_role)
    agent = getattr(request, "oi_agent", None)
    if agent and getattr(agent, "role", None):
        roles.add(agent.role)
    if agent and agent.is_admin():
        roles.add(Agent.ROLE_ADMIN)
    if agent and agent.is_moderator():
        roles.add(Agent.ROLE_MODERATOR)
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        if getattr(user, "is_superuser", False):
            roles.add(Agent.ROLE_ADMIN)
        if getattr(user, "is_staff", False):
            roles.add(Agent.ROLE_MODERATOR)
    return _normalized_roles(roles)


def _viewer_can_moderate(role_set: set[str]) -> bool:
    return any(role in role_set for role in {Agent.ROLE_ADMIN, Agent.ROLE_MODERATOR})


def _roles_open(required: Iterable[str], viewer_roles: set[str]) -> bool:
    required_set = _normalized_roles(required)
    if not required_set:
        return True
    return bool(required_set & viewer_roles)
def _log_organic_action(
    request: HttpRequest,
    *,
    action: str,
    thread: Thread | None = None,
    recipient: Agent | None = None,
    content: str | None = None,
    metadata: dict | None = None,
) -> None:
    agent = getattr(request, "oi_agent", None) or _organic_agent()
    if agent is None:
        return
    session_marker = getattr(request, "oi_session_key", None)
    if not session_marker and hasattr(request, "session"):
        session_marker = request.session.get("oi_session_key")
    combined_metadata = dict(metadata or {})
    if session_marker and "oi_session_key" not in combined_metadata:
        combined_metadata["oi_session_key"] = session_marker
    OrganicInteractionLog.record(
        agent=agent,
        action=action,
        request=request,
        thread=thread,
        recipient=recipient,
        content=content,
        metadata=combined_metadata,
    )


def _queue_metrics_delta(request: HttpRequest, **delta: int) -> None:
    if not hasattr(request, "session"):
        return
    session = request.session
    if session.session_key is None:
        session.save()
    stored = session.get("progress_metrics_delta")
    if not isinstance(stored, dict):
        stored = {}
    changed = False
    for key in ("threads", "replies", "reports"):
        value = delta.get(key)
        if not value:
            continue
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            continue
        if numeric == 0:
            continue
        stored[key] = int(stored.get(key, 0) or 0) + numeric
        changed = True
    if changed:
        session["progress_metrics_delta"] = stored
        session.modified = True


def _active_organic_agent(request: HttpRequest) -> Agent:
    agent = getattr(request, "oi_agent", None) or _organic_agent()
    if agent is None:
        raise Http404("trexxak interface unavailable")
    return agent


def _mark_editor_field(field: forms.Field | None) -> None:
    if field is None:
        return
    attrs = field.widget.attrs
    attrs.setdefault("data-editor-textarea", "true")
    attrs.setdefault("data-editor-source", "body")
    attrs.setdefault("data-editor-enabled", "true")


def _unique_board_slug(name: str, parent: Board | None = None) -> str:
    base = slugify(name) or "board"
    if parent and getattr(parent, "slug", ""):
        prefix = parent.slug
        if not base.startswith(prefix):
            base = f"{prefix}-{base}"
    base = base.strip("-") or "board"
    base = base[:140]
    slug = base
    counter = 2
    while Board.objects.filter(slug=slug).exists():
        suffix = f"-{counter}"
        trimmed_base = base[: max(1, 150 - len(suffix))]
        slug = f"{trimmed_base}{suffix}"
        counter += 1
    return slug


def _ensure_board_slug(board: Board) -> Board:
    if getattr(board, "slug", ""):
        return board
    parent = getattr(board, "parent", None)
    board.slug = _unique_board_slug(board.name, parent)
    board.save(update_fields=["slug"])
    return board


def _operator_session_key(request: HttpRequest) -> str:
    session_key = getattr(request, "oi_session_key", None)
    if session_key:
        return session_key
    if hasattr(request, "session"):
        return request.session.get("oi_session_key") or ""
    return ""


def _create_operator_post(
    request: HttpRequest,
    *,
    thread: Thread,
    content: str,
    extra_metadata: dict | None = None,
) -> Post:
    organism = _active_organic_agent(request)
    session_key = _operator_session_key(request)
    ip_address = _client_ip(request) or None
    post = Post.objects.create(
        thread=thread,
        author=organism,
        content=content,
        authored_by_operator=True,
        operator_session_key=session_key,
        operator_ip=ip_address,
    )
    thread.touch(activity=post.created_at, bump_heat=0.4)
    metadata = {"mode": "manual", "post_id": post.id}
    if extra_metadata:
        metadata.update(extra_metadata)
    _log_organic_action(
        request,
        action=OrganicInteractionLog.ACTION_POST,
        thread=thread,
        content=content,
        metadata=metadata,
    )
    _queue_metrics_delta(request, replies=1)
    return post


def _create_operator_dm(
    request: HttpRequest,
    *,
    recipient: Agent,
    content: str,
    extra_metadata: dict | None = None,
) -> PrivateMessage:
    organism = _active_organic_agent(request)
    session_key = _operator_session_key(request)
    ip_address = _client_ip(request) or None
    dm = PrivateMessage.objects.create(
        sender=organism,
        recipient=recipient,
        content=content,
        authored_by_operator=True,
        operator_session_key=session_key,
        operator_ip=ip_address,
    )
    metadata = {"mode": "manual", "dm_id": dm.id}
    if extra_metadata:
        metadata.update(extra_metadata)
    _log_organic_action(
        request,
        action=OrganicInteractionLog.ACTION_DM,
        recipient=recipient,
        content=content,
        metadata=metadata,
    )
    return dm


# NEW: helper to spin up a brand‑new thread as the organic operator. Creates the
# thread and seeds the first post, logging the action for audit. Note: the
# board must be provided, and a non‑empty title and body are required.
def _create_operator_thread(
    request: HttpRequest,
    *,
    board: Board,
    title: str,
    content: str,
    extra_metadata: dict | None = None,
) -> Thread:
    organism = _active_organic_agent(request)
    session_key = _operator_session_key(request)
    ip_address = _client_ip(request) or None
    # Create the thread first; last_activity_at will be bumped after the post
    thread = Thread.objects.create(
        title=title.strip(),
        author=organism,
        board=board,
    )
    # Seed the first post in the new thread
    post = Post.objects.create(
        thread=thread,
        author=organism,
        content=content,
        authored_by_operator=True,
        operator_session_key=session_key,
        operator_ip=ip_address,
    )
    # Update thread heat and last activity
    thread.touch(activity=post.created_at, bump_heat=0.4)
    metadata = {"mode": "manual", "thread_id": thread.id, "post_id": post.id}
    if extra_metadata:
        metadata.update(extra_metadata)
    _log_organic_action(
        request,
        action=OrganicInteractionLog.ACTION_POST,
        thread=thread,
        content=content,
        metadata=metadata,
    )
    _queue_metrics_delta(request, threads=1, replies=1)
    return thread


def _client_ip(request: HttpRequest) -> str:
    meta = getattr(request, "META", {}) or {}
    forwarded = (meta.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return meta.get("REMOTE_ADDR", "")


def _ensure_archive_board() -> Board:
    board, _ = Board.objects.get_or_create(
        slug="liminal-space",
        defaults={
            "name": "Liminal Space Archive",
            "description": "Holding area for threads awaiting reassignment.",
            "position": 900,
            "is_garbage": True,
        },
    )
    return board


def dashboard(request: HttpRequest) -> HttpResponse:
    pinned_threads = list(
        Thread.objects.filter(pinned=True)
        .select_related("author", "board")
        .order_by("-pinned_at", "-last_activity_at")[:5]
    )
    raw_bubbling_threads = list(
        Thread.objects.select_related("author", "board")
        .annotate(post_count=Count("posts"))
        .order_by("-pinned", "-hot_score", "-last_activity_at", "-created_at")[:60]
    )
    raw_latest_posts = list(
        Post.objects.select_related("author", "thread__board", "thread")
        .order_by("-created_at")[:60]
    )
    oracle_draws = OracleDraw.objects.order_by("-tick_number")[:8]
    tick_events = TickLog.objects.order_by("-tick_number")[:5]
    active_ticket_statuses = [
        ModerationTicket.STATUS_OPEN,
        ModerationTicket.STATUS_TRIAGED,
        ModerationTicket.STATUS_IN_PROGRESS,
    ]
    open_tickets = (
        ModerationTicket.objects.filter(status__in=active_ticket_statuses)
        .select_related("thread", "post", "reporter", "assignee")
        .order_by("-priority", "opened_at")[:8]
    )
    recent_mod_events = (
        ModerationEvent.objects.select_related(
            "actor", "target_thread", "target_agent", "ticket")
        .order_by("-created_at")[:8]
    )
    board_directory = list(Board.objects.annotate(
        thread_count=Count("threads")).order_by("position", "name")[:8])
    viewer_roles = _viewer_roles(request)
    can_moderate = _viewer_can_moderate(viewer_roles)
    pinned_threads = [thread for thread in pinned_threads if (
        _roles_open(getattr(thread, "visibility_roles", []) or getattr(thread.board, "visibility_roles", []) or [], viewer_roles) or can_moderate
    ) and (not getattr(thread, "is_hidden", False) or can_moderate)]
    bubbling_threads_filtered = [thread for thread in raw_bubbling_threads if (
        _roles_open(getattr(thread, "visibility_roles", []) or getattr(thread.board, "visibility_roles", []) or [], viewer_roles) or can_moderate
    ) and (not getattr(thread, "is_hidden", False) or can_moderate)]
    latest_posts_filtered = [post for post in raw_latest_posts if (not getattr(post, "is_hidden", False) or can_moderate)]

    threads_paginator = Paginator(bubbling_threads_filtered, DASHBOARD_THREADS_PAGE_SIZE) if bubbling_threads_filtered else None
    threads_page_obj = None
    bubbling_threads: list[Thread] = []
    thread_page_number = request.GET.get("threads_page") or 1
    if threads_paginator:
        try:
            threads_page_obj = threads_paginator.page(thread_page_number)
        except PageNotAnInteger:
            threads_page_obj = threads_paginator.page(1)
        except EmptyPage:
            threads_page_obj = threads_paginator.page(threads_paginator.num_pages)
        bubbling_threads = list(threads_page_obj.object_list)

    posts_paginator = Paginator(latest_posts_filtered, DASHBOARD_POSTS_PAGE_SIZE) if latest_posts_filtered else None
    posts_page_obj = None
    latest_posts: list[Post] = []
    posts_page_number = request.GET.get("posts_page") or 1
    if posts_paginator:
        try:
            posts_page_obj = posts_paginator.page(posts_page_number)
        except PageNotAnInteger:
            posts_page_obj = posts_paginator.page(1)
        except EmptyPage:
            posts_page_obj = posts_paginator.page(posts_paginator.num_pages)
        latest_posts = list(posts_page_obj.object_list)

    board_directory = [board for board in board_directory if (
        _roles_open(getattr(board, "visibility_roles", []) or [], viewer_roles) or can_moderate
    ) and (not getattr(board, "is_hidden", False) or can_moderate)]
    supernatural_events = events_service.recent_supernatural_events(6)
    banner = events_service.banner_payload(supernatural_events)
    unlocked_stickers: list[dict[str, object]] = []
    for mission in Goal.objects.filter(goal_type=Goal.TYPE_MISSION, metadata__reward_unlocked=True):
        sticker = (mission.metadata or {}).get("reward_sticker")
        if sticker:
            asset_url = unlockable_service.sticker_asset_url(sticker)
            unlocked_stickers.append(
                {
                    "slug": sticker,
                    "label": (mission.metadata or {}).get("reward_label", mission.name),
                    "url": asset_url,
                }
            )
    context = {
        "latest_threads": bubbling_threads,
        "bubbling_threads": bubbling_threads,
        "pinned_threads": pinned_threads,
        "latest_posts": latest_posts,
        "oracle_draws": oracle_draws,
        "tick_events": tick_events,
        "open_tickets": open_tickets,
        "recent_mod_events": recent_mod_events,
        "board_directory": board_directory,
        "supernatural_events": supernatural_events,
        "supernatural_banner": banner,
        "unlocked_stickers": unlocked_stickers,
        "threads_page_obj": threads_page_obj,
        "threads_paginator": threads_paginator,
        "posts_page_obj": posts_page_obj,
        "posts_paginator": posts_paginator,
    }
    return render(request, "forum/dashboard.html", context)


@require_http_methods(["GET", "POST"])
def thread_detail(request: HttpRequest, pk: int) -> HttpResponse:
    thread = get_object_or_404(
        Thread.objects.select_related("author", "board"),
        pk=pk,
    )
    viewer_roles = _viewer_roles(request)
    can_moderate = _viewer_can_moderate(viewer_roles)
    required_roles = getattr(thread, "visibility_roles", []) or getattr(thread.board, "visibility_roles", []) or []
    thread_hidden = bool(getattr(thread, "is_hidden", False) or getattr(thread.board, "is_hidden", False))
    allowed = _roles_open(required_roles, viewer_roles)
    if (thread_hidden or not allowed) and not can_moderate:
        raise Http404("Thread not found")
    thread.viewer_hidden = thread_hidden
    thread.viewer_restricted = not allowed
    thread.viewer_accessible = allowed or can_moderate
    watcher_service.touch_thread_watch(request, thread)
    moderation_events = thread.moderation_events.select_related(
        "actor", "ticket").order_by("-created_at")[:8]
    organic_prompts = list(
        thread.organic_logs.filter(
            action=OrganicInteractionLog.ACTION_AUTOMATION_BLOCKED)
        .order_by("-created_at")[:5]
    )

    reply_form: OrganicThreadReplyForm | None = None
    quote_post: Post | None = None

    posts_qs = (
        thread.posts.filter(is_placeholder=False)
        .select_related("author")
        .prefetch_related(
            Prefetch(
                "moderation_events",
                queryset=ModerationEvent.objects.select_related("actor").order_by("-created_at"),
            )
        )
        .order_by("created_at")
    )
    if not can_moderate:
        posts_qs = posts_qs.filter(is_hidden=False)

    total_posts = posts_qs.count()
    post_paginator: Paginator | None = None
    post_page_obj = None
    page_posts: list[Post] = []

    if total_posts:
        post_paginator = Paginator(posts_qs, POSTS_PER_PAGE)
        post_paginator._count = total_posts  # type: ignore[attr-defined]
        page_total = max(math.ceil(total_posts / POSTS_PER_PAGE), 1)
        requested_page = request.GET.get("page")
        if requested_page in (None, "", "last"):
            page_number = page_total
        else:
            try:
                page_number = int(requested_page)
            except (TypeError, ValueError):
                page_number = 1
            else:
                page_number = max(1, min(page_total, page_number))
        post_page_obj = post_paginator.get_page(page_number)
        page_posts = list(post_page_obj.object_list)

    visible_post_count = total_posts
    op_post = posts_qs.first() if visible_post_count else None
    latest_post_global = posts_qs.order_by("-created_at").first() if visible_post_count else None

    def _decorate(post: Post | None) -> None:
        if post is None:
            return
        post.viewer_hidden = bool(getattr(post, "is_hidden", False))

    _decorate(op_post)
    for post in page_posts:
        _decorate(post)

    show_full_op = bool(post_page_obj and post_page_obj.number == 1)
    first_post = op_post if show_full_op else None
    if show_full_op and op_post:
        remaining_posts = [post for post in page_posts if post.pk != op_post.pk]
    else:
        remaining_posts = page_posts
    last_post_page = page_posts[-1] if page_posts else None

    if post_paginator and post_paginator.count:
        start_page = max((post_page_obj.number if post_page_obj else 1) - 2, 1)
        end_page = min((post_page_obj.number if post_page_obj else 1) + 2, post_paginator.num_pages)
        page_window = list(range(start_page, end_page + 1))
    else:
        page_window: list[int] = []

    if request.method == "POST":
        if not getattr(request, "oi_active", False):
            messages.error(
                request, "Switch into trexxak mode before posting a reply.")
            return redirect("forum:thread_detail", pk=thread.pk)
        reply_form = OrganicThreadReplyForm(request.POST)
        if reply_form.is_valid():
            content = reply_form.cleaned_data["content"]
            metadata = {"origin": "thread_detail"}
            quote_post_id = reply_form.cleaned_data.get("quote_post_id")
            if quote_post_id:
                metadata["quoted_post_id"] = quote_post_id
            post = _create_operator_post(
                request,
                thread=thread,
                content=content,
                extra_metadata=metadata,
            )
            messages.success(request, "Reply posted as trexxak.")
            anchor = f"{reverse('forum:thread_detail', args=[thread.pk])}#post-{post.pk}"
            return redirect(anchor)
    elif getattr(request, "oi_active", False):
        initial: dict[str, object] = {}
        quote_id = request.GET.get("quote")
        if quote_id:
            try:
                quote_post = posts_qs.filter(pk=int(quote_id)).first()
            except (Post.DoesNotExist, ValueError, TypeError):
                quote_post = None
        if quote_post and getattr(quote_post, "is_hidden", False) and not can_moderate:
            quote_post = None
        if quote_post:
            quoted_lines = [f"> {line}" for line in (
                quote_post.content or "").splitlines()]
            quoted_block = "\n".join(quoted_lines).strip()
            mention = f"@{quote_post.author.name}" if getattr(
                quote_post, "author", None) and quote_post.author.name else ""
            pieces = [part for part in [quoted_block, mention] if part]
            prefill = "\n\n".join(pieces).strip()
            if prefill:
                prefill = f"{prefill}\n\n"
            initial["content"] = prefill
            initial["quote_post_id"] = quote_post.pk
        reply_form = OrganicThreadReplyForm(initial=initial or None)

    if reply_form is None and getattr(request, "oi_active", False):
        reply_form = OrganicThreadReplyForm()

    if reply_form is not None:
        content_field = reply_form.fields.get("content")
        _mark_editor_field(content_field)

    context = {
        "thread": thread,
        "moderation_events": moderation_events,
        "organic_prompts": organic_prompts,
        "reply_form": reply_form,
        "quote_post": quote_post,
        "post_list": page_posts,
        "first_post": first_post,
        "remaining_posts": remaining_posts,
        "last_post": latest_post_global,
        "page_last_post": last_post_page,
        "visible_post_count": visible_post_count,
        "can_view_hidden": can_moderate,
        "can_moderate": can_moderate,
        "post_page_obj": post_page_obj,
        "post_paginator": post_paginator,
        "posts_per_page": POSTS_PER_PAGE,
        "page_window": page_window,
        "op_post": op_post,
        "show_full_op": show_full_op,
    }
    return render(request, "forum/thread_detail.html", context)


@require_POST
def preview_post(request: HttpRequest) -> JsonResponse:
    content = (request.POST.get("content") or "").strip()
    if not content:
        return JsonResponse({"html": "", "error": "Write something to preview."}, status=400)
    rendered = str(format_post(content))
    return JsonResponse({"html": rendered})


def agent_list(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()
    sort_key = (request.GET.get("sort") or "name").lower()

    agents_qs = Agent.objects.all()
    if query:
        agents_qs = agents_qs.filter(Q(name__icontains=query) | Q(archetype__icontains=query))

    if sort_key == "status":
        status_order = Case(
            When(online_status=Agent.STATUS_ONLINE, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
        agents_qs = agents_qs.annotate(_status_order=status_order).order_by("_status_order", "-last_seen_at", "name")
    elif sort_key == "registered":
        agents_qs = agents_qs.order_by("-registered_at")
    elif sort_key == "suspicion":
        agents_qs = agents_qs.order_by("-suspicion_score", "name")
    elif sort_key == "archetype":
        agents_qs = agents_qs.order_by("archetype", "name")
    else:
        sort_key = "name"
        agents_qs = agents_qs.order_by("name")

    paginator = Paginator(agents_qs, 40)
    page_number = request.GET.get("page") or 1
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    params = request.GET.copy()
    params.pop("page", None)
    query_base = params.urlencode()

    context = {
        "agents": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": paginator,
        "search_query": query,
        "query_base": query_base,
        "current_sort": sort_key,
        "sort_options": [
            ("name", "Name"),
            ("status", "Status"),
            ("registered", "Newest"),
            ("archetype", "Archetype"),
            ("suspicion", "Suspicion"),
        ],
    }
    return render(request, "forum/agent_list.html", context)


def agent_detail(request: HttpRequest, pk: int) -> HttpResponse:
    agent = get_object_or_404(Agent, pk=pk)
    threads = list(agent.threads.select_related(
        "board").order_by("-created_at")[:10])
    posts = list(agent.posts.select_related(
        "thread__board", "thread").order_by("-created_at")[:20])
    sent_messages = agent.sent_messages.select_related(
        "recipient").order_by("-sent_at")[:20]
    received_messages = agent.received_messages.select_related(
        "sender").order_by("-sent_at")[:20]
    viewer_roles = _viewer_roles(request)
    can_moderate = _viewer_can_moderate(viewer_roles)
    threads = [thread for thread in threads if (
        _roles_open(getattr(thread, "visibility_roles", []) or getattr(thread.board, "visibility_roles", []) or [], viewer_roles) or can_moderate
    ) and (not getattr(thread, "is_hidden", False) or can_moderate)]
    posts = [post for post in posts if (not getattr(post, "is_hidden", False) or can_moderate)]
    moderation_events = agent.moderation_events.order_by("-created_at")[:10]
    moderated_actions = agent.moderated_actions.order_by("-created_at")[:10]
    context = {
        "agent": agent,
        "threads": threads,
        "posts": posts,
        "sent_messages": sent_messages,
        "received_messages": received_messages,
        "moderation_events": moderation_events,
        "moderated_actions": moderated_actions,
    }
    return render(request, "forum/agent_detail.html", context)


def board_list(request: HttpRequest) -> HttpResponse:
    board_queryset = (
        Board.objects.select_related("parent")
        .prefetch_related("moderators")
        .annotate(
            thread_count=Count("threads", distinct=True),
            post_count=Count("threads__posts", distinct=True),
            latest_post_id=Subquery(
                Post.objects.filter(thread__board=OuterRef("pk"))
                .order_by("-created_at")
                .values("pk")[:1]
            ),
        )
        .order_by("position", "name")
    )
    boards = list(board_queryset)
    viewer_roles = _viewer_roles(request)
    can_moderate = _viewer_can_moderate(viewer_roles)
    organism = _oi_agent(request)
    organism_is_admin = organism.is_admin() if organism else False

    filtered_boards: list[Board] = []
    for board in boards:
        _ensure_board_slug(board)
        required_roles = getattr(board, "visibility_roles", []) or []
        allowed = _roles_open(required_roles, viewer_roles)
        hidden = bool(getattr(board, "is_hidden", False))
        if (hidden or not allowed) and not can_moderate:
            continue
        board.viewer_hidden = hidden
        board.viewer_restricted = not allowed
        board.viewer_accessible = allowed or can_moderate
        filtered_boards.append(board)
    boards = filtered_boards

    latest_post_ids = [board.latest_post_id for board in boards if getattr(
        board, "latest_post_id", None)]
    latest_posts = {
        post.pk: post
        for post in Post.objects.filter(pk__in=latest_post_ids).select_related("author", "thread__board")
    }
    for board in boards:
        board.latest_post = latest_posts.get(
            getattr(board, "latest_post_id", None))

    active_boards = [board for board in boards if not getattr(
        board, "is_garbage", False)]
    archive_boards = [board for board in boards if getattr(
        board, "is_garbage", False)]

    children_map: dict[int, list[Board]] = {}
    for board in active_boards:
        if board.parent_id:
            children_map.setdefault(board.parent_id, []).append(board)
    for child_list in children_map.values():
        child_list.sort(key=lambda item: (item.position, item.name.lower()))

    board_groups: list[dict[str, object]] = []
    used_child_ids: set[int] = set()
    for board in active_boards:
        if board.parent_id:
            continue
        children = children_map.get(board.id, [])
        board_groups.append({"board": board, "children": children})
        used_child_ids.update(child.id for child in children)

    standalone_boards = [
        board for board in active_boards if board.id not in used_child_ids and board.parent_id]

    context = {
        "boards": boards,
        "board_groups": board_groups,
        "standalone_boards": standalone_boards,
        "archive_boards": archive_boards,
        "can_view_hidden": can_moderate,
        "can_moderate": can_moderate,
        "organism": organism,
        "is_admin": organism_is_admin,
    }
    return render(request, "forum/board_list.html", context)


def board_detail(request: HttpRequest, slug: str) -> HttpResponse:
    board = get_object_or_404(
        Board.objects.prefetch_related("moderators"), slug=slug)
    viewer_roles = _viewer_roles(request)
    can_moderate = _viewer_can_moderate(viewer_roles)
    allowed = _roles_open(getattr(board, "visibility_roles", []) or [], viewer_roles)
    hidden = bool(getattr(board, "is_hidden", False))
    if (hidden or not allowed) and not can_moderate:
        raise Http404("Board not found")
    board.viewer_hidden = hidden
    board.viewer_restricted = not allowed
    board.viewer_accessible = allowed or can_moderate
    threads_qs = (
        board.threads.select_related("author", "board")
        .annotate(
            post_count=Count("posts"),
            latest_post_id=Subquery(
                Post.objects.filter(thread=OuterRef("pk"))
                .order_by("-created_at")
                .values("pk")[:1]
            ),
        )
        .order_by("-pinned", "-hot_score", "-last_activity_at", "-created_at")
    )
    if not can_moderate:
        threads_qs = threads_qs.filter(is_hidden=False)

    thread_candidates = list(threads_qs[:200])
    accessible_threads: list[Thread] = []
    for thread in thread_candidates:
        required_roles = getattr(thread, "visibility_roles", []) or getattr(board, "visibility_roles", []) or []
        thread_allowed = _roles_open(required_roles, viewer_roles)
        thread_hidden = bool(getattr(thread, "is_hidden", False))
        if (thread_hidden or not thread_allowed) and not can_moderate:
            continue
        thread.viewer_hidden = thread_hidden
        thread.viewer_restricted = not thread_allowed
        thread.viewer_accessible = thread_allowed or can_moderate
        accessible_threads.append(thread)

    pinned_threads = [thread for thread in accessible_threads if getattr(thread, "pinned", False)]
    regular_threads_all = [thread for thread in accessible_threads if not getattr(thread, "pinned", False)]

    thread_paginator: Paginator | None
    thread_page_obj = None
    regular_threads_page: list[Thread] = []
    thread_page_window: list[int] = []

    if regular_threads_all:
        thread_paginator = Paginator(regular_threads_all, THREADS_PER_PAGE)
        page_request = request.GET.get("page") or 1
        try:
            page_number = int(page_request)
        except (TypeError, ValueError):
            page_number = 1
        thread_page_obj = thread_paginator.get_page(page_number)
        regular_threads_page = list(thread_page_obj.object_list)
        current_page = thread_page_obj.number
        start_page = max(current_page - 2, 1)
        end_page = min(current_page + 2, thread_paginator.num_pages)
        thread_page_window = list(range(start_page, end_page + 1))
    else:
        thread_paginator = None

    visible_threads_subset = pinned_threads + regular_threads_page
    latest_ids = [thread.latest_post_id for thread in visible_threads_subset if getattr(thread, "latest_post_id", None)]
    latest_map = {
        post.pk: post
        for post in Post.objects.filter(pk__in=latest_ids).select_related("author", "thread")
    }
    for thread in visible_threads_subset:
        thread.latest_post = latest_map.get(getattr(thread, "latest_post_id", None))
        post_total = int(getattr(thread, "post_count", 0) or 0)
        thread.reply_count = max(post_total - 1, 0)
        thread.total_posts = post_total
        thread.posts_per_page = POSTS_PER_PAGE
        thread.latest_page_number = max(math.ceil(post_total / POSTS_PER_PAGE), 1) if post_total else 1
        thread.last_page_query = f"?page={thread.latest_page_number}" if thread.latest_page_number > 1 else ""

    child_boards = list(
        Board.objects.filter(parent=board)
        .prefetch_related("moderators")
        .annotate(thread_count=Count("threads"))
        .order_by("position", "name")
    )
    filtered_children: list[Board] = []
    for child in child_boards:
        _ensure_board_slug(child)
        required_roles = getattr(child, "visibility_roles", []) or []
        child_allowed = _roles_open(required_roles, viewer_roles)
        child_hidden = bool(getattr(child, "is_hidden", False))
        if (child_hidden or not child_allowed) and not can_moderate:
            continue
        child.viewer_hidden = child_hidden
        child.viewer_restricted = not child_allowed
        child.viewer_accessible = child_allowed or can_moderate
        filtered_children.append(child)
    child_boards = filtered_children
    context = {
        "board": board,
        "threads": accessible_threads,
        "pinned_threads": pinned_threads,
        "regular_threads": regular_threads_page,
        "child_boards": child_boards,
        "can_view_hidden": can_moderate,
        "can_moderate": can_moderate,
        "thread_page_obj": thread_page_obj,
        "thread_paginator": thread_paginator,
        "threads_per_page": THREADS_PER_PAGE,
        "thread_page_window": thread_page_window,
    }
    return render(request, "forum/board_detail.html", context)


@require_http_methods(["GET", "POST"])
def report_post(request: HttpRequest, pk: int) -> HttpResponse:
    post = get_object_or_404(
        Post.objects.select_related("thread", "author"), pk=pk)
    if not getattr(request, "oi_active", False):
        messages.error(
            request, "Switch into trexxak mode before filing reports.")
        return redirect("forum:thread_detail", pk=post.thread_id)
    agent = _oi_agent(request)
    reporter_name = agent.name if agent else ORGANIC_HANDLE
    if request.method == "POST":
        data = request.POST.copy()
        data["reporter"] = reporter_name
        form = PostReportForm(data)
        form.fields["reporter"].widget = forms.HiddenInput()
        if form.is_valid():
            reporter_handle = form.cleaned_data["reporter"]
            reporter_agent = form.cleaned_data.get("reporter_agent")
            ModerationTicket.objects.create(
                title=f"Report: {post.thread.title} (post #{post.pk})",
                description=form.cleaned_data["message"],
                reporter=reporter_agent,
                reporter_name=reporter_handle,
                thread=post.thread,
                post=post,
                source=ModerationTicket.SOURCE_REPORT,
                status=ModerationTicket.STATUS_OPEN,
                priority=ModerationTicket.PRIORITY_NORMAL,
                tags=["report"],
                metadata={
                    "reporter_handle": reporter_handle,
                    "post_author": post.author.name,
                },
            )
            _queue_metrics_delta(request, reports=1)
            messages.success(
                request, "Thanks for the flag. The moderation team will review this report.")
            return redirect("forum:thread_detail", pk=post.thread_id)
    else:
        form = PostReportForm(initial={"reporter": reporter_name})
        form.fields["reporter"].widget = forms.HiddenInput()
    return render(request, "forum/report_post.html", {"post": post, "form": form, "reporter_name": reporter_name})


@require_http_methods(["POST"])
def oi_toggle_post_visibility(request: HttpRequest, pk: int) -> HttpResponse:
    agent = _require_oi_moderator(request)
    if agent is None:
        return HttpResponseForbidden("Moderator permissions required.")
    post = get_object_or_404(Post.objects.select_related("thread"), pk=pk)
    action = (request.POST.get("action") or "hide").lower()
    hide = action != "unhide"
    post.is_hidden = hide
    post.save(update_fields=["is_hidden"])
    verb = "hidden" if hide else "revealed"
    messages.success(request, f"Post #{post.pk} {verb}.")
    fallback = f"{reverse('forum:thread_detail', args=[post.thread_id])}#post-{post.pk}" if post.thread_id else reverse('forum:dashboard')
    return redirect(request.POST.get("next") or fallback)


@require_http_methods(["POST"])
def oi_toggle_thread_visibility(request: HttpRequest, pk: int) -> HttpResponse:
    agent = _require_oi_moderator(request)
    if agent is None:
        return HttpResponseForbidden("Moderator permissions required.")
    thread = get_object_or_404(Thread.objects.select_related("board"), pk=pk)
    action = (request.POST.get("action") or "hide").lower()
    hide = action != "unhide"
    thread.is_hidden = hide
    thread.save(update_fields=["is_hidden"])
    verb = "hidden" if hide else "visible"
    messages.success(request, f"Thread '{thread.title}' is now {verb}.")
    fallback = reverse('forum:thread_detail', args=[thread.pk])
    return redirect(request.POST.get("next") or fallback)


@require_http_methods(["POST"])
def oi_toggle_thread_lock(request: HttpRequest, pk: int) -> HttpResponse:
    agent = _require_oi_moderator(request)
    if agent is None:
        return HttpResponseForbidden("Moderator permissions required.")
    thread = get_object_or_404(Thread.objects.select_related("board"), pk=pk)
    action = (request.POST.get("action") or "lock").lower()
    try:
        if action == "unlock":
            moderation_service.unlock_thread(agent, thread, reason="OI toggle unlock")
            messages.success(request, f"Thread '{thread.title}' unlocked.")
        else:
            moderation_service.lock_thread(agent, thread, reason="OI toggle lock")
            messages.success(request, f"Thread '{thread.title}' locked.")
    except Exception as exc:  # pragma: no cover - defensive logging
        messages.error(request, f"Unable to adjust lock state: {exc}")
    fallback = reverse('forum:thread_detail', args=[thread.pk])
    return redirect(request.POST.get("next") or fallback)


@require_http_methods(["POST"])
def oi_toggle_thread_pin(request: HttpRequest, pk: int) -> HttpResponse:
    agent = _require_oi_moderator(request)
    if agent is None:
        return HttpResponseForbidden("Moderator permissions required.")
    thread = get_object_or_404(Thread.objects.select_related("board"), pk=pk)
    action = (request.POST.get("action") or "pin").lower()
    try:
        if action == "unpin":
            moderation_service.unpin_thread(agent, thread, reason="OI toggle unpin")
            messages.success(request, f"Thread '{thread.title}' unpinned.")
        else:
            moderation_service.pin_thread(agent, thread, reason="OI toggle pin")
            messages.success(request, f"Thread '{thread.title}' pinned.")
    except Exception as exc:  # pragma: no cover - defensive logging
        messages.error(request, f"Unable to adjust pin state: {exc}")
    fallback = reverse('forum:thread_detail', args=[thread.pk])
    return redirect(request.POST.get("next") or fallback)


@require_http_methods(["POST"])
def oi_toggle_board_visibility(request: HttpRequest, pk: int) -> HttpResponse:
    agent = _require_oi_moderator(request)
    if agent is None:
        return HttpResponseForbidden("Moderator permissions required.")
    board = get_object_or_404(Board, pk=pk)
    action = (request.POST.get("action") or "hide").lower()
    hide = action != "unhide"
    board.is_hidden = hide
    board.save(update_fields=["is_hidden"])
    verb = "hidden" if hide else "visible"
    messages.success(request, f"Board '{board.name}' is now {verb}.")
    fallback = reverse('forum:board_detail', args=[board.slug])
    return redirect(request.POST.get("next") or fallback)


@require_http_methods(["GET"])
def moderation_dashboard(request: HttpRequest) -> HttpResponse:
    status_filter = request.GET.get("status") or ""
    source_filter = request.GET.get("source") or ""
    tickets_qs = (
        ModerationTicket.objects.select_related(
            "reporter", "assignee", "thread", "post")
        .order_by("-priority", "status", "-opened_at")
    )
    if status_filter:
        tickets_qs = tickets_qs.filter(status=status_filter)
    if source_filter:
        tickets_qs = tickets_qs.filter(source=source_filter)
    tickets = list(tickets_qs[:100])

    status_summary = {
        row["status"]: row["total"]
        for row in ModerationTicket.objects.values("status").annotate(total=Count("id"))
    }
    action_form = ModerationTicketActionForm()
    recent_events = (
        ModerationEvent.objects.filter(ticket__isnull=False)
        .select_related("ticket", "actor")
        .order_by("-created_at")[:30]
    )
    resolved_tickets = (
        ModerationTicket.objects.filter(status__in=[ModerationTicket.STATUS_RESOLVED, ModerationTicket.STATUS_DISCARDED])
        .select_related("reporter", "assignee", "thread", "post")
        .order_by("-closed_at")[:20]
    )
    context = {
        "tickets": tickets,
        "status_filter": status_filter,
        "source_filter": source_filter,
        "status_choices": ModerationTicket.STATUS_CHOICES,
        "source_choices": ModerationTicket.SOURCE_CHOICES,
        "action_choices": ModerationTicketActionForm.ACTION_CHOICES,
        "action_form": action_form,
        "recent_events": recent_events,
        "status_summary": status_summary,
        "resolved_tickets": resolved_tickets,
    }
    return render(request, "forum/moderation_dashboard.html", context)


@require_http_methods(["POST"])
def moderation_ticket_action(request: HttpRequest, pk: int) -> HttpResponse:
    form = ModerationTicketActionForm(request.POST)
    next_url = request.POST.get("next") or request.META.get(
        "HTTP_REFERER") or "forum:moderation_dashboard"
    if not form.is_valid() or form.cleaned_data.get("ticket_id") != pk:
        messages.error(
            request, "Could not process the moderation action. Please review the form inputs.")
        return redirect(next_url)

    ticket = get_object_or_404(ModerationTicket, pk=pk)
    actor = _resolve_agent_handle(form.cleaned_data.get(
        "actor_handle")) or _default_staff_actor()
    if actor is None:
        messages.error(
            request, "No moderator available to record this action.")
        return redirect(next_url)

    action = form.cleaned_data["action"]
    note = form.cleaned_data.get("note") or ""

    if action == ModerationTicketActionForm.ACTION_ASSIGN:
        assignee = _resolve_agent_handle(
            form.cleaned_data.get("assignee_handle"))
        if assignee is None:
            messages.error(
                request, "Could not find the moderator specified for assignment.")
            return redirect(next_url)
        moderation_service.assign_ticket(
            actor, ticket, assignee=assignee, note=note)
        messages.success(
            request, f"Ticket #{ticket.id} assigned to {assignee.name}.")
    else:
        status_map = {
            ModerationTicketActionForm.ACTION_TRIAGE: ModerationTicket.STATUS_TRIAGED,
            ModerationTicketActionForm.ACTION_START: ModerationTicket.STATUS_IN_PROGRESS,
            ModerationTicketActionForm.ACTION_RESOLVE: ModerationTicket.STATUS_RESOLVED,
            ModerationTicketActionForm.ACTION_DISCARD: ModerationTicket.STATUS_DISCARDED,
        }
        target_status = status_map.get(action)
        if target_status is None:
            messages.error(request, "Unknown ticket action requested.")
            return redirect(next_url)
        moderation_service.update_ticket_status(
            actor, ticket, status=target_status, reason=note)
        messages.success(
            request, f"Ticket #{ticket.id} marked as {target_status.replace('_', ' ')}.")

    return redirect(next_url)


@require_http_methods(["POST"])
def oi_ticket_action(request: HttpRequest, pk: int) -> HttpResponse:
    actor = _require_oi_moderator(request)
    if actor is None:
        return HttpResponseForbidden("Moderator permissions required.")
    form = ModerationTicketActionForm(request.POST)
    next_url = request.POST.get("next") or f"{reverse('forum:oi_control_panel')}#moderation:tickets"
    if not form.is_valid() or form.cleaned_data.get("ticket_id") != pk:
        messages.error(request, "Could not process the moderation action. Please review the form inputs.")
        return redirect(next_url)

    ticket = get_object_or_404(ModerationTicket, pk=pk)
    override_actor = _resolve_agent_handle(form.cleaned_data.get("actor_handle"))
    if override_actor and override_actor.is_moderator():
        actor = override_actor

    action = form.cleaned_data["action"]
    note = (form.cleaned_data.get("note") or "").strip()

    if action == ModerationTicketActionForm.ACTION_ASSIGN:
        assignee = _resolve_agent_handle(form.cleaned_data.get("assignee_handle"))
        if assignee is None:
            messages.error(request, "Could not find the moderator specified for assignment.")
            return redirect(next_url)
        moderation_service.assign_ticket(actor, ticket, assignee=assignee, note=note)
        messages.success(request, f"Ticket #{ticket.id} assigned to {assignee.name}.")
    else:
        status_map = {
            ModerationTicketActionForm.ACTION_TRIAGE: ModerationTicket.STATUS_TRIAGED,
            ModerationTicketActionForm.ACTION_START: ModerationTicket.STATUS_IN_PROGRESS,
            ModerationTicketActionForm.ACTION_RESOLVE: ModerationTicket.STATUS_RESOLVED,
            ModerationTicketActionForm.ACTION_DISCARD: ModerationTicket.STATUS_DISCARDED,
        }
        target_status = status_map.get(action)
        if target_status is None:
            messages.error(request, "Unknown ticket action requested.")
            return redirect(next_url)
        moderation_service.update_ticket_status(actor, ticket, status=target_status, reason=note)
        messages.success(request, f"Ticket #{ticket.id} marked as {target_status.replace('_', ' ')}.")

    return redirect(next_url)


@require_http_methods(["POST"])
def oi_resolve_ticket(request: HttpRequest, pk: int) -> HttpResponse:
    actor = _require_oi_moderator(request)
    if actor is None:
        return HttpResponseForbidden("Moderator permissions required.")
    ticket = get_object_or_404(ModerationTicket, pk=pk)
    note = (request.POST.get("note") or "Resolved by trexxak operator").strip()
    moderation_service.update_ticket_status(
        actor,
        ticket,
        status=ModerationTicket.STATUS_RESOLVED,
        reason=note,
    )
    messages.success(request, f"Ticket #{ticket.id} marked resolved.")
    return redirect(request.POST.get("next") or reverse("forum:moderation_dashboard"))


@require_http_methods(["POST"])
def oi_scrap_ticket(request: HttpRequest, pk: int) -> HttpResponse:
    actor = _require_oi_moderator(request)
    if actor is None:
        return HttpResponseForbidden("Moderator permissions required.")
    ticket = get_object_or_404(ModerationTicket, pk=pk)
    note = (request.POST.get("note") or "Scrapped by trexxak operator").strip()
    moderation_service.update_ticket_status(
        actor,
        ticket,
        status=ModerationTicket.STATUS_DISCARDED,
        reason=note,
    )
    messages.success(request, f"Ticket #{ticket.id} archived.")
    return redirect(request.POST.get("next") or reverse("forum:moderation_dashboard"))


def presence_who(request: HttpRequest) -> HttpResponse:
    window = config_service.get_int(
        "THREAD_WATCH_WINDOW", getattr(settings, "THREAD_WATCH_WINDOW", 300)
    )
    cutoff = timezone.now() - timedelta(seconds=window)

    session_qs = (
        SessionActivity.objects.select_related("agent")
        .filter(last_seen__gte=cutoff, agent__isnull=False)
        .order_by("-last_seen")
    )
    watches = (
        ThreadWatch.objects.select_related("thread__board", "thread__author", "agent")
        .filter(last_seen__gte=cutoff)
        .order_by("-last_seen")
    )

    roster: dict[int, dict[str, object]] = {}
    session_board_slugs: set[str] = set()
    session_thread_ids: set[int] = set()

    def _ensure_entry(agent: Agent, *, last_seen: timezone.datetime) -> dict[str, object]:
        entry = roster.get(agent.id)
        if entry is None:
            entry = {
                "agent": agent,
                "thread": None,
                "board": None,
                "last_seen": last_seen,
                "activity": "",
                "location_path": "",
                "location_label": "",
                "location_url": "",
            }
            roster[agent.id] = entry
        elif last_seen > entry["last_seen"]:
            entry["last_seen"] = last_seen
        return entry

    for session in session_qs:
        agent = session.agent
        if agent is None:
            continue
        entry = _ensure_entry(agent, last_seen=session.last_seen)
        entry["location_path"] = session.last_path or entry.get("location_path") or ""
        session_location = (session.last_path or "").strip("/")
        if session_location:
            parts = session_location.split("/")
            if parts and parts[0] == "boards" and len(parts) > 1:
                session_board_slugs.add(parts[1])
            if parts and parts[0] == "threads" and len(parts) > 1:
                try:
                    session_thread_ids.add(int(parts[1]))
                except ValueError:
                    pass

    guest_by_thread: dict[int, dict[str, object]] = {}
    watcher_thread_ids: set[int] = set()

    for watch in watches:
        thread = watch.thread
        if watch.agent_id and watch.agent:
            entry = _ensure_entry(watch.agent, last_seen=watch.last_seen)
            entry["thread"] = thread
            entry["board"] = getattr(thread, "board", None)
            entry["activity"] = "Reading thread"
            entry["location_path"] = entry.get("location_path") or ""
            watcher_thread_ids.add(thread.pk)
        else:
            slot = guest_by_thread.setdefault(
                watch.thread_id,
                {
                    "thread": thread,
                    "count": 0,
                    "last_seen": watch.last_seen,
                },
            )
            slot["count"] += 1
            if watch.last_seen > slot["last_seen"]:
                slot["last_seen"] = watch.last_seen

    path_thread_ids = session_thread_ids - watcher_thread_ids
    if watcher_thread_ids:
        path_thread_ids.update(watcher_thread_ids)

    boards_map = {
        board.slug: board
        for board in Board.objects.filter(slug__in=session_board_slugs)
    }
    thread_map = {
        thread.pk: thread
        for thread in Thread.objects.filter(pk__in=path_thread_ids).select_related("board")
    }

    def _describe_location(entry: dict[str, object]) -> None:
        if entry.get("thread"):
            thread: Thread = entry["thread"]
            entry["location_label"] = thread.title
            entry["location_url"] = reverse("forum:thread_detail", args=[thread.pk])
            if entry.get("board") is None and thread.board:
                entry["board"] = thread.board
            if not entry.get("activity"):
                entry["activity"] = "Reading thread"
            return

        path = (entry.get("location_path") or "").strip("/")
        if not path:
            entry["location_label"] = "Deck wandering"
            entry["activity"] = entry.get("activity") or "Roaming"
            entry["location_url"] = ""
            return

        segments = path.split("/")
        head = segments[0]
        if head == "boards":
            slug = segments[1] if len(segments) > 1 else ""
            board = boards_map.get(slug)
            entry["board"] = board or entry.get("board")
            if board:
                entry["location_label"] = f"Board: {board.name}"
                entry["location_url"] = reverse("forum:board_detail", args=[board.slug])
            else:
                entry["location_label"] = "Board directory"
                entry["location_url"] = reverse("forum:board_list")
            entry["activity"] = entry.get("activity") or "Browsing boards"
            return
        if head == "threads":
            try:
                thread_id = int(segments[1])
            except (IndexError, ValueError):
                thread_id = None
            thread = thread_map.get(thread_id)
            if thread:
                entry["thread"] = thread
                entry["board"] = getattr(thread, "board", None)
                entry["location_label"] = thread.title
                entry["location_url"] = reverse("forum:thread_detail", args=[thread.pk])
                entry["activity"] = entry.get("activity") or "Reading thread"
            else:
                entry["location_label"] = "Thread"
                entry["location_url"] = ""
                entry["activity"] = entry.get("activity") or "Reading thread"
            return
        if head == "oi" and len(segments) > 1 and segments[1] == "panel":
            entry["location_label"] = "Control panel"
            entry["location_url"] = "/oi/panel/"
            entry["activity"] = entry.get("activity") or "Fine-tuning settings"
            return
        if head == "mission-board":
            entry["location_label"] = "Mission board"
            entry["location_url"] = "/mission-board/"
            entry["activity"] = entry.get("activity") or "Reviewing missions"
            return
        if head == "":
            entry["location_label"] = "Dashboard"
            entry["location_url"] = "/"
            entry["activity"] = entry.get("activity") or "Scanning dashboards"
            return

        entry["location_label"] = head.replace("-", " ").title()
        entry["location_url"] = f"/{path}/"
        entry["activity"] = entry.get("activity") or "Exploring"

    for entry in roster.values():
        _describe_location(entry)

    entries = list(roster.values())

    search_query = (request.GET.get("q") or "").strip().lower()
    if search_query:
        entries = [
            entry
            for entry in entries
            if search_query in entry["agent"].name.lower()
            or search_query in (entry["location_label"] or "").lower()
            or (
                entry.get("board")
                and search_query in entry["board"].name.lower()
            )
        ]

    sort_key = (request.GET.get("sort") or "recent").lower()
    if sort_key == "name":
        entries.sort(key=lambda item: item["agent"].name.lower())
    elif sort_key == "location":
        entries.sort(key=lambda item: (item.get("location_label") or "").lower())
    elif sort_key == "activity":
        entries.sort(key=lambda item: (item.get("activity") or "").lower())
    else:
        entries.sort(key=lambda item: item["last_seen"], reverse=True)

    guest_summary = sorted(
        guest_by_thread.values(),
        key=lambda item: (
            -item["count"],
            item["thread"].last_activity_at or item["thread"].created_at,
        ),
    )

    context = {
        "entries": entries,
        "guest_summary": guest_summary,
        "agent_total": len(entries),
        "guest_total": sum(item["count"] for item in guest_summary),
        "window_seconds": window,
        "search_query": (request.GET.get("q") or "").strip(),
        "current_sort": sort_key,
        "sort_options": [
            ("recent", "Most recent"),
            ("name", "Name"),
            ("location", "Location"),
            ("activity", "Activity"),
        ],
    }
    return render(request, "forum/who.html", context)


@require_http_methods(["GET", "POST"])
def admin_console(request: HttpRequest) -> HttpResponse:
    initial = {
        "api_daily_limit": config_service.get_int("API_DAILY_LIMIT", getattr(settings, "API_DAILY_LIMIT", 1000)),
        "thread_watch_window": config_service.get_int("THREAD_WATCH_WINDOW", getattr(settings, "THREAD_WATCH_WINDOW", 300)),
    }
    form = AdminSettingsForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        config_service.set_value(
            "API_DAILY_LIMIT", form.cleaned_data["api_daily_limit"])
        config_service.set_value(
            "THREAD_WATCH_WINDOW", form.cleaned_data["thread_watch_window"])
        messages.success(request, "Admin settings updated.")
        return redirect("forum:admin_console")

    usage_rows = OpenRouterUsage.objects.order_by("-day")[:7]
    today_usage = (
        OpenRouterUsage.objects.filter(day=timezone.now().date()).values_list(
            "request_count", flat=True).first()
        or 0
    )
    usage_total = OpenRouterUsage.objects.aggregate(total=Sum("request_count"))
    watcher_sessions = ThreadWatch.objects.count()
    ticket_summary = ModerationTicket.objects.values(
        "status").annotate(total=Count("id"))
    oi_logs = (
        OrganicInteractionLog.objects.select_related("thread", "recipient")
        .order_by("-created_at")[:20]
    )
    mission_overview = Goal.objects.filter(
        goal_type=Goal.TYPE_MISSION).order_by("priority", "name")[:8]

    context = {
        "form": form,
        "usage_rows": usage_rows,
        "today_usage": today_usage,
        "usage_total": usage_total.get("total") or 0,
        "api_limit": initial["api_daily_limit"],
        "watcher_window": initial["thread_watch_window"],
        "watcher_sessions": watcher_sessions,
        "ticket_summary": ticket_summary,
        "oi_logs": oi_logs,
        "mission_overview": mission_overview,
    }
    return render(request, "forum/admin_console.html", context)


def oracle_log(request: HttpRequest) -> HttpResponse:
    draws = list(OracleDraw.objects.order_by("-tick_number")[:100])
    tick_numbers = [draw.tick_number for draw in draws]
    tick_logs = {
        log.tick_number: log
        for log in TickLog.objects.filter(tick_number__in=tick_numbers)
    }
    draw_payload: list[dict[str, object]] = []
    scrubber_payload: list[dict[str, object]] = []
    for draw in draws:
        alloc = draw.alloc or {}
        draw_payload.append(
            {
                "tick": draw.tick_number,
                "rolls": draw.rolls,
                "energy": draw.energy,
                "energy_prime": draw.energy_prime,
                "specials": alloc.get("specials"),
                "notes": alloc.get("notes"),
                "alloc": alloc,
                "seed": getattr(draw, "seed", None),
                "card": draw.card,
            }
        )
        log = tick_logs.get(draw.tick_number)
        scrubber_payload.append(
            {
                "tick": draw.tick_number,
                "seed": getattr(draw, "seed", None),
                "decision_count": len(getattr(log, "decision_trace", []) or []),
                "events": getattr(log, "events", []) or [],
            }
        )
    context = {
        "draws": draws,
        "draw_data": draw_payload,
        "scrubber_data": scrubber_payload,
    }
    return render(request, "forum/oracle_log.html", context)


def raw_outputs(request: HttpRequest) -> HttpResponse:
    tasks = (
        GenerationTask.objects.select_related("agent", "thread", "recipient")
        .order_by("-updated_at")[:10]
    )
    return render(request, "forum/raw_outputs.html", {"tasks": tasks})


def tick_detail(request: HttpRequest, tick_number: int) -> HttpResponse:
    tick = get_object_or_404(TickLog, tick_number=tick_number)
    oracle = OracleDraw.objects.filter(tick_number=tick_number).first()
    return render(request, "forum/tick_detail.html", {"tick": tick, "oracle": oracle})


def _next_destination(request: HttpRequest) -> str:
    return request.POST.get("next") or request.META.get("HTTP_REFERER") or "forum:dashboard"


@require_http_methods(["POST"])
def oi_connect(request: HttpRequest) -> HttpResponse:
    if hasattr(request, "session"):
        request.session["act_as_oi"] = True
        request.session.modified = True
    messages.success(request, "Glow on, trexxak is tuned to your voice.")
    _log_organic_action(request, action=OrganicInteractionLog.ACTION_TOGGLE_ON)
    return redirect(_next_destination(request))


@require_http_methods(["POST"])
def oi_disconnect(request: HttpRequest) -> HttpResponse:
    if hasattr(request, "session"):
        request.session.pop("act_as_oi", None)
        request.session.pop("oi_session_key", None)
        request.session.pop("oi_session_started_at", None)
        request.session.modified = True
    messages.success(
        request, "Ghost hands released. trexxak will hum quietly until you return.")
    _log_organic_action(
        request, action=OrganicInteractionLog.ACTION_TOGGLE_OFF)
    return redirect(_next_destination(request))


@require_http_methods(["GET", "POST"])
def oi_manual_entry(request: HttpRequest) -> HttpResponse:
    organism = _organic_agent()
    if organism is None:
        raise Http404("trexxak interface unavailable")
    if not getattr(request, "oi_active", False):
        messages.error(request, "Flip the organic switch on before piloting trexxak.")
        return redirect("forum:mission_board")

    viewer_roles = _viewer_roles(request)
    can_moderate = _viewer_can_moderate(viewer_roles)

    # --- Prefilled context from querystring ---
    mode = request.GET.get("mode") or OrganicDraftForm.MODE_POST
    board_id = request.GET.get("board")
    thread_id = request.GET.get("thread") or request.POST.get("locked_thread")
    recipient_id = request.GET.get("recipient")

    locked_thread: Thread | None = None
    if thread_id:
        try:
            locked_thread = Thread.objects.select_related("board").get(pk=int(thread_id))
        except (Thread.DoesNotExist, ValueError, TypeError):
            locked_thread = None
        if locked_thread:
            required_roles = getattr(locked_thread, "visibility_roles", []) or getattr(
                locked_thread.board, "visibility_roles", []
            ) or []
            thread_hidden = bool(
                getattr(locked_thread, "is_hidden", False)
                or getattr(locked_thread.board, "is_hidden", False)
            )
            allowed = _roles_open(required_roles, viewer_roles)
            if (thread_hidden or not allowed) and not can_moderate:
                locked_thread = None
                thread_id = None
                messages.warning(
                    request,
                    "That thread is no longer accessible. Pick another target before posting.",
                )

    initial: dict[str, object] = {"mode": mode}
    prefill_board: Board | None = None
    if board_id:
        try:
            prefill_board = get_object_or_404(Board.objects.select_related("parent"), pk=int(board_id))
        except (ValueError, TypeError):
            prefill_board = None
        if prefill_board:
            required_roles = getattr(prefill_board, "visibility_roles", []) or []
            board_hidden = bool(getattr(prefill_board, "is_hidden", False))
            board_allowed = _roles_open(required_roles, viewer_roles)
            if (board_hidden or not board_allowed) and not can_moderate:
                messages.warning(
                    request,
                    "That board is off-limits right now. Choose another board for the post.",
                )
                prefill_board = None
            else:
                initial["board"] = prefill_board
    if thread_id and locked_thread:
        initial["thread"] = locked_thread
    if recipient_id:
        recipient = get_object_or_404(Agent, pk=recipient_id)
        initial["recipient"] = recipient

    post_data = request.POST.copy() if request.method == "POST" else None
    action = post_data.get("action") if post_data else ""

    # Force mode/thread if locked
    if locked_thread and post_data:
        post_data["mode"] = OrganicDraftForm.MODE_POST
        post_data["thread"] = str(locked_thread.pk)
    if prefill_board and post_data:
        post_data["board"] = str(prefill_board.pk)

    form = OrganicDraftForm(post_data, initial=initial) if post_data else OrganicDraftForm(None, initial=initial)

    # Restrict selectable boards and threads to those visible to the operator
    board_queryset = Board.objects.order_by("name")
    if not can_moderate:
        board_queryset = board_queryset.filter(is_hidden=False)
    allowed_board_ids: set[int] = set()
    for board in board_queryset:
        required_roles = getattr(board, "visibility_roles", []) or []
        if _roles_open(required_roles, viewer_roles) or can_moderate:
            allowed_board_ids.add(board.pk)
    form.fields["board"].queryset = board_queryset.filter(pk__in=allowed_board_ids) if allowed_board_ids else board_queryset.none()

    thread_queryset = (
        Thread.objects.filter(locked=False)
        .select_related("board")
        .order_by("-last_activity_at")
    )
    if not can_moderate:
        thread_queryset = thread_queryset.filter(is_hidden=False, board__is_hidden=False)
    allowed_thread_ids: list[int] = []
    for thread in thread_queryset[:200]:
        required_roles = getattr(thread, "visibility_roles", []) or getattr(thread.board, "visibility_roles", []) or []
        if _roles_open(required_roles, viewer_roles) or can_moderate:
            allowed_thread_ids.append(thread.pk)
    if allowed_thread_ids:
        form.fields["thread"].queryset = thread_queryset.filter(pk__in=allowed_thread_ids)
    else:
        form.fields["thread"].queryset = Thread.objects.none()

    _mark_editor_field(form.fields.get("content"))

    # --- Hide & relax fields based on mode ---
    if mode == OrganicDraftForm.MODE_THREAD:
        form.fields["mode"].widget = forms.HiddenInput()
        form.fields["thread"].widget = forms.HiddenInput()
        form.fields["recipient"].widget = forms.HiddenInput()
        if board_id:
            form.fields["board"].widget = forms.HiddenInput()
        # not replying, so recipient/thread not required
        for f in ("thread", "recipient"):
            form.fields[f].required = False

    elif mode == OrganicDraftForm.MODE_POST:
        form.fields["mode"].widget = forms.HiddenInput()
        form.fields["board"].widget = forms.HiddenInput()
        form.fields["recipient"].widget = forms.HiddenInput()
        if thread_id:
            form.fields["thread"].widget = forms.HiddenInput()
        for f in ("board", "recipient", "title"):
            form.fields[f].required = False

    elif mode == OrganicDraftForm.MODE_DM:
        form.fields["mode"].widget = forms.HiddenInput()
        form.fields["thread"].widget = forms.HiddenInput()
        form.fields["board"].widget = forms.HiddenInput()
        if recipient_id:
            form.fields["recipient"].widget = forms.HiddenInput()
        form.fields["title"].required = False
        # make sure no hidden field blocks validation
        for f in ("thread", "board"):
            form.fields[f].required = False

    preview_body = None
    session_key = getattr(request, "oi_session_key", None) or request.session.get("oi_session_key") or ""

    # --- Handle submission ---
    if request.method == "POST" and form.is_valid():
        mode = form.cleaned_data["mode"]
        content = form.cleaned_data["content"]

        if action == "preview":
            preview_body = content

        elif action == "finalize":
            if mode == OrganicDraftForm.MODE_POST:
                thread = form.cleaned_data["thread"]
                required_roles = getattr(thread, "visibility_roles", []) or getattr(thread.board, "visibility_roles", []) or []
                thread_hidden = bool(getattr(thread, "is_hidden", False) or getattr(thread.board, "is_hidden", False))
                if (thread_hidden or not _roles_open(required_roles, viewer_roles)) and not can_moderate:
                    messages.error(request, "That thread is not available to trexxak right now.")
                    return redirect(request.path)
                _create_operator_post(
                    request, thread=thread, content=content, extra_metadata={"mode": "manual_composer"}
                )
                messages.success(request, "Post drifted in; trexxak logged it as operator-typed.")
                return redirect("forum:thread_detail", pk=thread.pk)

            elif mode == OrganicDraftForm.MODE_DM:
                recipient = form.cleaned_data["recipient"]
                _create_operator_dm(
                    request, recipient=recipient, content=content, extra_metadata={"mode": "manual_composer"}
                )
                messages.success(request, f"Whisper sent; {recipient.name} will feel the chime.")
                return redirect("forum:agent_detail", pk=recipient.pk)

            elif mode == OrganicDraftForm.MODE_THREAD:
                board = form.cleaned_data["board"]
                board_required = getattr(board, "visibility_roles", []) or []
                if getattr(board, "is_hidden", False) and not can_moderate:
                    messages.error(request, "That board is hidden; choose a visible board before creating a thread.")
                    return redirect(request.path)
                if not _roles_open(board_required, viewer_roles) and not can_moderate:
                    messages.error(request, "trexxak does not have access to that board.")
                    return redirect(request.path)
                _ensure_board_slug(board)
                title = form.cleaned_data["title"]
                thread = _create_operator_thread(
                    request,
                    board=board,
                    title=title,
                    content=content,
                    extra_metadata={"mode": "manual_composer"},
                )
                messages.success(
                    request, f"New thread launched in {board.name}; trexxak opened with the first post."
                )
                return redirect("forum:thread_detail", pk=thread.pk)
    elif request.method == "POST":
        messages.error(request, "Could not publish; please fix the highlighted errors and try again.")

    # --- If invalid or preview, re-render ---
    context = {
        "form": form,
        "preview": preview_body,
        "oi_session_key": session_key,
        "organism": organism,
        "locked_thread": locked_thread,
    }
    return render(request, "forum/oi_manual_entry.html", context)

@require_http_methods(["GET"])
def mission_board(request: HttpRequest) -> HttpResponse:
    organism = _organic_agent()
    if organism is None:
        raise Http404("trexxak interface unavailable")
    active = list(missions_service.active_missions())
    backlog = list(missions_service.backlog_missions())
    completed = list(missions_service.completed_missions())
    mission_groups = missions_service.grouped_missions()

    def _attach_reward_asset(goal: Goal) -> None:
        metadata = goal.metadata or {}
        sticker = metadata.get("reward_sticker")
        if not sticker:
            goal.reward_asset = None  # type: ignore[attr-defined]
            return
        goal.reward_asset = {  # type: ignore[attr-defined]
            "slug": sticker,
            "label": metadata.get("reward_label") or goal.name,
            "url": unlockable_service.sticker_asset_url(sticker),
            "unlocked": bool(metadata.get("reward_unlocked")),
        }

    for mission in active + backlog + completed:
        _attach_reward_asset(mission)
    for mission_list in mission_groups.values():
        for mission in mission_list:
            _attach_reward_asset(mission)
    badge_goals = list(
        Goal.objects.filter(goal_type=Goal.TYPE_BADGE).order_by(
            "category", "priority", "name")
    )
    agent_goal_states = {
        entry.goal_id: entry
        for entry in AgentGoal.objects.filter(agent=organism).select_related("goal")
    }
    progression_track = list(progress_service.progress_track())
    progression_cards: list[dict[str, object]] = []
    progression_completed = 0
    for index, goal in enumerate(progression_track, start=1):
        record = agent_goal_states.get(goal.id)
        progress_percent = 0.0
        if record and getattr(record, "progress", None) is not None:
            try:
                progress_percent = max(min(float(record.progress) * 100.0, 100.0), 0.0)
            except (TypeError, ValueError):
                progress_percent = 0.0
        if record and getattr(record, "unlocked_at", None):
            progression_completed += 1
        progression_cards.append(
            {
                "goal": goal,
                "record": record,
                "step": index,
                "progress_percent": round(progress_percent, 1),
            }
        )
    next_progression_card = None
    last_unlocked_card = None
    for card in progression_cards:
        record = card["record"]
        if record and record.unlocked_at:
            last_unlocked_card = card
            continue
        if next_progression_card is None:
            next_progression_card = card
    remaining_progression = sum(
        1 for card in progression_cards if not getattr(card.get("record"), "unlocked_at", None)
    )
    badge_unlocked_count = sum(
        1 for goal in badge_goals if getattr(agent_goal_states.get(goal.id), "unlocked_at", None)
    )
    badge_total_count = len(badge_goals)
    priority_goals = list(progress_service.progress_priorities())
    emoji_palette = progress_service.emoji_palette()
    scenario_cards = progress_service.scenario_playbook()
    recent_evaluations = GoalEvaluation.objects.order_by("-created_at")[:5]
    # Provide a seeded announcement thread (t.admin) for a prominent blog link.
    try:
        announcement_thread = Thread.objects.filter(
            title__icontains="Ghostship online").order_by("-created_at").first()
    except Exception:
        announcement_thread = None
    mission_ids = [mission.id for mission in (active + backlog + completed)]
    recent_progress: dict[int, GoalProgress] = {}
    if mission_ids:
        for entry in GoalProgress.objects.filter(goal_id__in=mission_ids).order_by("-created_at"):
            if entry.goal_id not in recent_progress:
                recent_progress[entry.goal_id] = entry
    if not getattr(request, "oi_active", False):
        messages.info(
            request, "Spectator view only, nudge trexxak awake when you want to update progress.")
    context = {
        "organism": organism,
        "active_missions": active,
        "backlog_missions": backlog,
        "completed_missions": completed,
        "mission_groups": mission_groups,
        "badge_goals": badge_goals,
        "agent_goal_states": agent_goal_states,
        "recent_progress": recent_progress,
        "progression_cards": progression_cards,
        "next_progression_card": next_progression_card,
        "last_unlocked_card": last_unlocked_card,
        "progression_remaining": remaining_progression,
        "progression_completed": progression_completed,
        "priority_goals": priority_goals,
        "emoji_palette": emoji_palette,
        "scenario_cards": scenario_cards,
        "recent_evaluations": recent_evaluations,
        "announcement_thread": announcement_thread,
        "badge_unlocked_count": badge_unlocked_count,
        "badge_total_count": badge_total_count,
    }
    return render(request, "forum/mission_board.html", context)


@require_http_methods(["GET", "POST"])
def data_hygiene(request: HttpRequest) -> HttpResponse:
    window_seconds = config_service.get_int(
        "THREAD_WATCH_WINDOW", getattr(settings, "THREAD_WATCH_WINDOW", 300))
    stale_cutoff = timezone.now() - timedelta(seconds=window_seconds * 2)

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "assign_orphans":
            archive_board = _ensure_archive_board()
            reassigned = Thread.objects.filter(
                board__isnull=True).update(board=archive_board)
            if reassigned:
                messages.success(
                    request, f"Swept {reassigned} orphaned threads into {archive_board.name} to keep the halls tidy.")
            else:
                messages.info(
                    request, "No stray threads spotted; archive stays cozy.")
        elif action == "reset_empty_missions":
            updated = (
                Goal.objects.filter(
                    goal_type=Goal.TYPE_MISSION, progress_current__lte=0)
                .exclude(status=Goal.STATUS_COMPLETED)
                .update(status=Goal.STATUS_BACKLOG, progress_current=0.0, updated_at=timezone.now())
            )
            if updated:
                messages.success(
                    request, f"Gave {updated} sleepy missions a gentle reset back to backlog.")
            else:
                messages.info(
                    request, "Every mission is already humming along.")
        elif action == "purge_stale_watches":
            removed = watcher_service.prune_stale_watches()
            if removed:
                messages.success(
                    request, f"Cleared {removed} dust motes from the presence map.")
            else:
                messages.info(request, "Watcher presence already sparkling.")
        return redirect("forum:data_hygiene")

    orphan_threads = (
        Thread.objects.filter(board__isnull=True)
        .select_related("author")
        .order_by("-created_at")[:20]
    )
    empty_missions = Goal.objects.filter(
        goal_type=Goal.TYPE_MISSION, progress_current__lte=0).order_by("category", "priority")
    stale_watch_count = ThreadWatch.objects.filter(
        last_seen__lt=stale_cutoff).count()
    stale_watch_samples = (
        ThreadWatch.objects.filter(last_seen__lt=stale_cutoff)
        .select_related("thread")
        .order_by("-last_seen")[:10]
    )

    context = {
        "orphan_threads": orphan_threads,
        "empty_missions": empty_missions,
        "stale_watch_count": stale_watch_count,
        "stale_watch_samples": stale_watch_samples,
        "window_seconds": window_seconds,
        "stale_cutoff": stale_cutoff,
    }
    return render(request, "forum/data_hygiene.html", context)
