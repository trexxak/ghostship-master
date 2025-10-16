from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Iterable, Sequence

from django.utils import timezone

from forum import openrouter
from forum.models import Agent, AgentGoal, Goal, GoalEvaluation, Post, TickLog

from . import goals as goal_service

logger = logging.getLogger(__name__)


def ensure_goal_catalog() -> None:
    goal_service.ensure_goal_catalog()


def progress_track() -> Iterable[Goal]:
    return goal_service.progress_track()


def emoji_palette() -> Sequence[str]:
    return goal_service.emoji_palette()


def scenario_playbook() -> Sequence[dict[str, str]]:
    return goal_service.scenario_playbook()


def progress_priorities(limit: int = 6) -> list[dict[str, Any]]:
    track = list(progress_track())[:limit]
    priorities: list[dict[str, Any]] = []
    for goal in track:
        priorities.append(
            {
                "slug": goal.slug,
                "name": goal.name,
                "emoji": goal.emoji or goal.icon_slug or "",
                "telemetry_rules": goal.telemetry_rules or {},
                "description": goal.description,
            }
        )
    return priorities


def _build_referee_prompt(batch_ticks: Sequence[int], actor: Agent | None) -> dict[str, Any]:
    ticks = list(TickLog.objects.filter(tick_number__in=batch_ticks).order_by("tick_number"))
    payload = [
        {"tick": entry.tick_number, "events": entry.events, "timestamp": entry.timestamp.isoformat()}
        for entry in ticks
    ]
    goals = Goal.objects.filter(goal_type__in=[Goal.TYPE_PROGRESS, Goal.TYPE_BADGE]).order_by("priority", "name")
    achievements = [
        {
            "slug": goal.slug,
            "name": goal.name,
            "emoji": goal.emoji or goal.icon_slug or "",
            "category": goal.category,
            "priority": goal.priority,
            "telemetry_rules": goal.telemetry_rules or {},
        }
        for goal in goals
    ]
    return {
        "system": "You are ProgressRef, a meticulous referee determining achievements for trexxak.",
        "actor": actor.name if actor else None,
        "goals": achievements,
        "tick_bundle": payload,
        "instructions": (
            "Only unlock achievements when evidence meets the criteria. "
            "Respond with JSON: {\"unlocked\": [{\"slug\": str, \"post_id\": int, \"confidence\": number, \"rationale\": str}], "
            "\"review_flags\": [{\"slug\": str, \"reason\": str}]}. "
            "Reference explicit post IDs when granting awards."
        ),
    }


def _parse_referee_response(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Progress referee returned non-JSON payload: %s", text[:200])
        return {"unlocked": [], "review_flags": [], "raw": text}


def evaluate_tick_batch(
    *,
    batch_ticks: Sequence[int],
    actor: Agent,
    model: str | None = None,
) -> tuple[GoalEvaluation, bool]:
    if not batch_ticks:
        raise ValueError("batch_ticks must not be empty")
    batch_ticks = tuple(sorted(set(batch_ticks)))
    label = f"{batch_ticks[0]:04d}-{batch_ticks[-1]:04d}"
    evaluation, created = GoalEvaluation.objects.get_or_create(
        batch_label=label,
        defaults={
            "tick_numbers": list(batch_ticks),
            "alias": "Progress is evaluated...",
            "status": GoalEvaluation.STATUS_PENDING,
        },
    )
    fresh_run = created or evaluation.status != GoalEvaluation.STATUS_COMPLETED
    if not fresh_run:
        return evaluation, False

    prompt_payload = _build_referee_prompt(batch_ticks, actor)
    evaluation.request_payload = prompt_payload
    evaluation.model_name = model or openrouter.DEFAULT_MODEL
    evaluation.status = GoalEvaluation.STATUS_PENDING
    evaluation.error_message = ""
    evaluation.response_payload = {}
    evaluation.tick_numbers = list(batch_ticks)
    evaluation.save(update_fields=["request_payload", "model_name", "status", "error_message", "response_payload", "tick_numbers"])

    started = time.monotonic()
    response = openrouter.generate_completion(
        json.dumps(prompt_payload),
        model=model,
        temperature=0.2,
        max_tokens=600,
        metadata={"alias": "progress_referee"},
    )
    evaluation.duration_ms = int((time.monotonic() - started) * 1000)

    if not response.get("success"):
        evaluation.status = GoalEvaluation.STATUS_FAILED
        evaluation.error_message = response.get("text") or "OpenRouter call failed"
        evaluation.completed_at = timezone.now()
        evaluation.save(update_fields=["duration_ms", "status", "error_message", "completed_at"])
        return evaluation, True

    parsed = _parse_referee_response(response["text"])
    evaluation.response_payload = parsed

    unlocked_payload = parsed.get("unlocked")
    if "raw" in parsed or not isinstance(unlocked_payload, list):
        evaluation.status = GoalEvaluation.STATUS_FAILED
        evaluation.error_message = str(parsed.get("raw") or "Invalid referee payload")[:255]
        evaluation.completed_at = timezone.now()
        evaluation.save(update_fields=["response_payload", "status", "error_message", "completed_at", "duration_ms"])
        return evaluation, True

    evaluation.status = GoalEvaluation.STATUS_COMPLETED
    evaluation.completed_at = timezone.now()
    evaluation.save(update_fields=["response_payload", "status", "completed_at", "duration_ms"])

    for item in unlocked_payload or []:
        slug = item.get("slug")
        if not slug:
            continue
        goal = Goal.objects.filter(slug=slug).first()
        if goal is None:
            logger.warning("Progress referee suggested unknown goal %s", slug)
            continue
        post = None
        post_id = item.get("post_id")
        if post_id:
            post = Post.objects.filter(id=post_id).first()
        goal_service.award_goal(
            agent=actor,
            goal=goal,
            source=AgentGoal.SOURCE_REFEREE,
            post=post,
            metadata={
                "referee": {
                    "confidence": item.get("confidence"),
                    "batch": evaluation.batch_label,
                }
            },
            rationale=item.get("rationale", ""),
            trace_id=evaluation.batch_label,
        )
    return evaluation, True
