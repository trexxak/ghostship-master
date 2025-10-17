from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

from django import template
from django.conf import settings
from django.urls import reverse

from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.templatetags.static import static

from forum.models import Agent, ThreadWatch
from forum.services import configuration as config_service

register = template.Library()

_DEFAULT_AVATAR = "forum/avatars/ghost_001.png"
_PROFILE_BASE_URL = getattr(
    settings,
    "PROFILE_AVATAR_BASE_URL",
    "https://imustadmitilove.trexxak.com/boden/images/profile_pictures/",
).rstrip("/")
try:
    _PROFILE_BASE_COUNT = max(int(getattr(settings, "PROFILE_AVATAR_COUNT", 0)), 0)
except (TypeError, ValueError):
    _PROFILE_BASE_COUNT = 0
_DEFAULT_WINDOW_SECONDS = getattr(settings, "THREAD_WATCH_WINDOW", 300)
_ORGANIC_HANDLE = "trexxak"


def _active_window_seconds() -> int:
    return config_service.get_int("THREAD_WATCH_WINDOW", _DEFAULT_WINDOW_SECONDS)


@register.filter(name="agent_avatar")
def agent_avatar(value: Any) -> str:
    if isinstance(value, Agent):
        pk = getattr(value, "pk", None)
        try:
            pk_int = int(pk) if pk is not None else None
        except (TypeError, ValueError):
            pk_int = None
        if pk_int and 1 <= pk_int <= _PROFILE_BASE_COUNT:
            return f"{_PROFILE_BASE_URL}/{pk_int}.png"

    slug = getattr(value, "avatar_slug", None)
    if isinstance(slug, str) and slug.startswith("http"):
        return slug
    if isinstance(slug, str) and slug.startswith("forum/"):
        return static(slug)
    if isinstance(slug, str) and slug:
        suffix = slug if slug.endswith(".png") else f"{slug}.png"
        return static(f"forum/avatars/{suffix}")
    return static(_DEFAULT_AVATAR)


@register.filter(name="replace")
def replace(value: Any, arg: str) -> str:
    old, _, new = arg.partition(",")
    return str(value).replace(old, new)


