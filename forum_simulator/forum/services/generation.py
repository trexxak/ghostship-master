from __future__ import annotations

import logging
import random
import re
from datetime import timedelta
from typing import Any, Optional

from django.conf import settings
from django.db import models, transaction, connections, OperationalError, ProgrammingError
from django.utils import timezone

from forum.models import Agent, GenerationTask, Post, PrivateMessage, Thread, ModerationTicket, OrganicInteractionLog
from forum.openrouter import DEFAULT_MAX_TOKENS, generate_completion, remaining_requests
from forum.services import configuration as config_service

logger = logging.getLogger(__name__)

QUEUE_BATCH_SIZE = getattr(settings, "GENERATION_QUEUE_BATCH_SIZE", 3)
RETRY_DELAY_SECONDS = getattr(settings, "GENERATION_RETRY_DELAY_SECONDS", 60)
MEMORY_MAX = getattr(settings, "AGENT_MEMORY_MAX_ENTRIES", 12)
PEER_MEMORY_MAX = getattr(settings, "AGENT_PEER_MEMORY_MAX_ENTRIES", 4)
PEER_TRACK_MAX = getattr(settings, "AGENT_PEER_MEMORY_MAX_PEERS", 12)
THREAD_MEMORY_MAX = getattr(settings, "AGENT_THREAD_MEMORY_MAX_ENTRIES", 6)
THREAD_TRACK_MAX = getattr(settings, "AGENT_THREAD_MEMORY_MAX_THREADS", 20)
MENTION_TOKEN_PATTERN = re.compile(r"@([A-Za-z0-9_.-]{2,})|\[([A-Za-z0-9_.-]{2,})\]")
BATCHABLE_TYPES = {
    GenerationTask.TYPE_REPLY,
    GenerationTask.TYPE_DM,
}


def _normalize_agent_memory(raw: Any) -> dict[str, Any]:
    """Ensure the agent memory follows the structured schema."""

    if not raw:
        return {"global": [], "peers": {}, "threads": {}}

    if isinstance(raw, list):
        trimmed = [entry for entry in raw if entry][-MEMORY_MAX:]
        return {"global": trimmed, "peers": {}, "threads": {}}

    if isinstance(raw, dict):
        global_entries = raw.get("global") or []
        if not isinstance(global_entries, list):
            global_entries = []
        else:
            global_entries = [entry for entry in global_entries if entry][-MEMORY_MAX:]

        peers_raw = raw.get("peers") or {}
        peers: dict[str, dict[str, Any]] = {}
        if isinstance(peers_raw, dict):
            for key, info in peers_raw.items():
                if not isinstance(info, dict):
                    continue
                notes = info.get("notes") or info.get("memory") or []
                if not isinstance(notes, list):
                    notes = []
                peers[str(key)] = {
                    "handle": info.get("handle"),
                    "notes": [note for note in notes if note][-MEMORY_MAX:],
                    "last_seen": info.get("last_seen"),
                }
        threads_raw = raw.get("threads") or {}
        threads: dict[str, dict[str, Any]] = {}
        if isinstance(threads_raw, dict):
            for key, info in threads_raw.items():
                if not isinstance(info, dict):
                    continue
                notes = info.get("notes") or []
                if not isinstance(notes, list):
                    notes = []
                threads[str(key)] = {
                    "title": info.get("title"),
                    "topics": info.get("topics"),
                    "notes": [note for note in notes if note][-THREAD_MEMORY_MAX:],
                    "last_seen": info.get("last_seen"),
                }
        return {"global": global_entries, "peers": peers, "threads": threads}

    return {"global": [], "peers": {}, "threads": {}}


