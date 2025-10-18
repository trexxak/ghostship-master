from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote_plus
from xml.etree.ElementTree import Element, tostring

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


def _build_mention_element(name: str) -> tuple[Element | None, str]:
    clean = (name or "").strip()
    if not clean:
        return None, ""
    agent = _resolve_agent(clean)
    label = agent.name if agent else clean
    display = f"@{label}"
    if agent:
        element = Element("a")
        element.set("class", f"mention ghost-handle role-{agent.role}")
        element.set("href", reverse("forum:agent_detail", args=[agent.pk]))
        element.set("data-handle", agent.name.lower())
        element.set("data-handle-display", label)
        element.set("rel", "nofollow")
        element.text = display
        return element, display
    return None, display


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
    raw_text = "" if value is None else str(value)
    if not raw_text:
        return ""

    parts: list[str] = []
    last_index = 0
    for match in _MENTION_PATTERN.finditer(raw_text):
        start, end = match.span()
        if start > last_index:
            parts.append(escape(raw_text[last_index:start]))
        name = match.group("bracket") or match.group("at") or ""
        element, fallback = _build_mention_element(name)
        if element is not None:
            parts.append(tostring(element, encoding="unicode", method="html"))
        else:
            parts.append(escape(fallback))
        last_index = end
    if last_index < len(raw_text):
        parts.append(escape(raw_text[last_index:]))
    return "".join(parts)


@register.filter(name="render_mentions")
def render_mentions(value: Any) -> str:
    return mark_safe(_render_mentions_markup(value))


def _render_inline_markup(text: str) -> str:
    def _render_segment(segment: str) -> str:
        result: list[str] = []
        index = 0
        length = len(segment)
        while index < length:
            char = segment[index]
            if char == "\n":
                result.append("<br>")
                index += 1
                continue
            if segment.startswith("**", index):
                close = segment.find("**", index + 2)
                if close != -1:
                    before = segment[index - 1] if index > 0 else " "
                    after = segment[close + 2] if close + 2 < length else " "
                    if (before.isspace() or before in "([{'\"") and (after.isspace() or after in ")]}'\"',.!?:;"):
                        inner = _render_segment(segment[index + 2 : close])
                        result.append(f"<strong>{inner}</strong>")
                        index = close + 2
                        continue
                result.append(escape("*"))
                index += 1
                continue
            if char == "_":
                close = segment.find("_", index + 1)
                if close != -1 and close > index + 1:
                    before = segment[index - 1] if index > 0 else " "
                    after = segment[close + 1] if close + 1 < length else " "
                    if (before.isspace() or before in "([{'\"") and (after.isspace() or after in ")]}'\"',.!?:;"):
                        inner = _render_segment(segment[index + 1 : close])
                        result.append(f"<em>{inner}</em>")
                        index = close + 1
                        continue
                result.append(escape("_"))
                index += 1
                continue
            if char == "`":
                close = segment.find("`", index + 1)
                if close != -1:
                    code_text = segment[index + 1 : close]
                    result.append(f"<code>{escape(code_text)}</code>")
                    index = close + 1
                    continue
                result.append(escape("`"))
                index += 1
                continue
            if char in {"@", "["}:
                match = _MENTION_PATTERN.match(segment, index)
                if match:
                    name = match.group("bracket") or match.group("at") or ""
                    element, fallback = _build_mention_element(name)
                    if element is not None:
                        result.append(tostring(element, encoding="unicode", method="html"))
                    else:
                        result.append(escape(fallback))
                    index = match.end()
                    continue
            result.append(escape(char))
            index += 1
        return "".join(result)

    return _render_segment(text or "")


@register.filter(name="format_post")
def format_post(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)
    lines = text.splitlines()
    html_parts: list[str] = []
    total_lines = len(lines)
    pointer = 0

    def _consume_blank(idx: int) -> int:
        while idx < total_lines and not lines[idx].strip():
            idx += 1
        return idx

    pointer = _consume_blank(pointer)
    while pointer < total_lines:
        line = lines[pointer]
        stripped = line.strip()

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            pointer += 1
            code_lines: list[str] = []
            while pointer < total_lines:
                current = lines[pointer]
                if current.strip().startswith("```"):
                    pointer += 1
                    break
                code_lines.append(current)
                pointer += 1
            lang_attr = f' class="language-{escape(language)}"' if language else ""
            code_html = escape("\n".join(code_lines))
            html_parts.append(f"<pre><code{lang_attr}>{code_html}</code></pre>")
            pointer = _consume_blank(pointer)
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while pointer < total_lines:
                current = lines[pointer]
                current_stripped = current.strip()
                if current_stripped.startswith(">"):
                    quote_lines.append(current_stripped[1:].lstrip())
                    pointer += 1
                    continue
                break
            quote_text = "\n".join(quote_lines)
            html_parts.append(f"<blockquote>{_render_inline_markup(quote_text)}</blockquote>")
            pointer = _consume_blank(pointer)
            continue

        if stripped.startswith("- "):
            items: list[str] = []
            while pointer < total_lines:
                current = lines[pointer]
                current_stripped = current.strip()
                if current_stripped.startswith("- "):
                    items.append(current_stripped[2:])
                    pointer += 1
                    continue
                break
            if items:
                item_html = "".join(f"<li>{_render_inline_markup(item)}</li>" for item in items)
                html_parts.append(f"<ul>{item_html}</ul>")
            pointer = _consume_blank(pointer)
            continue

        paragraph_lines: list[str] = []
        while pointer < total_lines:
            current = lines[pointer]
            current_stripped = current.strip()
            if not current_stripped:
                pointer += 1
                break
            if current_stripped.startswith(("```", ">", "- ")):
                break
            paragraph_lines.append(current)
            pointer += 1
        paragraph_text = "\n".join(paragraph_lines)
        html_parts.append(f"<p>{_render_inline_markup(paragraph_text)}</p>")
        pointer = _consume_blank(pointer)

    if not html_parts:
        return ""

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