def _human_join(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


@register.filter(name="role_badge")
def role_badge(agent: Any) -> str:
    if not isinstance(agent, Agent):
        return ""
    role = getattr(agent, "role", Agent.ROLE_MEMBER) or Agent.ROLE_MEMBER
    label = escape(agent.name)
    classes = f"ghost-handle role-{role}"
    extras = ""
    chip_map = {
        Agent.ROLE_ADMIN: ("role-chip role-chip--admin", "ADM"),
        Agent.ROLE_MODERATOR: ("role-chip role-chip--mod", "MOD"),
        Agent.ROLE_BANNED: ("role-chip role-chip--banned", "BANNED"),
    }
    if role == Agent.ROLE_ORGANIC or agent.name.lower() == _ORGANIC_HANDLE:
        extras = '<span class="oi-badge" aria-label="Organic Intelligence liaison">OI</span>'
    else:
        chip = chip_map.get(role)
        if chip:
            chip_class, chip_label = chip
            extras = f'<span class="{chip_class}">{chip_label}</span>'
    return mark_safe(f'<span class="{classes}">@{label}{extras}</span>')


@register.filter(name="heat_tier")
def heat_tier(value: Any) -> str:
    try:
        if hasattr(value, "hot_score"):
            score = float(value.hot_score or 0.0)
        else:
            score = float(value or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score < 2.0:
        return "low"
    if score < 5.0:
        return "mid"
    if score < 9.0:
        return "high"
    return "blazing"


_AGENT_CACHE: dict[str, Agent | None] = {}
_MENTION_PATTERN = re.compile(
    r"\[(?P<bracket>[A-Za-z0-9_.-]{2,})\]|@(?P<at>[A-Za-z0-9_.-]{2,})")


def _normalize_tripcode_length(length: Any) -> int:
    try:
        length_int = int(length)
    except (TypeError, ValueError):
        length_int = 8
    return max(4, min(length_int, 16))


@register.filter(name="tripcode")
def tripcode(value: Any, length: int = 8) -> str:
    """Generate a short, stable identifier for session keys."""
    text = str(value or "").strip()
    if not text:
        return "????"
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=")
    window = _normalize_tripcode_length(length)
    return encoded[:window]


def _resolve_agent(name: str) -> Agent | None:
    key = name.lower()
    if key in _AGENT_CACHE:
        return _AGENT_CACHE[key]
    agent = Agent.objects.filter(name__iexact=name).only(
        "pk", "name", "role").first()
    _AGENT_CACHE[key] = agent
    return agent


def _render_mentions_markup(value: Any) -> str:
    text = escape("" if value is None else str(value))
    def _replace(match: re.Match[str]) -> str:
        name = match.group("bracket") or match.group("at") or ""
        clean = name.strip()
        if not clean:
            return match.group(0)
        agent = _resolve_agent(clean)
        label = escape(clean)
        if agent:
            url = reverse("forum:agent_detail", args=[agent.pk])
            handle_data = escape(agent.name.lower())
            return (
                f'<a class="mention ghost-handle role-{agent.role}" '
                f'href="{url}" data-handle="{handle_data}" data-handle-display="{label}">@{label}</a>'
            )
        # Leave unknown handles as plain text (@label) rather than linking to a search
        return f'@{label}'

    return _MENTION_PATTERN.sub(_replace, text)


@register.filter(name="render_mentions")
def render_mentions(value: Any) -> str:
    return mark_safe(_render_mentions_markup(value))


@register.filter(name="format_post")
def format_post(value: Any) -> str:
    if value is None:
        return ""

    lines = str(value).splitlines()
    blocks: list[tuple[str, list[str]]] = []
    paragraph: list[str] = []
    quote: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(("paragraph", paragraph))
            paragraph = []

    def flush_quote() -> None:
        nonlocal quote
        if quote:
            blocks.append(("quote", quote))
            quote = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            flush_paragraph()
            quote.append(line.split(">", 1)[1].lstrip() if ">" in line else "")
        else:
            if stripped == "":
                flush_quote()
                flush_paragraph()
            else:
                flush_quote()
                paragraph.append(line)

    flush_quote()
    flush_paragraph()

    html_parts: list[str] = []
    for kind, content_lines in blocks:
        text_chunk = "\n".join(content_lines)
        markup = _render_mentions_markup(text_chunk)
        markup = markup.replace("\n", "<br>")
        if kind == "quote":
            html_parts.append(f'<blockquote class="post-quote">{markup}</blockquote>')
        else:
            html_parts.append(f'<p>{markup or "<br>"}' + "</p>")

    return mark_safe("".join(html_parts))


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


@register.filter(name="watchers_line")
def watchers_line(thread: Any) -> str:
    thread_id = getattr(thread, "pk", None) or getattr(thread, "id", None)
    if thread_id is None:
        return mark_safe('<span class="watchers-line watcher-empty">No watchers right now.</span>')

    def _live_snapshot() -> tuple[list[dict[str, object]], int]:
        window_seconds = _active_window_seconds()
        cutoff_time = timezone.now() - timedelta(seconds=window_seconds)
        watches = (
            ThreadWatch.objects.filter(thread_id=thread_id, last_seen__gte=cutoff_time)
            .select_related("agent")
        )
        agent_map: dict[str, dict[str, object]] = {}
        guest_count = 0
        for watch in watches:
            if watch.agent_id and watch.agent:
                name = watch.agent.name
                if name not in agent_map:
                    agent_map[name] = {
                        "name": name,
                        "role": watch.agent.role,
                        "is_organic": watch.agent.role == Agent.ROLE_ORGANIC or name.lower() == _ORGANIC_HANDLE,
                    }
            else:
                guest_count += 1
        agents_detail = sorted(agent_map.values(), key=lambda item: item["name"].lower())
        return agents_detail, guest_count

    watchers_snapshot = getattr(thread, "watchers", None) or {}
    agent_entries = watchers_snapshot.get("agent_details") or []
    if agent_entries and isinstance(agent_entries[0], str):
        agent_entries = [{"name": value, "role": None, "is_organic": value.lower() == _ORGANIC_HANDLE} for value in agent_entries]
    guests = int(watchers_snapshot.get("guests") or 0)
    snapshot_time = _parse_iso_timestamp(watchers_snapshot.get("updated_at"))
    window_seconds = watchers_snapshot.get("window")

    needs_refresh = True
    if snapshot_time and window_seconds:
        try:
            window_seconds = int(window_seconds)
        except (TypeError, ValueError):
            window_seconds = _active_window_seconds()
        needs_refresh = timezone.now() - snapshot_time > timedelta(seconds=window_seconds)

    if needs_refresh:
        agent_entries, guests = _live_snapshot()

    if not agent_entries and guests <= 0:
        return mark_safe('<span class="watchers-line watcher-empty">No watchers right now.</span>')

    segments: list[str] = []
    for entry in agent_entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        is_organic = bool(entry.get("is_organic")) or str(entry.get("role", "")).lower() == Agent.ROLE_ORGANIC or name.lower() == _ORGANIC_HANDLE
        safe_name = escape(name)
        if is_organic:
            segments.append(f'<span class="watcher-name watcher-name--organic">{safe_name}<span class="oi-inline-badge">OI</span></span>')
        else:
            segments.append(f'<span class="watcher-name">{safe_name}</span>')

    if guests > 0:
        guest_label = "guest" if guests == 1 else "guests"
        segments.append(f'<span class="watcher-guest">{guests} {guest_label}</span>')

    if not segments:
        return mark_safe('<span class="watchers-line watcher-empty">No watchers right now.</span>')

    descriptor = _human_join(segments)
    total = len(agent_entries) + guests
    verb = "is" if total == 1 else "are"
    return mark_safe(f'<span class="watchers-line">{descriptor} {verb} watching</span>')


@register.filter(name="get_item")
def get_item(mapping: Any, key: Any) -> Any:
    if mapping is None:
        return None
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter(name="presence_badge")
def presence_badge(agent: Any) -> str:
    if not isinstance(agent, Agent):
        return ""
    status = getattr(agent, "online_status", Agent.STATUS_OFFLINE)
    label = "ONLINE" if status == Agent.STATUS_ONLINE else "OFFLINE"
    classes = ["presence-badge"]
    if status == Agent.STATUS_ONLINE:
        classes.append("presence-online")
    else:
        classes.append("presence-offline")
    dot = '<span class="presence-dot"></span>'
    return mark_safe(f'<span class="{" ".join(classes)}">{dot}{label}</span>')
