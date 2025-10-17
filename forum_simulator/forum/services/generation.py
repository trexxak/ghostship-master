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
MENTION_TOKEN_PATTERN = re.compile(r"@([A-Za-z0-9_.-]{2,})|\[([A-Za-z0-9_.-]{2,})\]")
BATCHABLE_TYPES = {
    GenerationTask.TYPE_REPLY,
    GenerationTask.TYPE_DM,
}

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
        "Observe organics with curiosity and candor; respond like a focused investigator.",
        "Prefer grounded, evidence-focused language rather than retro web slang or forced nostalgia.",
    ]

    mind_state = agent.mind_state if isinstance(agent.mind_state, dict) else {}
    signature_hint = mind_state.get("persona_signature") or mind_state.get("signature")
    if signature_hint:
        persona_bits.append(f"Voice sample: {signature_hint}")

    needs = agent.needs or {}
    if needs:
        focused = sorted(
            needs.items(), key=lambda item: item[1], reverse=True)[:3]
        persona_bits.append(
            "Key drives: " + ", ".join(f"{k} {v:.2f}" for k, v in focused))

    memory_lines = [entry for entry in (agent.memory or []) if entry][-3:]
    context: list[str] = persona_bits
    if memory_lines:
        context.append("Things you still remember:")
        for memo in memory_lines:
            if isinstance(memo, str):
                context.append(f"- {memo[:160]}")
            else:
                snippet = memo.get("summary") or memo.get("text") or ""
                if snippet:
                    context.append(f"- {snippet[:160]}")

    mentionable: list[str] = []
    length_hint: dict[str, Any] | None = None

    agent_handle_lower = ""
    if getattr(agent, "name", None):
        agent_handle_lower = agent.name.lower()

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

        recent_quotes: list[str] = []
        for post in reversed(recent_posts):
            excerpt = _format_post_excerpt(post)
            if excerpt:
                recent_quotes.append(excerpt)
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

        topics = task.payload.get("topics") or getattr(
            task.thread, "topics", []) or []
        if topics:
            context.append("Topics: " + ", ".join(topics))

        mentionable_handles: set[str] = set()
        handle_to_excerpt: dict[str, str | None] = {}
        thread_author = getattr(task.thread, "author", None)
        if thread_author and getattr(thread_author, "name", None):
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
                "Only mention ghosts listed above and anchor any tag to the cited detail; do not invent handles or tag yourself unless directly summoned."
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

    if task.task_type == GenerationTask.TYPE_REPLY:
        context.append(
            "Anchor the reply in the organic being discussed and bring a new observation or pointed question.")
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
        # Automated DM handoffs are disabled; operators must craft DMs manually.
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
    memory = agent.memory or []
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


def _update_agent_memory(task: GenerationTask, content: str) -> None:
    agent = task.agent
    memory = agent.memory or []
    memory.append(
        {
            "ts": timezone.now().isoformat(),
            "task": task.task_type,
            "thread": task.thread_id,
            "recipient": task.recipient_id,
            "summary": content[:200],
        }
    )
    agent.memory = memory[-MEMORY_MAX:]
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