def _format_memory_snippet(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry[:160]
    if isinstance(entry, dict):
        summary = (entry.get("summary") or entry.get("text") or "").strip()
        thread_title = (entry.get("thread_title") or "").strip()
        channel = (entry.get("channel") or "").strip()
        topic_bits = entry.get("topics") or []
        parts: list[str] = []
        if summary:
            parts.append(summary[:160])
        if thread_title:
            parts.append(f"in {thread_title}")
        if channel and not thread_title:
            parts.append(channel)
        if topic_bits:
            if isinstance(topic_bits, list):
                trimmed_topics = ", ".join(str(bit) for bit in topic_bits[:3])
                if trimmed_topics:
                    parts.append(f"topics: {trimmed_topics}")
            elif isinstance(topic_bits, str):
                parts.append(f"topics: {topic_bits}")
        snippet = "; ".join(part for part in parts if part)
        return snippet or None
    return None


def _render_peer_memories(memory: dict[str, Any], participant_ids: set[int], labels: dict[int, str]) -> list[str]:
    peers = memory.get("peers") or {}
    if not isinstance(peers, dict):
        return []

    rendered: list[str] = []
    for pid in participant_ids:
        peer_info = peers.get(str(pid))
        if not peer_info or not isinstance(peer_info, dict):
            continue
        notes = peer_info.get("notes") or []
        if not isinstance(notes, list) or not notes:
            continue
        handle = peer_info.get("handle") or labels.get(pid) or f"user {pid}"
        snippets = []
        for note in notes[-PEER_MEMORY_MAX:]:
            snippet = _format_memory_snippet(note)
            if snippet:
                snippets.append(snippet)
        if not snippets:
            continue
        rendered.append(f"- {handle}: " + " | ".join(snippets))
    return rendered[:3]


def _render_thread_memories(memory: dict[str, Any], thread_id: int | None) -> list[str]:
    if thread_id is None:
        return []
    threads = memory.get("threads") or {}
    if not isinstance(threads, dict):
        return []

    thread_entry = threads.get(str(thread_id))
    if not thread_entry or not isinstance(thread_entry, dict):
        return []

    notes = thread_entry.get("notes") or []
    if not isinstance(notes, list):
        return []

    rendered: list[str] = []
    for note in notes[-THREAD_MEMORY_MAX:]:
        snippet = _format_memory_snippet(note)
        if snippet:
            rendered.append(f"- {snippet}")
    if not rendered:
        return []

    title = thread_entry.get("title")
    if title:
        header = f"Thread memories from '{title}':"
    else:
        header = "Thread memories:"
    return [header, *rendered[:4]]

def _table_exists(table: str, using: str = "default") -> bool:
    try:
        return table in connections[using].introspection.table_names()
    except Exception:  # noqa: BLE001
        return False

def _batch_size_limit() -> int:
    configured = config_service.get_int("GENERATION_BATCH_SIZE", 3)
    try:
        limit = int(configured)
    except (TypeError, ValueError):
        limit = 3
    return max(1, limit)


class DuplicateContentError(RuntimeError):
    """Raised when generated text is too similar to recent content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _canonical_handle(name: str | None) -> str | None:
    if not name:
        return None
    agent = Agent.objects.filter(name__iexact=name).only("name").first()
    if not agent:
        return None
    return agent.name


def _format_post_excerpt(post: Post, *, include_author: bool = True) -> str | None:
    content = (getattr(post, "content", "") or "").replace("\n", " ").replace("\r", " ").strip()
    if not content:
        return None
    snippet = content[:160]
    if not include_author:
        return snippet
    author = getattr(post, "author", None)
    author_name = getattr(author, "name", None) or "unknown"
    return f"[{author_name}] {snippet}"


def _sanitize_mentions(task: GenerationTask, content: str) -> str:
    if not content:
        return content

    agent_name = ""
    if task.agent and getattr(task.agent, "name", None):
        agent_name = task.agent.name.lower()

    handles = {
        token
        for match in MENTION_TOKEN_PATTERN.finditer(content)
        if (token := (match.group(1) or match.group(2) or "").strip())
    }
    if not handles:
        return content

    canonical_map: dict[str, str] = {}
    for handle in handles:
        canonical = _canonical_handle(handle)
        if canonical:
            canonical_map[handle.lower()] = canonical

    def _replace(match: re.Match[str]) -> str:
        raw = (match.group(1) or match.group(2) or "").strip()
        if not raw:
            return match.group(0)
        lowered = raw.lower()
        canonical = canonical_map.get(lowered)
        if agent_name and lowered == agent_name:
            return canonical or raw
        if canonical:
            return f"@{canonical}"
        return raw

    return MENTION_TOKEN_PATTERN.sub(_replace, content)


def _sample_post_length(agent: Agent, *, rng: Optional[random.Random] = None) -> dict[str, Any]:
    sampler = rng or random
    profile = getattr(agent, "speech_profile", None) or {}
    min_words = max(6, int(profile.get("min_words", 16)))
    max_words = int(profile.get("max_words", max(min_words + 4, 34)))
    if max_words <= min_words:
        max_words = min_words + 4
    mean_words = int(profile.get("mean_words", (min_words + max_words) // 2))
    mean_words = max(min_words, min(max_words, mean_words))
    sentence_range = profile.get("sentence_range") or [1, 3]
    sentence_low = max(1, int(sentence_range[0]))
    sentence_high = max(sentence_low, int(sentence_range[1]) if len(sentence_range) > 1 else sentence_low)
    burst_range = profile.get("burst_range") or [6, min(max_words, max(6, min_words + 6))]
    burst_min = max(3, int(burst_range[0]))
    burst_max = max(burst_min + 1, int(burst_range[1]))
    burst_chance = float(profile.get("burst_chance", 0.18))
    burst_chance = min(max(burst_chance, 0.0), 0.5)
    burst_roll = sampler.random()
    if burst_roll < burst_chance:
        target_words = sampler.randint(burst_min, min(max_words, burst_max))
        burst = True
    else:
        spread = max(2.0, (max_words - min_words) / 3.0)
        sample = int(round(sampler.gauss(mean_words, spread)))
        target_words = max(min_words, min(max_words, sample))
        burst = False
    target_sentences = sampler.randint(sentence_low, sentence_high)
    return {"words": target_words, "sentences": target_sentences, "burst": burst}


def _format_length_instruction(length_hint: dict[str, Any]) -> str:
    sentences = max(1, int(length_hint.get("sentences", 1)))
    words = max(4, int(length_hint.get("words", 18)))
    sentence_label = "sentence" if sentences == 1 else "sentences"
    return f"Aim for roughly {words} words across {sentences} {sentence_label}."


def enqueue_generation_task(
    *,
    task_type: str,
    agent: Agent,
    thread: Optional[Thread] = None,
    recipient: Optional[Agent] = None,
    payload: Optional[dict] = None,
) -> GenerationTask:
    return GenerationTask.objects.create(
        task_type=task_type,
        agent=agent,
        thread=thread,
        recipient=recipient,
        payload=payload or {},
    )


def _queue_limit() -> int:
    configured = config_service.get_int("GENERATION_QUEUE_LIMIT", QUEUE_BATCH_SIZE)
    try:
        value = int(configured)
    except (TypeError, ValueError):
        value = QUEUE_BATCH_SIZE
    return max(1, value)


def process_generation_queue(*, limit: Optional[int] = None) -> tuple[int, int]:
    limit = limit or _queue_limit()
    table = GenerationTask._meta.db_table
    if not _table_exists(table):
        return 0, 0

    now = timezone.now()
    try:
        task_qs = (
            GenerationTask.objects.select_related("agent", "thread", "recipient")
            .filter(status=GenerationTask.STATUS_PENDING)
            .filter(models.Q(scheduled_for__isnull=True) | models.Q(scheduled_for__lte=now))
            .order_by("created_at")[:limit]
        )
        tasks = list(task_qs)
    except (OperationalError, ProgrammingError):
        return 0, 0

    processed = 0
    deferred = 0

    idx = 0
    total = len(tasks)
    batch_limit = _batch_size_limit()

    while idx < total:
        batch = _slice_batch(tasks, idx, batch_limit)
        completed, delayed = _process_task_batch(batch)
        processed += completed
        deferred += delayed
        idx += len(batch)

    return processed, deferred


def _process_single_task(task: GenerationTask) -> bool:
    skip_reason = _skip_reason(task)
    if skip_reason:
        logger.info("Skipping task %s: %s", task.id, skip_reason)
        _complete_without_output(task, skip_reason)
        return True
    try:
        _mark_task_processing(task)
        result = _generate_payload(task)
        return _handle_generation_result(task, result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Generation task %s failed", task.id)
        _defer_task(task, str(exc))
        return False


def _generate_payload(task: GenerationTask) -> Optional[dict]:
    if remaining_requests() <= 0:
        logger.info(
            "OpenRouter quota exhausted or unavailable; using fallback for task %s", task.id)
        return {"success": False, "text": _fallback_for_task(task), "response": None}

    prompt = _build_prompt(task)
    result = generate_completion(
        prompt,
        max_tokens=task.payload.get("max_tokens", DEFAULT_MAX_TOKENS),
        temperature=task.payload.get("temperature", 0.7),
        metadata={"task_id": task.id, "task_type": task.task_type},
    )
    if not result or not (result.get("text") or "").strip():
        result = {"success": False, "text": _fallback_for_task(
            task), "response": None}
    return result


def _build_prompt(task: GenerationTask) -> str:
    agent = task.agent
    mood = getattr(agent, "mood", "neutral").lower()
    archetype = getattr(agent, "archetype", "ghost").lower()
    # Relax strict persona scaffolding: provide gentle guidance rather than forced name-mentions
    persona_bits = [
        f"Participant profile: name={agent.name if getattr(agent, 'name', None) else 'unknown'}, archetype={archetype}, mood={mood}.",
        "Use these attributes as guidance for tone and perspective; avoid repeating the agent's handle unless it reads naturally.",
        "When referring to yourself or any other ghost, write the handle with an @ prefix (e.g. @trexxak) instead of leading the post with 'Name -'.",
        "Write like an engaged forum regular who mixes receipts with reactions and the occasional aside.",
        "Let a little personality leak through—curiosity, humor, or concern are welcome as long as the evidence stays clear.",
        "Prefer grounded, evidence-focused language rather than retro web slang or forced nostalgia.",
    ]

    mind_state = agent.mind_state if isinstance(agent.mind_state, dict) else {}
    signature_hint = mind_state.get("persona_signature") or mind_state.get("signature")
    if signature_hint:
        persona_bits.append(f"Voice sample: {signature_hint}")

    memory_state = _normalize_agent_memory(agent.memory)
    needs = agent.needs or {}
    if needs:
        focused = sorted(
            needs.items(), key=lambda item: item[1], reverse=True)[:3]
        persona_bits.append(
            "Key drives: " + ", ".join(f"{k} {v:.2f}" for k, v in focused))

    context: list[str] = persona_bits
    global_memories = memory_state.get("global") or []
    rendered_global: list[str] = []
    for memo in global_memories[-3:]:
        snippet = _format_memory_snippet(memo)
        if snippet:
            rendered_global.append(f"- {snippet}")
    if rendered_global:
        context.append("Things you still remember:")
        context.extend(rendered_global)

    mentionable: list[str] = []
    length_hint: dict[str, Any] | None = None

    agent_handle_lower = ""
    if getattr(agent, "name", None):
        agent_handle_lower = agent.name.lower()

    participant_ids: set[int] = set()
    participant_labels: dict[int, str] = {}
    if task.recipient_id and getattr(task, "recipient", None):
        participant_ids.add(task.recipient_id)
        if getattr(task.recipient, "name", None):
            participant_labels[task.recipient_id] = task.recipient.name

    if task.thread:
        exclude_post_id = task.payload.get("exclude_post_id")
        context.append(f"Thread title: {task.thread.title}")
        base_posts = Post.objects.filter(thread=task.thread, is_placeholder=False).exclude(
            id=exclude_post_id
        )

        recent_posts_qs = base_posts.select_related("author").order_by("-created_at")[:3]
        recent_posts = list(recent_posts_qs)

        opener_post = (
            base_posts.select_related("author").order_by("created_at").first()
        )
        if opener_post:
            opener_excerpt = _format_post_excerpt(opener_post)
            if opener_excerpt:
                context.append("Thread opener:")
                context.append(opener_excerpt)
            if opener_post.author_id and opener_post.author_id != getattr(agent, "id", None):
                participant_ids.add(opener_post.author_id)
                if opener_post.author and getattr(opener_post.author, "name", None):
                    participant_labels[opener_post.author_id] = opener_post.author.name

        recent_quotes: list[str] = []
        for post in reversed(recent_posts):
            excerpt = _format_post_excerpt(post)
            if excerpt:
                recent_quotes.append(excerpt)
            if post.author_id and post.author_id != getattr(agent, "id", None):
                participant_ids.add(post.author_id)
                if post.author and getattr(post.author, "name", None):
                    participant_labels[post.author_id] = post.author.name
        if recent_quotes:
            context.append("Recent comments:")
            context.extend(recent_quotes)

        timeline_posts: list[Post] = []
        recent_ids = {post.id for post in recent_posts}
        if opener_post:
            recent_ids.add(opener_post.id)
        timeline_candidates = base_posts.select_related("author").order_by("created_at")
        for post in timeline_candidates:
            if post.id in recent_ids:
                continue
            timeline_posts.append(post)
            if len(timeline_posts) >= 3:
                break
        if timeline_posts:
            context.append("Earlier thread highlights:")
            for post in timeline_posts:
                excerpt = _format_post_excerpt(post)
                if excerpt:
                    context.append(f"- {excerpt}")
                if post.author_id and post.author_id != getattr(agent, "id", None):
                    participant_ids.add(post.author_id)
                    if post.author and getattr(post.author, "name", None):
                        participant_labels[post.author_id] = post.author.name

        topics = task.payload.get("topics") or getattr(
            task.thread, "topics", []) or []
        if topics:
            context.append("Topics: " + ", ".join(topics))

        thread_memories = _render_thread_memories(memory_state, task.thread_id)
        if thread_memories:
            context.extend(thread_memories)
            context.append(
                "Fold those remembered beats into your reply so it tracks with where the thread currently sits."
            )

        mentionable_handles: set[str] = set()
        handle_to_excerpt: dict[str, str | None] = {}
        thread_author = getattr(task.thread, "author", None)
        if thread_author and getattr(thread_author, "name", None):
            if thread_author.id and thread_author.id != getattr(agent, "id", None):
                participant_ids.add(thread_author.id)
                participant_labels.setdefault(thread_author.id, thread_author.name)
            if not agent_handle_lower or thread_author.name.lower() != agent_handle_lower:
                mentionable_handles.add(thread_author.name)
                if thread_author.name not in handle_to_excerpt and opener_post and opener_post.author_id == thread_author.id:
                    handle_to_excerpt[thread_author.name] = _format_post_excerpt(opener_post, include_author=False)
        for post in recent_posts:
            if post.author_id and getattr(post.author, "name", None):
                name = post.author.name
                if not agent_handle_lower or name.lower() != agent_handle_lower:
                    mentionable_handles.add(name)
                    handle_to_excerpt.setdefault(name, _format_post_excerpt(post, include_author=False))
        for post in timeline_posts:
            if post.author_id and getattr(post.author, "name", None):
                name = post.author.name
                if not agent_handle_lower or name.lower() != agent_handle_lower:
                    mentionable_handles.add(name)
                    handle_to_excerpt.setdefault(name, _format_post_excerpt(post, include_author=False))
        payload_handles = task.payload.get("mention_whitelist") or []
        for handle in payload_handles:
            canonical = _canonical_handle(str(handle))
            if canonical and canonical.lower() != agent_handle_lower:
                mentionable_handles.add(canonical)
        mentionable = sorted(name for name in mentionable_handles if name)
        if mentionable:
            context.append("Mentionable ghosts and receipts:")
            for handle in mentionable:
                excerpt = handle_to_excerpt.get(handle)
                if excerpt:
                    context.append(f"- @{handle}: {excerpt}")
                else:
                    context.append(
                        f"- @{handle}: no fresh post excerpt available—reference prior intel if you name them."
                    )
            context.append(
                "Mention only if you are directly responding to that ghost—otherwise let the update stand without a tag."
            )
        else:
            context.append(
                "You are not obligated to tag anyone here; share the update in your own voice unless a direct reply is needed."
            )

        theme = task.payload.get("theme")
        if theme:
            context.append(f"Thread theme: {theme}.")

        setting = task.payload.get("setting")
        if setting:
            context.append(f"Setting or vibe: {setting}.")

        tone_hint = task.payload.get("tone")
        if tone_hint:
            context.append(f"Tone guidance: {tone_hint}.")

        style_notes = task.payload.get("style_notes")
        if style_notes:
            context.append(f"Style notes: {style_notes}")

        body_guidance = task.payload.get("body_guidance")
        if body_guidance:
            context.append(f"Body guidance: {body_guidance}")

        if task.payload.get("seeded"):
            context.append(
                "This is the first reply: acknowledge the opener, add one fresh detail about the organic, and invite follow-up evidence.")

        last_post = recent_posts[0] if recent_posts else None
        if last_post and last_post.author_id == agent.id:
            context.append(
                "You authored the most recent comment—open with a light nod to avoid double-posting, or skip it only if it would distract from new intel."
            )

    peer_memories = _render_peer_memories(memory_state, participant_ids, participant_labels)
    if peer_memories:
        context.append("Shared history cues:")
        context.extend(peer_memories)

    if task.task_type == GenerationTask.TYPE_REPLY:
        context.append(
            "Anchor the reply in the organic being discussed and bring a new observation or pointed question.")
        context.append(
            "Blend the intel with at least one natural-sounding reaction so it feels like a live forum reply, not a diagnostic log.")
        if mentionable:
            context.append(
                "If you tag another ghost, choose from the mentionable list and explain why they matter here.")
        length_hint = _sample_post_length(agent)
        context.append(_format_length_instruction(length_hint))
        if length_hint.get("burst"):
            context.append("Keep this one extra punchy—deliver the insight in a quick burst and bail early.")
        else:
            context.append("Balance brevity with clarity: land the evidence, then give a short reaction or invitation.")
    elif task.task_type == GenerationTask.TYPE_THREAD_START:
        context.append(
            "Frame the situation clearly and point to the evidence or questions that kicked off this watch.")
        context.append(
            "Sound like you're opening a real discussion—set the stakes, add a quick personal hook, and welcome others in.")
    elif task.task_type == GenerationTask.TYPE_DM:
        context.append(
            "Keep the tone direct and collaborative while swapping actionable intel.")

    context.append(
        "Stay precise, cite the organic event, and avoid recycled jokes or filler.")

    instruction = task.payload.get("instruction") or _default_instruction(task)
    prompt = "\n".join(line for line in context if line)
    prompt += (
        f"\n\nInstruction: {instruction}\n"
        f"Keep it concise and specific. Respond in <= {task.payload.get('max_tokens', DEFAULT_MAX_TOKENS)} tokens."
    )
    return prompt


def _default_instruction(task: GenerationTask) -> str:
    if task.task_type == GenerationTask.TYPE_REPLY:
        return "Write a reply that riffs on the organic being discussed through your persona."
    if task.task_type == GenerationTask.TYPE_DM:
        return "Compose a quick private message that swaps organics intel or coordinates next steps."
    if task.task_type == GenerationTask.TYPE_THREAD_START:
        return "Draft the opening post that frames the organic topic and sets the old-web vibe."
    return "Provide forum text."


def _persist_output(task: GenerationTask, content: str, *, is_placeholder: bool = False) -> None:
    payload = task.payload or {}
    tick_number = payload.get("tick_number")

    # If content is empty, we'll surface this to moderation/escalation elsewhere.

    if task.task_type == GenerationTask.TYPE_THREAD_START and task.thread:
        thread = task.thread
        placeholder = (
            Post.objects.filter(thread=thread, author=task.agent, is_placeholder=True)
            .order_by("created_at")
            .first()
        )
        if is_placeholder:
            if placeholder:
                placeholder.content = content
                placeholder.tick_number = tick_number
                placeholder.save(update_fields=["content", "tick_number"])
            else:
                Post.objects.create(
                    thread=thread,
                    author=task.agent,
                    content=content,
                    tick_number=tick_number,
                    is_placeholder=True,
                )
            return
        if placeholder:
            placeholder.content = content
            placeholder.tick_number = tick_number
            placeholder.is_placeholder = False
            placeholder.created_at = timezone.now()
            placeholder.save(update_fields=["content", "tick_number", "is_placeholder", "created_at"])
            post = placeholder
        else:
            post = Post.objects.create(
                thread=thread,
                author=task.agent,
                content=content,
                tick_number=tick_number,
            )
        thread = task.thread
        thread.heat = max(thread.heat, 1.0) if thread.heat is not None else 1.0
        thread.touch(activity=post.created_at, bump_heat=1.2, auto_save=False)
        thread.save(update_fields=["heat", "last_activity_at", "hot_score"])
    elif task.task_type == GenerationTask.TYPE_REPLY and task.thread:
        thread = task.thread
        placeholder = (
            Post.objects.filter(thread=thread, author=task.agent, is_placeholder=True)
            .order_by("created_at")
            .first()
        )
        if is_placeholder:
            if placeholder:
                placeholder.content = content
                placeholder.tick_number = tick_number
                placeholder.save(update_fields=["content", "tick_number"])
            else:
                Post.objects.create(
                    thread=thread,
                    author=task.agent,
                    content=content,
                    tick_number=tick_number,
                    is_placeholder=True,
                )
            return
        if placeholder:
            placeholder.content = content
            placeholder.tick_number = tick_number
            placeholder.is_placeholder = False
            placeholder.created_at = timezone.now()
            placeholder.save(update_fields=["content", "tick_number", "is_placeholder", "created_at"])
            post = placeholder
        else:
            post = Post.objects.create(
                thread=thread,
                author=task.agent,
                content=content,
                tick_number=tick_number,
            )
        thread = task.thread
        thread.heat = (thread.heat or 0) + 1
        thread.touch(activity=post.created_at, bump_heat=0.6, auto_save=False)
        thread.save(update_fields=["heat", "last_activity_at", "hot_score"])
    elif task.task_type == GenerationTask.TYPE_DM and task.recipient:
        PrivateMessage.objects.create(
            sender=task.agent,
            recipient=task.recipient,
            content=content,
            tick_number=tick_number,
            authored_by_operator=False,
        )
        return


def _post_process_output(task: GenerationTask, content: str) -> tuple[bool, str | None]:
    """Simple post-processor that rejects near-duplicates or verbatim echoes of recent context.

    Returns (accepted, reason). If rejected, caller should reschedule with stricter instruction.
    """
    # Reject exact echoes of the fallback or prompt-instruction
    if not content or not content.strip():
        return False, "empty"

    # Check against most recent post(s) in the thread
    if task.thread:
        recent = list(
            Post.objects.filter(thread=task.thread, is_placeholder=False)
            .order_by('-created_at')[:2]
        )
        for post in recent:
            a = (post.content or '').strip()
            b = content.strip()
            if not a:
                continue
            # simple heuristics: exact match or >70% overlap by token intersection
            if a == b:
                return False, "verbatim duplicate"
            a_tokens = set(x.lower() for x in a.split())
            b_tokens = set(x.lower() for x in b.split())
            if a_tokens and b_tokens:
                overlap = len(a_tokens & b_tokens) / \
                    max(1, min(len(a_tokens), len(b_tokens)))
                if overlap >= 0.7:
                    return False, "substantial overlap with recent post"

    # Check agent memory for repeated tropes
    agent = task.agent
    memory_state = _normalize_agent_memory(agent.memory)
    memory = memory_state.get("global") or []
    summary = content[:200]
    repeats = sum(1 for entry in memory[-6:] if isinstance(entry, dict) and (entry.get(
        'summary') or '') and (entry.get('summary') in summary or summary in (entry.get('summary') or '')))
    if repeats >= 2:
        # Penalise repetition by raising a soft failure
        try:
            agent.suspicion_score = float(
                getattr(agent, 'suspicion_score', 0.0)) + 0.05
            agent.save(update_fields=['suspicion_score', 'updated_at'])
        except Exception:
            pass
        return False, 'repeated trope in agent memory'

    return True, None


def _reschedule_with_stricter_instruction(task: GenerationTask, reason: str) -> None:
    """Reschedule the task with a clearer, stricter instruction to avoid echoing or duplication."""
    payload = dict(task.payload or {})
    old_instr = payload.get('instruction') or ''
    payload['instruction'] = (
        "(RETRY - avoid repeating existing content) "
        + "Be concise, do not quote the prompt verbatim, and add at least one new, specific observation. "
        + old_instr
    )
    task.payload = payload
    task.attempts = (task.attempts or 0) + 1
    task.last_error = f"Rescheduled: {reason}"
    from django.utils import timezone

    task.scheduled_for = timezone.now() + timedelta(seconds=RETRY_DELAY_SECONDS)
    task.status = GenerationTask.STATUS_DEFERRED
    task.save(update_fields=['payload', 'attempts',
              'last_error', 'scheduled_for', 'status', 'updated_at'])


def _peer_targets_for_task(task: GenerationTask) -> list[tuple[int, dict[str, Any]]]:
    peers: list[tuple[int, dict[str, Any]]] = []

    if task.recipient_id and getattr(task, "recipient", None):
        peers.append(
            (
                task.recipient_id,
                {
                    "handle": getattr(task.recipient, "name", None),
                    "channel": "direct message",
                    "topics": task.payload.get("topics") if isinstance(task.payload, dict) else None,
                },
            )
        )

    if task.thread_id and getattr(task, "thread", None):
        thread_title = getattr(task.thread, "title", None)
        topics = task.payload.get("topics") if isinstance(task.payload, dict) else None
        seen: set[int] = set()
        thread_author_id = getattr(task.thread, "author_id", None)
        if thread_author_id and thread_author_id != getattr(task.agent, "id", None):
            seen.add(thread_author_id)
            peers.append(
                (
                    thread_author_id,
                    {
                        "handle": getattr(getattr(task.thread, "author", None), "name", None),
                        "channel": "thread reply",
                        "thread_title": thread_title,
                        "topics": topics,
                    },
                )
            )

        recent_posts = (
            Post.objects.filter(thread_id=task.thread_id)
            .exclude(author_id=getattr(task.agent, "id", None))
            .select_related("author")
            .order_by("-created_at")[:6]
        )
        for post in recent_posts:
            if not post.author_id or post.author_id in seen:
                continue
            seen.add(post.author_id)
            peers.append(
                (
                    post.author_id,
                    {
                        "handle": getattr(post.author, "name", None),
                        "channel": "thread reply",
                        "thread_title": thread_title,
                        "topics": topics,
                    },
                )
            )

    return peers


def _update_agent_memory(task: GenerationTask, content: str) -> None:
    agent = task.agent
    memory = _normalize_agent_memory(agent.memory)

    timestamp = timezone.now().isoformat()
    entry = {
        "ts": timestamp,
        "task": task.task_type,
        "thread": task.thread_id,
        "recipient": task.recipient_id,
        "summary": content[:200],
    }

    global_entries = memory.setdefault("global", [])
    global_entries.append(entry)
    memory["global"] = [item for item in global_entries if item][-MEMORY_MAX:]

    peers = memory.setdefault("peers", {})
    threads = memory.setdefault("threads", {})
    thread_handles: list[str] = []
    for peer_id, metadata in _peer_targets_for_task(task):
        key = str(peer_id)
        note = {
            "ts": timestamp,
            "summary": content[:200],
            "thread": task.thread_id,
            "thread_title": metadata.get("thread_title"),
            "channel": metadata.get("channel"),
        }
        topics = metadata.get("topics")
        if topics:
            note["topics"] = topics

        peer_entry = peers.get(key) if isinstance(peers.get(key), dict) else {}
        notes = list(peer_entry.get("notes") or [])
        notes.append(note)
        peer_entry["notes"] = [item for item in notes if item][-max(1, PEER_MEMORY_MAX):]
        peer_entry["handle"] = metadata.get("handle") or peer_entry.get("handle")
        peer_entry["last_seen"] = timestamp
        peers[key] = peer_entry
        handle = metadata.get("handle")
        if handle and handle not in thread_handles:
            thread_handles.append(handle)

    if len(peers) > PEER_TRACK_MAX:
        sorted_peers = sorted(
            peers.items(),
            key=lambda item: item[1].get("last_seen") if isinstance(item[1], dict) else "",
            reverse=True,
        )
        memory["peers"] = dict(sorted_peers[:PEER_TRACK_MAX])

    if task.thread_id:
        key = str(task.thread_id)
        thread_entry = threads.get(key) if isinstance(threads.get(key), dict) else {}
        notes = list(thread_entry.get("notes") or [])
        note: dict[str, Any] = {
            "ts": timestamp,
            "summary": content[:200],
            "thread_title": getattr(task.thread, "title", None),
        }
        topics = None
        if isinstance(task.payload, dict):
            topics = task.payload.get("topics")
        if not topics and getattr(task.thread, "topics", None):
            topics = getattr(task.thread, "topics")
        if topics:
            note["topics"] = topics
        if thread_handles:
            note["participants"] = thread_handles
        notes.append(note)
        thread_entry["notes"] = [item for item in notes if item][-max(1, THREAD_MEMORY_MAX):]
        if getattr(task.thread, "title", None):
            thread_entry["title"] = getattr(task.thread, "title", None)
        if topics:
            thread_entry["topics"] = topics
        thread_entry["last_seen"] = timestamp
        threads[key] = thread_entry

    if len(threads) > THREAD_TRACK_MAX:
        sorted_threads = sorted(
            threads.items(),
            key=lambda item: item[1].get("last_seen") if isinstance(item[1], dict) else "",
            reverse=True,
        )
        memory["threads"] = dict(sorted_threads[:THREAD_TRACK_MAX])

    agent.memory = memory
    agent.save(update_fields=["memory", "updated_at"])


def _skip_reason(task: GenerationTask) -> Optional[str]:
    agent = task.agent
    if hasattr(agent, "is_banned") and agent.is_banned():
        return "agent banned"
    if hasattr(agent, "is_organic") and agent.is_organic():
        try:
            OrganicInteractionLog.record(
                agent=agent,
                action=OrganicInteractionLog.ACTION_AUTOMATION_BLOCKED,
                thread=task.thread,
                recipient=task.recipient,
                content=f"[automation:{task.task_type}]",
                metadata={
                    "task_id": task.id,
                    "reason": "organic_guardrail",
                },
            )
        except Exception:
            # Logging should not block the guardrail.
            pass
        return "organic_interface_guardrail"
    if task.thread and getattr(task.thread, "locked", False) and task.task_type != GenerationTask.TYPE_DM:
        return "thread locked"
    return None


def _complete_without_output(task: GenerationTask, reason: str) -> None:
    task.status = GenerationTask.STATUS_COMPLETED
    task.response_text = f"(skipped: {reason})"
    task.completed_at = timezone.now()
    task.save(update_fields=["status", "response_text",
              "completed_at", "updated_at"])


def _fallback_for_task(task: GenerationTask) -> str:
    thread_title = task.thread.title if task.thread else "the forum"
    mood = getattr(task.agent, "mood", "neutral")
    archetype = getattr(task.agent, "archetype", "ghost")
    if task.task_type == GenerationTask.TYPE_THREAD_START:
        topics = ", ".join(task.payload.get("topics", [])
                           ) or "a favorite organic"
        return (
            f"Opening post for '{thread_title}'. Stay in-character as a {archetype.lower()} ghost in a {mood} mood; frame it like an old-web bulletin about {topics} and invite other ghosts to drop receipts."
        )
    if task.task_type == GenerationTask.TYPE_REPLY:
        return (
            f"Reply in '{thread_title}' as a {archetype.lower()} ghost. Reference the organic, add fresh evidence, and keep the tone {mood}."
        )
    if task.task_type == GenerationTask.TYPE_DM:
        recipient = task.recipient.name if task.recipient else "their counterpart"
        return (
            f"Write a private message to {recipient} that swaps organics intel in a quick {mood} voice."
        )
    return "Share a short ghostship note about today's organic activity."


def _defer_task(task: GenerationTask, reason: str) -> None:
    task.status = GenerationTask.STATUS_DEFERRED
    task.last_error = reason
    task.scheduled_for = timezone.now() + timedelta(seconds=RETRY_DELAY_SECONDS)
    task.save(update_fields=["status", "last_error",
              "scheduled_for", "updated_at"])
def _slice_batch(tasks: list[GenerationTask], start: int, batch_limit: int) -> list[GenerationTask]:
    """Return a slice of tasks sharing a batchable type."""
    head = tasks[start]
    if head.task_type not in BATCHABLE_TYPES or batch_limit <= 1:
        return [head]
    batch: list[GenerationTask] = [head]
    upper = min(start + batch_limit, len(tasks))
    for idx in range(start + 1, upper):
        candidate = tasks[idx]
        if candidate.task_type != head.task_type:
            break
        batch.append(candidate)
    return batch


def _process_task_batch(tasks: list[GenerationTask]) -> tuple[int, int]:
    """Process a batch of tasks, optionally generating them in a single LLM call."""
    if not tasks:
        return 0, 0

    processed = 0
    deferred = 0
    ready: list[GenerationTask] = []

    for task in tasks:
        skip_reason = _skip_reason(task)
        if skip_reason:
            logger.info("Skipping task %s: %s", task.id, skip_reason)
            _complete_without_output(task, skip_reason)
            processed += 1
            continue
        ready.append(task)

    if not ready:
        return processed, deferred

    if len(ready) == 1 or ready[0].task_type not in BATCHABLE_TYPES:
        task = ready[0]
        if _process_single_task(task):
            processed += 1
        else:
            deferred += 1
        return processed, deferred

    available = remaining_requests()
    try:
        available_int = int(available)
    except (TypeError, ValueError):
        available_int = 0
    if available_int < len(ready):
        for task in ready:
            if _process_single_task(task):
                processed += 1
            else:
                deferred += 1
        return processed, deferred

    for task in ready:
        _mark_task_processing(task)

    try:
        batch_results = _generate_batch_payload(ready)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Batch generation failed: %s", exc)
        batch_results = None

    if not batch_results or len(batch_results) != len(ready):
        # Fallback to individual generation while preserving the incremented attempt counter.
        for task in ready:
            try:
                result = _generate_payload(task)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Generation task %s failed", task.id)
                _defer_task(task, str(exc))
                deferred += 1
                continue
            if _handle_generation_result(task, result):
                processed += 1
            else:
                deferred += 1
        return processed, deferred

    for task, result in zip(ready, batch_results):
        if _handle_generation_result(task, result):
            processed += 1
        else:
            deferred += 1

    return processed, deferred


def _mark_task_processing(task: GenerationTask) -> None:
    with transaction.atomic():
        task.status = GenerationTask.STATUS_PROCESSING
        task.attempts += 1
        task.save(update_fields=["status", "attempts", "updated_at"])


def _handle_empty_response(task: GenerationTask) -> None:
    _defer_task(task, "Empty response")
    try:
        if task.thread:
            watchers = task.thread.watchers or {}
            if not isinstance(watchers, dict):
                watchers = {}
            count = int(watchers.get("empty_persist_count") or 0) + 1
            watchers["empty_persist_count"] = count
            task.thread.watchers = watchers
            task.thread.save(update_fields=["watchers"])
            if count >= 2:
                ModerationTicket.objects.create(
                    title=f"Thread needs body: {task.thread.title}",
                    description=(
                        f"Thread '{task.thread.title}' had empty generated content {count} times."
                        " Please review and seed a proper opening post."
                    ),
                    reporter=None,
                    reporter_name="system",
                    thread=task.thread,
                    source=ModerationTicket.SOURCE_SYSTEM,
                    status=ModerationTicket.STATUS_OPEN,
                    priority=ModerationTicket.PRIORITY_NORMAL,
                    tags=["needs-body"],
                    metadata={"empty_persist_count": count},
                )
    except Exception:  # noqa: BLE001
        pass


def _handle_generation_result(task: GenerationTask, result: Optional[dict[str, Any]]) -> bool:
    if result is None:
        _defer_task(task, "No content generated")
        return False

    content = (result.get("text") or "").strip()
    if not content:
        _handle_empty_response(task)
        return False

    is_placeholder = not bool(result.get("success"))
    content = _sanitize_mentions(task, content)

    if not is_placeholder:
        accepted, reason = _post_process_output(task, content)
        if not accepted:
            logger.info("Post-processor rejected task %s: %s", task.id, reason)
            _reschedule_with_stricter_instruction(task, reason)
            return False

    _persist_output(task, content, is_placeholder=is_placeholder)
    if not is_placeholder:
        _update_agent_memory(task, content)

    task.status = GenerationTask.STATUS_COMPLETED
    task.response_text = content
    task.completed_at = timezone.now()
    task.save(update_fields=["status", "response_text", "completed_at", "updated_at"])
    return True


def _generate_batch_payload(tasks: list[GenerationTask]) -> Optional[list[dict[str, Any]]]:
    if not tasks:
        return None
    prompt = _build_batch_prompt(tasks)
    max_tokens = sum(int(task.payload.get("max_tokens", DEFAULT_MAX_TOKENS)) for task in tasks)
    # Guard against runaway requests; cap to a reasonable ceiling.
    max_tokens = max(64, min(max_tokens, 3200))
    temperatures = [float(task.payload.get("temperature", 0.7)) for task in tasks]
    temperature = sum(temperatures) / len(temperatures) if temperatures else 0.7
    metadata = {
        "batch": len(tasks),
        "task_ids": [task.id for task in tasks],
        "task_types": [task.task_type for task in tasks],
    }
    result = generate_completion(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        metadata=metadata,
    )
    if not result or not (result.get("text") or "").strip():
        return None
    segments = _split_batch_output(result["text"], len(tasks))
    if not segments or len(segments) != len(tasks):
        return None
    responses: list[dict[str, Any]] = []
    for segment in segments:
        responses.append(
            {
                "success": result.get("success", True),
                "text": segment,
                "response": result.get("response"),
            }
        )
    return responses


def _build_batch_prompt(tasks: list[GenerationTask]) -> str:
    lines = [
        "You are writing multiple Ghostship Bulletin messages at once.",
        "For each task, craft the final forum-ready text only.",
        "Format your final answer exactly as:",
        "TASK 1:",
        "<message>",
        "",
        "TASK 2:",
        "<message>",
        "",
        "Do not include commentary outside this structure.",
        "",
        "Task briefs:",
    ]
    for idx, task in enumerate(tasks, start=1):
        lines.append(f"---- TASK {idx} ----")
        lines.append(_build_prompt(task))
    return "\n".join(lines)


_TASK_SPLIT_PATTERN = re.compile(
    r"^task\s+(\d+):\s*(.*?)\s*(?=^task\s+\d+:|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def _split_batch_output(text: str, expected: int) -> Optional[list[str]]:
    matches = list(_TASK_SPLIT_PATTERN.finditer(text))
    if not matches:
        return None
    outputs: list[Optional[str]] = [None] * expected
    for match in matches:
        try:
            index = int(match.group(1)) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < expected and outputs[index] is None:
            outputs[index] = match.group(2).strip()
    if any(chunk is None for chunk in outputs):
        return None
    return [chunk or "" for chunk in outputs]  # type: ignore[misc]
