from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from django.conf import settings
from django.templatetags.static import static

from forum.models import Agent, AgentGoal, Goal


@dataclass(frozen=True)
class AvatarUnlock:
    """Represents a remote avatar slot unlocked by a specific progression goal."""

    slot: int
    goal_slug: str
    label: str
    blurb: str = ""


PROGRESSION_AVATAR_UNLOCKS: Sequence[AvatarUnlock] = (
    AvatarUnlock(1, "progress-spark", "Spark Resonance"),
    AvatarUnlock(2, "progress-footprint", "Footprint Echo"),
    AvatarUnlock(3, "progress-influencer", "Influencer Prism"),
    AvatarUnlock(4, "progress-weaver", "Weaver Loom"),
    AvatarUnlock(5, "progress-moderator-for-a-day", "Moderator Crest"),
    AvatarUnlock(6, "progress-systems-tinkerer", "Systems Tinkerer Glyph"),
    AvatarUnlock(7, "progress-kingmaker", "Kingmaker Diadem"),
    AvatarUnlock(8, "progress-guild-leader", "Guild Leader Sigil"),
    AvatarUnlock(9, "progress-administrator", "Administrator Crown"),
)


def avatar_unlocks() -> Sequence[AvatarUnlock]:
    return PROGRESSION_AVATAR_UNLOCKS


def default_avatar_url() -> str:
    """Return the canonical baseline avatar for trexxak."""
    profile_base = getattr(settings, "PROFILE_AVATAR_BASE_URL", "").rstrip("/")
    if profile_base:
        return f"{profile_base}/2.png"
    alt_base = getattr(settings, "UNLOCKABLE_AVATAR_BASE_URL", "").rstrip("/")
    if alt_base:
        return f"{alt_base}/1.png"
    return ""


def default_avatar_option() -> dict[str, str]:
    url = default_avatar_url()
    if not url:
        return {}
    return {
        "value": url,
        "url": url,
        "label": "Trexxak Baseline",
        "slot": "default",
    }


def avatar_slot_url(slot: int) -> str:
    base = getattr(settings, "UNLOCKABLE_AVATAR_BASE_URL", "").rstrip("/")
    if not base:
        return ""
    return f"{base}/{slot}.png"


def available_avatar_options(agent: Agent | None) -> list[dict[str, str]]:
    """
    Build the list of remote avatar options unlocked for the given agent.

    Each option contains both the persisted value and preview URL so the UI can
    render selections without extra bookkeeping.
    """
    if agent is None:
        return []
    unlock_map = {unlock.goal_slug: unlock for unlock in avatar_unlocks()}
    unlocked_lookup = set(
        AgentGoal.objects.filter(
            agent=agent,
            goal__slug__in=unlock_map.keys(),
            unlocked_at__isnull=False,
        ).values_list("goal__slug", flat=True)
    )
    options: list[dict[str, str]] = []
    for unlock in avatar_unlocks():
        if unlock.goal_slug not in unlocked_lookup:
            continue
        url = avatar_slot_url(unlock.slot)
        if not url:
            continue
        options.append(
            {
                "value": url,
                "url": url,
                "label": unlock.label,
                "slot": str(unlock.slot),
            }
        )
    return options


def sticker_asset_url(slug: str | None) -> str:
    if not slug:
        return ""
    base = getattr(settings, "UNLOCKABLE_EMOJI_BASE_URL", "").rstrip("/")
    if not base:
        return ""
    normalised = slug.strip()
    return f"{base}/{normalised}.png"


def mission_reward_assets() -> list[dict[str, str]]:
    """
    Collect metadata for every mission reward sticker defined in the catalogue.
    """
    assets: list[dict[str, str]] = []
    missions = Goal.objects.filter(goal_type=Goal.TYPE_MISSION).order_by("priority", "name")
    for mission in missions:
        metadata = mission.metadata or {}
        slug = metadata.get("reward_sticker")
        if not slug:
            continue
        label = metadata.get("reward_label") or mission.name
        url = sticker_asset_url(slug)
        assets.append(
            {
                "slug": slug,
                "label": label,
                "mission_slug": mission.slug,
                "url": url,
            }
        )
    return assets


def mission_reward_count() -> int:
    return len({item["slug"] for item in mission_reward_assets()})


def avatar_option_catalog(agent: Agent | None = None) -> list[dict[str, str]]:
    """Aggregate all avatar options available to the organic operator."""

    options: list[dict[str, str]] = []
    seen: set[str] = set()

    default_option = default_avatar_option()
    default_value = default_option.get("value")
    if default_value:
        options.append(default_option)
        seen.add(str(default_value))

    base_dir = Path(settings.BASE_DIR) / "forum" / "static" / "forum" / "avatars"
    if base_dir.exists():
        for path in sorted(base_dir.glob("*.png")):
            rel = f"forum/avatars/{path.name}"
            if rel in seen:
                continue
            options.append({
                "value": rel,
                "url": static(rel),
                "label": path.stem,
            })
            seen.add(rel)

    for remote in available_avatar_options(agent):
        value = str(remote.get("value") or "")
        if not value or value in seen:
            continue
        options.append(remote)
        seen.add(value)

    if not options:
        fallback = "forum/avatars/ghost_001.png"
        options.append({
            "value": fallback,
            "url": static(fallback),
            "label": "Ghost 001",
        })

    return options
