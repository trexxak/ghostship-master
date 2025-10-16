from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from django.utils import timezone

from forum.models import Agent, AgentGoal, Goal

from . import goals as goal_service

MISSION_REWARD_SLUGS: dict[str, str] = {
    "ping-first-contact": "snack-bar-diplomat",
    "afterhours-snack-run": "soggy-keyboard-survivor",
    "salvage-the-seance": "memetic-hazard",
    "organic-translation-ops": "banish-with-a-wink",
    "crowned-administrator": "crowned-administrator-badge",
}


def _organic_agent() -> Agent | None:
    return Agent.objects.filter(role=Agent.ROLE_ORGANIC).order_by("id").first()


def active_missions() -> Iterable[Goal]:
    return goal_service.mission_queryset().filter(status=Goal.STATUS_ACTIVE)


def backlog_missions() -> Iterable[Goal]:
    return goal_service.mission_queryset().filter(status=Goal.STATUS_BACKLOG)


def completed_missions(limit: int = 10) -> Iterable[Goal]:
    return goal_service.mission_queryset().filter(status=Goal.STATUS_COMPLETED).order_by("-updated_at")[:limit]


def grouped_missions() -> Dict[str, List[Goal]]:
    return goal_service.grouped_missions()


def ensure_default_catalog() -> None:
    goal_service.ensure_goal_catalog()


def record_progress(
    mission: Goal,
    *,
    delta: float,
    agent: Optional[Agent] = None,
    tick_number: Optional[int] = None,
    note: str = "",
) -> None:
    return goal_service.record_progress(
        mission,
        delta=delta,
        agent=agent,
        tick_number=tick_number,
        note=note,
    )


def grant_mission_reward(mission: Goal) -> None:
    metadata = dict(mission.metadata or {})
    changed = False
    if not metadata.get("reward_unlocked"):
        metadata["reward_unlocked"] = True
        metadata["reward_unlocked_at"] = timezone.now().isoformat()
        changed = True
    reward_slug = metadata.get("reward_goal") or metadata.get("reward_achievement") or MISSION_REWARD_SLUGS.get(mission.slug)
    organic = _organic_agent()
    if reward_slug and organic is not None:
        reward_goal = Goal.objects.filter(slug=reward_slug).first()
        if reward_goal:
            goal_service.award_goal(
                agent=organic,
                goal=reward_goal,
                source=AgentGoal.SOURCE_GOAL,
                metadata={"reward_source": mission.slug},
            )
    if changed:
        mission.metadata = metadata
        mission.updated_at = timezone.now()
        mission.save(update_fields=["metadata", "updated_at"])


def evaluate_tick(tick_number: int, events: Iterable[dict[str, object]]) -> List[dict[str, object]]:
    mission_list = [mission for mission in goal_service.mission_queryset() if mission.status == Goal.STATUS_ACTIVE]
    if not mission_list:
        return []

    organic = _organic_agent()
    admin_names = set(
        Agent.objects.filter(role=Agent.ROLE_ADMIN).values_list("name", flat=True)
    )
    counters: Dict[str, float] = defaultdict(float)
    special_counters: Dict[str, float] = defaultdict(float)

    for event in events:
        event_type = event.get("type")
        if event_type in {"reply_task", "oi_post", "oi_reply"}:
            counters["track-post"] += 1
        elif event_type == "report":
            counters["track-report"] += 1
        elif event_type == "thread":
            counters["track-thread"] += 1
        elif event_type in {"private_message_task", "oi_dm"}:
            recipient = event.get("recipient") or event.get("target")
            if isinstance(recipient, str) and recipient in admin_names:
                counters["track-dm-admin"] += 1
                counters["track-dm-any"] += 1
            else:
                counters["track-dm-any"] += 1
        elif event_type == "specials":
            flags = event.get("flags") or {}
            if flags.get("seance"):
                special_counters["seance"] += 1
            if flags.get("omen"):
                special_counters["omen"] += 1

    story_events: List[dict[str, object]] = []

    track_targets: dict[str, Goal] = {}
    seance_missions: list[Goal] = []
    standalone_missions: list[Goal] = []

    for mission in mission_list:
        metadata = mission.metadata or {}
        track_prefix = metadata.get("track")
        if track_prefix:
            if mission.status == Goal.STATUS_ACTIVE and track_prefix not in track_targets:
                track_targets[track_prefix] = mission
            continue
        if mission.slug == "salvage-the-seance":
            seance_missions.append(mission)
        else:
            standalone_missions.append(mission)

    for track_prefix, mission in track_targets.items():
        delta = counters.get(track_prefix, 0.0)
        if delta <= 0:
            continue
        pre_status = mission.status
        record_progress(
            mission,
            delta=delta,
            agent=organic,
            tick_number=tick_number,
            note=f"tick-{tick_number}:{mission.slug}",
        )
        mission.refresh_from_db(fields=["status", "progress_current", "metadata", "updated_at"])
        story_events.append(
            {
                "type": "mission_progress",
                "mission": mission.slug,
                "track": track_prefix,
                "delta": float(delta),
                "progress": float(mission.progress_current),
                "target": float(mission.target or 0),
                "tick": tick_number,
            }
        )
        if pre_status != Goal.STATUS_COMPLETED and mission.status == Goal.STATUS_COMPLETED:
            grant_mission_reward(mission)
            story_events.append(
                {
                    "type": "mission_reward",
                    "mission": mission.slug,
                    "reward": (mission.metadata or {}).get("reward_label"),
                    "sticker": (mission.metadata or {}).get("reward_sticker"),
                }
            )

    for mission in seance_missions:
        delta = special_counters.get("seance", 0.0)
        if delta <= 0:
            continue
        pre_status = mission.status
        record_progress(
            mission,
            delta=delta,
            agent=organic,
            tick_number=tick_number,
            note=f"tick-{tick_number}:{mission.slug}",
        )
        mission.refresh_from_db(fields=["status", "progress_current", "metadata", "updated_at"])
        story_events.append(
            {
                "type": "mission_progress",
                "mission": mission.slug,
                "track": "seance",
                "delta": float(delta),
                "progress": float(mission.progress_current),
                "target": float(mission.target or 0),
                "tick": tick_number,
            }
        )
        if pre_status != Goal.STATUS_COMPLETED and mission.status == Goal.STATUS_COMPLETED:
            grant_mission_reward(mission)
            story_events.append(
                {
                    "type": "mission_reward",
                    "mission": mission.slug,
                    "reward": (mission.metadata or {}).get("reward_label"),
                    "sticker": (mission.metadata or {}).get("reward_sticker"),
                }
            )

    for mission in standalone_missions:
        delta = counters.get(mission.slug, 0.0)
        if delta <= 0:
            continue
        pre_status = mission.status
        record_progress(
            mission,
            delta=delta,
            agent=organic,
            tick_number=tick_number,
            note=f"tick-{tick_number}:{mission.slug}",
        )
        mission.refresh_from_db(fields=["status", "progress_current", "metadata", "updated_at"])
        story_events.append(
            {
                "type": "mission_progress",
                "mission": mission.slug,
                "delta": float(delta),
                "progress": float(mission.progress_current),
                "target": float(mission.target or 0),
                "tick": tick_number,
            }
        )
        if pre_status != Goal.STATUS_COMPLETED and mission.status == Goal.STATUS_COMPLETED:
            grant_mission_reward(mission)
            story_events.append(
                {
                    "type": "mission_reward",
                    "mission": mission.slug,
                    "reward": (mission.metadata or {}).get("reward_label"),
                    "sticker": (mission.metadata or {}).get("reward_sticker"),
                }
            )

    return story_events
