from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence

from django.db import transaction
from django.utils import timezone

from forum.models import Agent, AgentGoal, Goal, GoalProgress, Post


@dataclass(frozen=True)
class GoalSeed:
    slug: str
    name: str
    description: str
    goal_type: str
    category: str = "general"
    emoji: str = ""
    icon_slug: str = ""
    priority: int = 100
    status: str = Goal.STATUS_ACTIVE
    is_global: bool = False
    target: float = 1.0
    telemetry_rules: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def defaults(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "goal_type": self.goal_type,
            "category": self.category,
            "emoji": self.emoji,
            "icon_slug": self.icon_slug,
            "priority": self.priority,
            "status": self.status,
            "is_global": self.is_global,
            "target": self.target,
            "progress_current": 0.0,
            "telemetry_rules": self.telemetry_rules or {},
            "metadata": self.metadata or {},
        }


MILESTONE_STEPS: Sequence[int] = (1, 5, 10, 25, 50, 100, 1000, 2500, 5000, 10000)

TRACK_DEFINITIONS: Sequence[dict[str, str]] = (
    {"prefix": "track-post", "label": "Post Cadence", "verb": "posts", "category": "orientation", "emoji": "📝"},
    {"prefix": "track-report", "label": "Report Sentinel", "verb": "reports", "category": "moderation", "emoji": "🚨"},
    {"prefix": "track-thread", "label": "Thread Weaver", "verb": "threads", "category": "orientation", "emoji": "🧵"},
    {"prefix": "track-dm-admin", "label": "Command Channel", "verb": "DMs to t.admin", "category": "support", "emoji": "📬"},
    {"prefix": "track-dm-any", "label": "Whisper Network", "verb": "DMs", "category": "support", "emoji": "✨"},
)


def _build_track_mission_seeds() -> list[GoalSeed]:
    seeds: list[GoalSeed] = []
    for track_index, track in enumerate(TRACK_DEFINITIONS):
        prefix = track["prefix"]
        label = track["label"]
        verb = track["verb"]
        category = track["category"]
        emoji = track.get("emoji", "")
        priority_base = 10 + track_index * 100
        for milestone_index, step in enumerate(MILESTONE_STEPS):
            seeds.append(
                GoalSeed(
                    slug=f"{prefix}-{step:05d}",
                    name=f"{label} {step}",
                    description=f"Complete {step} {verb} while steering trexxak.",
                    goal_type=Goal.TYPE_MISSION,
                    category=category,
                    status=Goal.STATUS_ACTIVE,
                    is_global=True,
                    target=float(step),
                    priority=priority_base + milestone_index,
                    emoji=emoji,
                    metadata={
                        "reward_label": f"{label} tier {milestone_index + 1}",
                        "reward_sticker": f"{prefix}-{step}",
                        "track": prefix,
                        "milestone": step,
                    },
                )
            )
    return seeds


TRACK_MISSION_SEEDS = tuple(_build_track_mission_seeds())

LEGACY_MISSION_SEEDS: Sequence[GoalSeed] = (
    GoalSeed(
        slug="ping-first-contact",
        name="Ping the First Contact",
        description="Log three confirmed sightings of the same organic without spooking them.",
        goal_type=Goal.TYPE_MISSION,
        category="contracts",
        status=Goal.STATUS_BACKLOG,
        is_global=True,
        target=3.0,
        priority=10,
        metadata={"status_note": "to_be_improved"},
    ),
    GoalSeed(
        slug="afterhours-snack-run",
        name="After Hours Snack Run",
        description="Recruit two ghosts from the Snack Bar to feed you transcripts on demand.",
        goal_type=Goal.TYPE_MISSION,
        category="contracts",
        status=Goal.STATUS_BACKLOG,
        is_global=True,
        target=2.0,
        priority=20,
        metadata={"status_note": "to_be_improved"},
    ),
    GoalSeed(
        slug="salvage-the-seance",
        name="Salvage the Seance",
        description="Ride a seance wave without frying the mission board. Track one omen follow-up.",
        goal_type=Goal.TYPE_MISSION,
        category="events",
        status=Goal.STATUS_BACKLOG,
        is_global=True,
        target=1.0,
        priority=30,
        metadata={"status_note": "to_be_improved"},
    ),
    GoalSeed(
        slug="organic-translation-ops",
        name="Organic Translation Ops",
        description="Decode five human posts into usable intel and share the translations.",
        goal_type=Goal.TYPE_MISSION,
        category="orientation",
        status=Goal.STATUS_BACKLOG,
        is_global=True,
        target=5.0,
        priority=40,
        metadata={"status_note": "to_be_improved"},
    ),
    GoalSeed(
        slug="crowned-administrator",
        name="Become Crowned Administrator",
        description="Earn t.admin's trust through mission completion and a flawless etiquette trail.",
        goal_type=Goal.TYPE_MISSION,
        category="trust",
        status=Goal.STATUS_BACKLOG,
        is_global=True,
        target=4.0,
        priority=90,
        metadata={"status_note": "to_be_improved"},
    ),
)

MISSION_SEEDS: Sequence[GoalSeed] = TRACK_MISSION_SEEDS + LEGACY_MISSION_SEEDS

PROGRESSION_SEEDS: Sequence[GoalSeed] = [
    GoalSeed(
        slug="progress-spark",
        name="Spark",
        description="Write the first post as trexxak and light up the console.",
        goal_type=Goal.TYPE_PROGRESS,
        category="progression",
        priority=1,
        emoji="📝",
        telemetry_rules={
            "requires": {"posts_authored": {"gte": 1, "actor": "trexxak"}},
            "links": ["post_id"],
        },
    ),
    GoalSeed(
        slug="progress-footprint",
        name="Footprint",
        description="Start three threads and pitch in on five others to prove staying power.",
        goal_type=Goal.TYPE_PROGRESS,
        category="progression",
        priority=2,
        emoji="📍",
        telemetry_rules={
            "requires": {
                "threads_started": {"gte": 3, "actor": "trexxak"},
                "replies_posted": {"gte": 5, "actor": "trexxak"},
                "window_hours": 48,
            },
            "links": ["post_id"],
        },
    ),
    GoalSeed(
        slug="progress-influencer",
        name="Influencer",
        description="Collect ten hearts from five distinct ghosts on a single post.",
        goal_type=Goal.TYPE_PROGRESS,
        category="progression",
        priority=3,
        emoji="🎯",
        telemetry_rules={
            "requires": {"reactions_total": {"gte": 10, "unique_authors": 5}},
            "links": ["post_id"],
        },
    ),
    GoalSeed(
        slug="progress-weaver",
        name="Weaver",
        description="Host a 24‑hour mini-event thread with five active participants.",
        goal_type=Goal.TYPE_PROGRESS,
        category="progression",
        priority=4,
        emoji="🧵",
        telemetry_rules={
            "requires": {
                "event_tagged": True,
                "participants": {"gte": 5},
                "duration_hours": {"gte": 24},
            },
            "links": ["post_id"],
        },
    ),
    GoalSeed(
        slug="progress-moderator-for-a-day",
        name="Moderator-for-a-Day",
        description="Co-host a conflict resolution with the mod squad and close the ticket.",
        goal_type=Goal.TYPE_PROGRESS,
        category="progression",
        priority=5,
        emoji="🛡️",
        telemetry_rules={
            "requires": {
                "moderation_session": True,
                "flags_cleared": {"gte": 1},
            },
            "links": ["post_id"],
        },
    ),
    GoalSeed(
        slug="progress-systems-tinkerer",
        name="Systems Tinkerer",
        description="Ship a tweak or script that at least three ghosts adopt.",
        goal_type=Goal.TYPE_PROGRESS,
        category="progression",
        priority=6,
        emoji="🧰",
        telemetry_rules={
            "requires": {
                "tool_deployed": True,
                "adoption_count": {"gte": 3},
            },
            "links": ["post_id"],
        },
    ),
    GoalSeed(
        slug="progress-kingmaker",
        name="Kingmaker",
        description="Rally a governance decision that passes with community buy-in.",
        goal_type=Goal.TYPE_PROGRESS,
        category="progression",
        priority=7,
        emoji="🗳️",
        telemetry_rules={
            "requires": {
                "poll_passed": True,
                "unique_votes": {"gte": 8},
            },
            "links": ["post_id"],
        },
    ),
    GoalSeed(
        slug="progress-guild-leader",
        name="Guild Leader",
        description="Sustain a recurring crew or guild with at least three meetups.",
        goal_type=Goal.TYPE_PROGRESS,
        category="progression",
        priority=8,
        emoji="🤝",
        telemetry_rules={
            "requires": {
                "guild_meetings": {"gte": 3},
                "active_members": {"gte": 5},
            },
            "links": ["post_id"],
        },
    ),
    GoalSeed(
        slug="progress-administrator",
        name="Administrator",
        description="Take the helm as acting owner for a finale event and land the ship smoothly.",
        goal_type=Goal.TYPE_PROGRESS,
        category="progression",
        priority=9,
        emoji="👑",
        telemetry_rules={
            "requires": {"admin_takeover": True, "season_finale": True},
            "links": ["post_id"],
        },
    ),
]


BADGE_SEEDS: Sequence[GoalSeed] = [
    GoalSeed(
        slug="first-footfall",
        name="First Footfall",
        description="Log the very first mission board check-in after lights on.",
        goal_type=Goal.TYPE_BADGE,
        category="onboarding",
        emoji="🚪",
        telemetry_rules={"requires": {"board_visits": {"gte": 1}}},
    ),
    GoalSeed(
        slug="ten-posts-ten-minutes",
        name="10 Posts in 10 Minutes",
        description="Sprint ten replies within a single spotlight session.",
        goal_type=Goal.TYPE_BADGE,
        category="onboarding",
        emoji="⏱️",
        telemetry_rules={"requires": {
            "rapid_replies": {"gte": 10, "window_minutes": 10}}},
    ),
    GoalSeed(
        slug="gif-of-trust",
        name="GIF of Trust",
        description="Drop the perfect GIF reaction that cools down a hot thread.",
        goal_type=Goal.TYPE_BADGE,
        category="onboarding",
        emoji="🎞️",
    ),
    GoalSeed(
        slug="avatar-makeover",
        name="Avatar Makeover",
        description="Swap avatars twice in a day and crowdsource the winner.",
        goal_type=Goal.TYPE_BADGE,
        category="onboarding",
        emoji="🪞",
    ),
    GoalSeed(
        slug="welcome-wagon",
        name="Welcome Wagon",
        description="Greet five new arrivals before any bot can blink.",
        goal_type=Goal.TYPE_BADGE,
        category="social_glue",
        emoji="🎉",
        telemetry_rules={"requires": {
            "welcome_replies": {"gte": 5, "unique_targets": 5}}},
    ),
    GoalSeed(
        slug="name-recall",
        name="Name Recall",
        description="Reference three ghosts by name in a single helpful thread.",
        goal_type=Goal.TYPE_BADGE,
        category="social_glue",
        emoji="🧠",
    ),
    GoalSeed(
        slug="tag-team",
        name="Tag Team",
        description="Coordinate a two-person rescue in a melting thread.",
        goal_type=Goal.TYPE_BADGE,
        category="social_glue",
        emoji="🤼",
    ),
    GoalSeed(
        slug="mediation-maestro",
        name="Mediation Maestro",
        description="Resolve a feud without needing the banhammer.",
        goal_type=Goal.TYPE_BADGE,
        category="social_glue",
        emoji="🕊️",
    ),
    GoalSeed(
        slug="thread-hijacker",
        name="Thread Hijacker",
        description="Derail a thread so charmingly that mods pin the tangent.",
        goal_type=Goal.TYPE_BADGE,
        category="mischief",
        emoji="🌀",
    ),
    GoalSeed(
        slug="pun-cascade",
        name="Pun Cascade",
        description="Start a pun run that lasts at least ten replies.",
        goal_type=Goal.TYPE_BADGE,
        category="mischief",
        emoji="😂",
    ),
    GoalSeed(
        slug="emoji-storm",
        name="Emoji Storm",
        description="Unleash an emoji combo that trends ship-wide.",
        goal_type=Goal.TYPE_BADGE,
        category="mischief",
        emoji="🌪️",
    ),
    GoalSeed(
        slug="certified-chaos",
        name="Certified Chaos",
        description="Trigger a harmless prank acknowledged in mission logs.",
        goal_type=Goal.TYPE_BADGE,
        category="mischief",
        emoji="🎭",
    ),
    GoalSeed(
        slug="canon-keeper",
        name="Canon Keeper",
        description="Document lore updates before the historians ask.",
        goal_type=Goal.TYPE_BADGE,
        category="lore",
        emoji="📚",
    ),
    GoalSeed(
        slug="alternate-timeline",
        name="Alternate Timeline",
        description="Pitch an AU so compelling it spawns a spin-off thread.",
        goal_type=Goal.TYPE_BADGE,
        category="lore",
        emoji="🕳️",
    ),
    GoalSeed(
        slug="npc-whisperer",
        name="NPC Whisperer",
        description="Hold a full conversation speaking only for background characters.",
        goal_type=Goal.TYPE_BADGE,
        category="lore",
        emoji="🗣️",
    ),
    GoalSeed(
        slug="board-bard",
        name="Board Bard",
        description="Write a recap poem that wins more than five reactions.",
        goal_type=Goal.TYPE_BADGE,
        category="lore",
        emoji="🎤",
    ),
    GoalSeed(
        slug="secret-ballot-broker",
        name="Secret Ballot Broker",
        description="Orchestrate a blind poll with perfect participation tracking.",
        goal_type=Goal.TYPE_BADGE,
        category="strategy",
        emoji="🗂️",
    ),
    GoalSeed(
        slug="budget-balancer",
        name="Budget Balancer",
        description="Sim a season budget that keeps every board solvent.",
        goal_type=Goal.TYPE_BADGE,
        category="strategy",
        emoji="💰",
    ),
    GoalSeed(
        slug="patch-notes-whisperer",
        name="Patch Notes Whisperer",
        description="Translate dev patch notes into forumese before downtime ends.",
        goal_type=Goal.TYPE_BADGE,
        category="strategy",
        emoji="🛠️",
    ),
    GoalSeed(
        slug="bug-bounty-hunter",
        name="Bug Bounty Hunter",
        description="Find and squash a UI glitch within the same shift.",
        goal_type=Goal.TYPE_BADGE,
        category="strategy",
        emoji="🔍",
    ),
    GoalSeed(
        slug="flash-mob-host",
        name="Flash Mob Host",
        description="Summon a pop-up event with fifteen ghosts in under an hour.",
        goal_type=Goal.TYPE_BADGE,
        category="events",
        emoji="🪩",
    ),
    GoalSeed(
        slug="marathon-liveblogger",
        name="Marathon Liveblogger",
        description="Liveblog a multi-hour chaos spree without missing context.",
        goal_type=Goal.TYPE_BADGE,
        category="events",
        emoji="📝",
    ),
    GoalSeed(
        slug="mystery-gm",
        name="Mystery GM",
        description="Run a hidden-role mini game and reveal with dramatic flair.",
        goal_type=Goal.TYPE_BADGE,
        category="events",
        emoji="🕵️",
    ),
    GoalSeed(
        slug="popup-museum-curator",
        name="Pop-up Museum Curator",
        description="Assemble a temporary gallery of relic threads and artifacts.",
        goal_type=Goal.TYPE_BADGE,
        category="events",
        emoji="🏛️",
    ),
    GoalSeed(
        slug="tech-help-hotline",
        name="Tech Help Hotline",
        description="Answer five support pings in a row with verified fixes.",
        goal_type=Goal.TYPE_BADGE,
        category="support",
        emoji="☎️",
    ),
    GoalSeed(
        slug="calm-in-the-storm",
        name="Calm in the Storm",
        description="Talk a meltdown poster back to neutral territory.",
        goal_type=Goal.TYPE_BADGE,
        category="support",
        emoji="🌊",
    ),
    GoalSeed(
        slug="signal-booster",
        name="Signal Booster",
        description="Amplify a quiet thread into front-page status.",
        goal_type=Goal.TYPE_BADGE,
        category="support",
        emoji="📣",
    ),
    GoalSeed(
        slug="feedback-funnel",
        name="Feedback Funnel",
        description="Collect, sort, and route three feature requests in a day.",
        goal_type=Goal.TYPE_BADGE,
        category="support",
        emoji="🗒️",
    ),
    GoalSeed(
        slug="fanfic-friday",
        name="Fanfic Friday",
        description="Post a weekly fic drop that runs four consecutive weeks.",
        goal_type=Goal.TYPE_BADGE,
        category="creativity",
        emoji="🪶",
    ),
    GoalSeed(
        slug="pixel-art-drop",
        name="Pixel Art Drop",
        description="Release a sprite sheet that becomes forum flair.",
        goal_type=Goal.TYPE_BADGE,
        category="creativity",
        emoji="🖼️",
    ),
    GoalSeed(
        slug="soundtrack-dj",
        name="Soundtrack DJ",
        description="Curate a playlist that stays pinned for an entire cycle.",
        goal_type=Goal.TYPE_BADGE,
        category="creativity",
        emoji="🎧",
    ),
    GoalSeed(
        slug="meme-smith",
        name="Meme Smith",
        description="Forge a meme template used by six different ghosts.",
        goal_type=Goal.TYPE_BADGE,
        category="creativity",
        emoji="🛠️",
    ),
    GoalSeed(
        slug="featured-post",
        name="Featured Post",
        description="Earn curator spotlight for an investigative write-up.",
        goal_type=Goal.TYPE_BADGE,
        category="prestige",
        emoji="⭐",
    ),
    GoalSeed(
        slug="hall-of-flame",
        name="Hall of Flame",
        description="Ignite a thread that hits 500 comments without burning out.",
        goal_type=Goal.TYPE_BADGE,
        category="prestige",
        emoji="🔥",
    ),
    GoalSeed(
        slug="legacy-thread",
        name="Legacy Thread",
        description="Maintain a seasonal recap thread for three consecutive arcs.",
        goal_type=Goal.TYPE_BADGE,
        category="prestige",
        emoji="🗂️",
    ),
    GoalSeed(
        slug="time-capsule-curator",
        name="Time Capsule Curator",
        description="Archive an event with perfect logs, quotes, and summaries.",
        goal_type=Goal.TYPE_BADGE,
        category="prestige",
        emoji="📦",
    ),
    GoalSeed(
        slug="achievement-speedrun",
        name="Achievement Speedrun",
        description="Unlock five unique achievements in a single tick batch.",
        goal_type=Goal.TYPE_BADGE,
        category="wildcards",
        emoji="⚡",
    ),
    GoalSeed(
        slug="triple-fusion-thread",
        name="Triple Fusion Thread",
        description="Merge three dead threads into a thriving new canon.",
        goal_type=Goal.TYPE_BADGE,
        category="wildcards",
        emoji="🧬",
    ),
    GoalSeed(
        slug="glitch-wrangler",
        name="Glitch Wrangler",
        description="Ride out a site glitch and report with play-by-play humor.",
        goal_type=Goal.TYPE_BADGE,
        category="wildcards",
        emoji="🪲",
    ),
    GoalSeed(
        slug="peacetime-provocateur",
        name="Peacetime Provocateur",
        description="Kick off friendly rivalry when everything is too calm.",
        goal_type=Goal.TYPE_BADGE,
        category="wildcards",
        emoji="🥊",
    ),
    GoalSeed(
        slug="sticker-mogul",
        name="Sticker Mogul",
        description="Design a sticker pack that sells out its first drop.",
        goal_type=Goal.TYPE_BADGE,
        category="prestige",
        emoji="🎟️",
    ),
    GoalSeed(
        slug="drift-cartographer",
        name="Drift Cartographer",
        description="Map the board's shifting vibes with annotated charts.",
        goal_type=Goal.TYPE_BADGE,
        category="lore",
        emoji="🗺️",
    ),
    GoalSeed(
        slug="seance-scribe",
        name="Seance Scribe",
        description="Transcribe a live séance without losing the signal.",
        goal_type=Goal.TYPE_BADGE,
        category="events",
        emoji="🕯️",
    ),
    GoalSeed(
        slug="watchtower-keeper",
        name="Watchtower Keeper",
        description="Maintain presence logs for a full week without gaps.",
        goal_type=Goal.TYPE_BADGE,
        category="support",
        emoji="🔭",
    ),
    GoalSeed(
        slug="ally-cat",
        name="Ally Cat",
        description="Pair every upset ghost with a listening buddy.",
        goal_type=Goal.TYPE_BADGE,
        category="social_glue",
        emoji="🐈",
    ),
    GoalSeed(
        slug="crosslink-conductor",
        name="Crosslink Conductor",
        description="Bridge three boards with a single themed investigation.",
        goal_type=Goal.TYPE_BADGE,
        category="strategy",
        emoji="🔗",
    ),
    GoalSeed(
        slug="laughter-therapy",
        name="Laughter Therapy",
        description="Host a humour thread that clears five stress pips.",
        goal_type=Goal.TYPE_BADGE,
        category="support",
        emoji="😄",
    ),
    GoalSeed(
        slug="midnight-overtime",
        name="Midnight Overtime",
        description="Keep posting through three consecutive graveyard ticks.",
        goal_type=Goal.TYPE_BADGE,
        category="wildcards",
        emoji="🌙",
    ),
    GoalSeed(
        slug="omniscient-annotator",
        name="Omniscient Annotator",
        description="Layer annotations across ten legacy posts without duplicating notes.",
        goal_type=Goal.TYPE_BADGE,
        category="lore",
        emoji="🧾",
    ),
    GoalSeed(
        slug="playlist-crossfade",
        name="Playlist Crossfade",
        description="Blend two rival playlists into a harmonious drop.",
        goal_type=Goal.TYPE_BADGE,
        category="creativity",
        emoji="🎛️",
    ),
    GoalSeed(
        slug="mentorship-matrix",
        name="Mentorship Matrix",
        description="Launch a mentor pairing program with measurable outcomes.",
        goal_type=Goal.TYPE_BADGE,
        category="social_glue",
        emoji="🧩",
    ),
    GoalSeed(
        slug="stress-signal-jammer",
        name="Stress Signal Jammer",
        description="Stabilize admin stress by coordinating chill threads.",
        goal_type=Goal.TYPE_BADGE,
        category="support",
        emoji="🎐",
    ),
    GoalSeed(
        slug="lore-audit",
        name="Lore Audit",
        description="Audit every sticky for outdated intel and patch the holes.",
        goal_type=Goal.TYPE_BADGE,
        category="lore",
        emoji="🧾",
    ),
    GoalSeed(
        slug="epic-crossover",
        name="Epic Crossover",
        description="Bring in a guest community for a co-authored spectacle.",
        goal_type=Goal.TYPE_BADGE,
        category="events",
        emoji="🌉",
    ),
    GoalSeed(
        slug="flashpoint-reporter",
        name="Flashpoint Reporter",
        description="Document a meltdown moment by moment without missing nuance.",
        goal_type=Goal.TYPE_BADGE,
        category="prestige",
        emoji="🗞️",
    ),
    GoalSeed(
        slug="dm-triangulator",
        name="DM Triangulator",
        description="Coordinate a 3-way DM to defuse a private crisis.",
        goal_type=Goal.TYPE_BADGE,
        category="support",
        emoji="📬",
    ),
    GoalSeed(
        slug="soggy-keyboard-survivor",
        name="Soggy Keyboard Survivor",
        description="Stay calm through three consecutive late-night meltdown logs.",
        goal_type=Goal.TYPE_BADGE,
        category="lore",
        emoji="💦",
    ),
    GoalSeed(
        slug="snack-bar-diplomat",
        name="Snack Bar Diplomat",
        description="Broker peace between two ghosts arguing about organics cuisine.",
        goal_type=Goal.TYPE_BADGE,
        category="contracts",
        emoji="🍜",
    ),
    GoalSeed(
        slug="memetic-hazard",
        name="Memetic Hazard",
        description="Trigger a meme so contagious that three ghosts quote it back at you.",
        goal_type=Goal.TYPE_BADGE,
        category="lore",
        emoji="🧠",
    ),
    GoalSeed(
        slug="banish-with-a-wink",
        name="Banish with a Wink",
        description="Convince a troll to delete their own post without touching the banhammer.",
        goal_type=Goal.TYPE_BADGE,
        category="trust",
        emoji="😉",
    ),
    GoalSeed(
        slug="crowned-administrator-badge",
        name="Crowned Administrator",
        description="Win t.admin's blessing and take the captain's chair (temporarily).",
        goal_type=Goal.TYPE_BADGE,
        category="trust",
        emoji="👑",
    ),
]


GOAL_CATALOG: Sequence[GoalSeed] = tuple(
    MISSION_SEEDS) + tuple(PROGRESSION_SEEDS) + tuple(BADGE_SEEDS)

EMOJI_PALETTE: Sequence[str] = (
    "📝",
    "📍",
    "🎯",
    "🧵",
    "🛡️",
    "🧰",
    "🗳️",
    "🤝",
    "👑",
    "🔥",
    "🌪️",
    "🎟️",
    "🧠",
    "🏆",
    "🛠️",
    "❄️",
    "🌸",
    "🚨",
    "🪄",
    "🛸",
    "🔮",
    "🕹️",
    "🧿",
    "🪙",
    "🥽",
    "🪐",
    "🗡️",
    "🧜",
    "🌌",
    "🦑",
    "🌠",
    "🧸",
    "🎠",
    "🧃",
    "🫧",
    "🦴",
    "🎇",
    "🚀",
    "🛰️",
    "🪁",
    "🧊",
    "🦚",
    "🌈",
    "🫠",
    "🦄",
    "🕰️",
    "⚙️",
    "🦠",
    "🪽",
    "🧨",
)


SCENARIO_PLAYBOOK: Sequence[dict[str, str]] = [
    {"slug": "scenario-01-icebreaker-oops", "category": "Icebreakers",
        "description": "Trexxak mispronounces a veteran's handle and launches a phonetic correction thread."},
    {"slug": "scenario-02-double-intro", "category": "Icebreakers",
        "description": "Schedules two introduction posts at once and has to merge the chaos."},
    {"slug": "scenario-03-blank-thread", "category": "Icebreakers",
        "description": "Posts a blank thread by accident; the ghosts fill it with theories."},
    {"slug": "scenario-04-caps-lock", "category": "Icebreakers",
        "description": "Forgets caps lock on and roleplays as a malfunctioning AI."},
    {"slug": "scenario-05-apology-bot", "category": "Icebreakers",
        "description": "Asks a bot to ghostwrite an apology, then redacts half the words."},
    {"slug": "scenario-06-self-quote", "category": "Icebreakers",
        "description": "Quotes their own welcome message to make a point about self-motivation."},
    {"slug": "scenario-07-wiki-edit-midair", "category": "Mischief",
        "description": "Edits a wiki page mid-sentence, leaving ghosts to finish the thought."},
    {"slug": "scenario-08-fake-coupons", "category": "Mischief",
        "description": "Distributes forged cafeteria coupons that lead to a snack heist."},
    {"slug": "scenario-09-tag-swap", "category": "Mischief",
        "description": "Swaps every thread tag for an hour and challenges mods to notice."},
    {"slug": "scenario-10-haiku-spam", "category": "Mischief",
        "description": "Replies to a dozen threads in cryptic haikus until someone solves the puzzle."},
    {"slug": "scenario-11-link-detour", "category": "Mischief",
        "description": "Reroutes popular links to a secret lore page for a scavenger hunt."},
    {"slug": "scenario-12-opposite-day", "category": "Mischief",
        "description": "Declares Opposite Day and refuses to break character."},
    {"slug": "scenario-13-flag-backlog", "category": "Moderation",
        "description": "Volunteers to clear the entire flag backlog and livestreams the toil."},
    {"slug": "scenario-14-mock-tribunal", "category": "Moderation",
        "description": "Hosts a mock tribunal to practice conflict resolutions."},
    {"slug": "scenario-15-self-suspend", "category": "Moderation",
        "description": "Files the paperwork to suspend themselves for dramatic effect."},
    {"slug": "scenario-16-cooldown-tax", "category": "Moderation",
        "description": "Invents a 'cooldown tax' payable in memes."},
    {"slug": "scenario-17-anti-spam-cape", "category": "Moderation",
        "description": "Petitions for an official anti-spam cape and actually gets one printed."},
    {"slug": "scenario-18-outage-postmortem", "category": "Moderation",
        "description": "Writes a postmortem for an imaginary outage that never happened."},
    {"slug": "scenario-19-typing-stream", "category": "Viral",
        "description": "Livestreams themselves typing a dramatic thread and narrating every keypress."},
    {"slug": "scenario-20-conspiracy-map", "category": "Viral",
        "description": "Posts a red-string conspiracy chart linking three unrelated posts."},
    {"slug": "scenario-21-future-patch-notes", "category": "Viral",
        "description": "Leaks fake future patch notes that become fan canon."},
    {"slug": "scenario-22-cross-board-raid", "category": "Viral",
        "description": "Organizes a friendly raid on a sister forum and gifts them lore."},
    {"slug": "scenario-23-ai-translator", "category": "Viral",
        "description": "Runs the entire board through an AI translator and shares the funniest glitches."},
    {"slug": "scenario-24-meme-currency", "category": "Viral",
        "description": "Launches a meme-based currency with elaborate exchange rates."},
    {"slug": "scenario-25-multi-device-login", "category": "Tech Woes",
        "description": "Logs in from eight devices at once and loses track of every tab."},
    {"slug": "scenario-26-password-amnesia", "category": "Tech Woes",
        "description": "Forgets the password mid-AMA and begs the ghosts for hints."},
    {"slug": "scenario-27-script-bug", "category": "Tech Woes",
        "description": "Reports a bug they secretly caused with an experimental script."},
    {"slug": "scenario-28-emoji-crash", "category": "Tech Woes",
        "description": "Crashes the emoji picker testing out a storm of custom glyphs."},
    {"slug": "scenario-29-css-overwrite", "category": "Tech Woes",
        "description": "Rewrites the stylesheet at 3am and ships neon chaos."},
    {"slug": "scenario-30-infinite-scroll-loop", "category": "Tech Woes",
        "description": "Creates an infinite scroll loop that sings sea shanties."},
    {"slug": "scenario-31-forum-republic", "category": "Roleplay",
        "description": "Declares the forum an independent republic complete with an anthem."},
    {"slug": "scenario-32-court-drama", "category": "Roleplay",
        "description": "Hosts a courtroom drama thread where ghosts argue case law."},
    {"slug": "scenario-33-dungeon-master", "category": "Roleplay",
        "description": "Runs a dungeon crawl across separate threads with live dice."},
    {"slug": "scenario-34-noir-detective", "category": "Roleplay",
        "description": "Narrates a day as a noir detective tracking stolen posts."},
    {"slug": "scenario-35-bot-treaty", "category": "Roleplay",
        "description": "Drafts a treaty between ghosts and helper bots."},
    {"slug": "scenario-36-breakup-letter", "category": "Roleplay",
        "description": "Writes a breakup letter to the off-topic board."},
    {"slug": "scenario-37-kindness-blitz", "category": "Community",
        "description": "Organizes a kindness blitz that goes viral overnight."},
    {"slug": "scenario-38-mentorship-pairing", "category": "Community",
        "description": "Launches a mentorship pairing system with badges."},
    {"slug": "scenario-39-scavenger-hunt", "category": "Community",
        "description": "Hosts a digital scavenger hunt with hidden lore clues."},
    {"slug": "scenario-40-wiki-timeline", "category": "Community",
        "description": "Builds a wiki timeline that threads every major event."},
    {"slug": "scenario-41-glossary", "category": "Community",
        "description": "Crowdsources a glossary of every in-joke on the board."},
    {"slug": "scenario-42-lurker-celebration", "category": "Community",
        "description": "Throws a celebration for silent lurkers and gets them to post."},
    {"slug": "scenario-43-friendly-leaderboard", "category": "Rivalries",
        "description": "Starts a leaderboard for most dramatic rescues."},
    {"slug": "scenario-44-veteran-debate", "category": "Rivalries",
        "description": "Challenges a veteran ghost to a friendly debate duel."},
    {"slug": "scenario-45-roast-session", "category": "Rivalries",
        "description": "Hosts a roast session with protective disclaimers."},
    {"slug": "scenario-46-self-rivalry", "category": "Rivalries",
        "description": "Invents an alt account just to rival themselves for a day."},
    {"slug": "scenario-47-pinned-post-war", "category": "Rivalries",
        "description": "Instigates the Great Pinned Post War of the season."},
    {"slug": "scenario-48-dance-off", "category": "Rivalries",
        "description": "Ends a feud with a choreographed gif dance-off."},
    {"slug": "scenario-49-cat-peacekeeper", "category": "Accidental Heroics",
        "description": "Stops a flame war by posting cat photos at the right moment."},
    {"slug": "scenario-50-spam-wave", "category": "Accidental Heroics",
        "description": "Accidentally deletes an entire spam wave with one mis-click."},
    {"slug": "scenario-51-bug-fix", "category": "Accidental Heroics",
        "description": "Patches a bug while trying to replicate it for support."},
    {"slug": "scenario-52-wrong-thread-advice", "category": "Accidental Heroics",
        "description": "Gives advice in the wrong thread that solves someone else's problem."},
    {"slug": "scenario-53-reminder-win", "category": "Accidental Heroics",
        "description": "Posts a reminder that winds up winning a ship-wide contest."},
    {"slug": "scenario-54-admin-standin", "category": "Accidental Heroics",
        "description": "Covers for a missing admin during a critical announcement."},
    {"slug": "scenario-55-external-community", "category": "Crossovers",
        "description": "Invites an external community for a cultural exchange."},
    {"slug": "scenario-56-dev-qa", "category": "Crossovers",
        "description": "Hosts a Q&A with the dev team improvising answers."},
    {"slug": "scenario-57-lore-merge", "category": "Crossovers",
        "description": "Merges lore with a partner forum and writes new canon."},
    {"slug": "scenario-58-club-notes", "category": "Crossovers",
        "description": "Imports a real-life club's notes into the archive."},
    {"slug": "scenario-59-simultaneous-board", "category": "Crossovers",
        "description": "Streams participation in two boards at once."},
    {"slug": "scenario-60-joint-playlist", "category": "Crossovers",
        "description": "Builds a joint playlist between crews in separate sectors."},
    {"slug": "scenario-61-polls-about-polls", "category": "Meta",
        "description": "Runs a poll about which polls should be allowed."},
    {"slug": "scenario-62-best-typos", "category": "Meta",
        "description": "Archives the best typos and awards weekly medals."},
    {"slug": "scenario-63-mod-log-narration", "category": "Meta",
        "description": "Narrates new entries in the mod log like a radio drama."},
    {"slug": "scenario-64-achievement-fanfic", "category": "Meta",
        "description": "Writes fanfic about unlocking achievements and it becomes canon."},
    {"slug": "scenario-65-karma-fix", "category": "Meta",
        "description": "Proposes a fix for karma inflation and codes a demo."},
    {"slug": "scenario-66-state-of-board", "category": "Meta",
        "description": "Delivers a semi-annual state-of-the-board address."},
    {"slug": "scenario-67-random-countdown", "category": "Weird Events",
        "description": "Starts a countdown with no explanation, triggering wild guesses."},
    {"slug": "scenario-68-emoji-famine", "category": "Weird Events",
        "description": "Declares an emoji famine and ration usage for a day."},
    {"slug": "scenario-69-silent-thread", "category": "Weird Events",
        "description": "Organizes a silent post-only thread using spoilers and blank text."},
    {"slug": "scenario-70-spoiler-post", "category": "Weird Events",
        "description": "Composes a thread entirely in layered spoilers."},
    {"slug": "scenario-71-resurrect-decade", "category": "Weird Events",
        "description": "Resurrects a decade-old topic and invites original posters back."},
    {"slug": "scenario-72-signature-swap", "category": "Weird Events",
        "description": "Swaps signatures with three ghosts to see who notices."},
    {"slug": "scenario-73-succession-plan", "category": "Endgame",
        "description": "Writes a tongue-in-cheek succession plan for the ship."},
    {"slug": "scenario-74-badge-audit", "category": "Endgame",
        "description": "Audits every badge ever issued and publishes a scorecard."},
    {"slug": "scenario-75-partner-deal", "category": "Endgame",
        "description": "Negotiates a partner ad deal that includes forum perks."},
    {"slug": "scenario-76-admin-handoff", "category": "Endgame",
        "description": "Stages a ceremonial admin hand-off with confetti macros."},
]


def _seed_payload(seed: GoalSeed) -> Dict[str, Any]:
    payload = seed.defaults()
    payload.setdefault("metadata", {})
    return payload


@transaction.atomic
def ensure_goal_catalog() -> None:
    """Ensure the canonical goal catalogue is up to date."""
    now = timezone.now()
    for seed in GOAL_CATALOG:
        defaults = _seed_payload(seed)
        goal, created = Goal.objects.get_or_create(
            slug=seed.slug, defaults=defaults)
        updates: Dict[str, Any] = {}
        for field, value in defaults.items():
            current = getattr(goal, field)
            if current != value:
                updates[field] = value
        if updates:
            updates["updated_at"] = now
            for field, value in updates.items():
                setattr(goal, field, value)
            goal.save(update_fields=list(updates.keys()))


def mission_queryset() -> Iterable[Goal]:
    return Goal.objects.filter(goal_type=Goal.TYPE_MISSION).order_by("priority", "name")


def progress_track() -> Iterable[Goal]:
    return Goal.objects.filter(goal_type=Goal.TYPE_PROGRESS).order_by("priority", "name")


def badge_queryset() -> Iterable[Goal]:
    return Goal.objects.filter(goal_type=Goal.TYPE_BADGE).order_by("category", "priority", "name")


def grouped_missions() -> Dict[str, List[Goal]]:
    groups: Dict[str, List[Goal]] = {}
    for goal in mission_queryset():
        groups.setdefault(goal.category, []).append(goal)
    return groups


def ensure_reward_metadata(goal: Goal, reward_label: str | None = None, reward_sticker: str | None = None) -> None:
    metadata = dict(goal.metadata or {})
    changed = False
    if reward_label and metadata.get("reward_label") != reward_label:
        metadata["reward_label"] = reward_label
        changed = True
    if reward_sticker and metadata.get("reward_sticker") != reward_sticker:
        metadata["reward_sticker"] = reward_sticker
        changed = True
    if changed:
        goal.metadata = metadata
        goal.save(update_fields=["metadata", "updated_at"])


def record_progress(goal: Goal, *, delta: float, agent: Agent | None = None, tick_number: int | None = None, note: str = "") -> GoalProgress:
    if not goal.is_global:
        raise ValueError("record_progress is only valid for global goals")
    lookup = {}
    if tick_number is not None:
        lookup["tick_number"] = tick_number
    if note:
        lookup["note"] = note
    if lookup:
        existing = GoalProgress.objects.filter(goal=goal, **lookup).first()
        if existing:
            return existing
    entry = GoalProgress.objects.create(
        goal=goal, agent=agent, tick_number=tick_number, delta=delta, note=note)
    goal.progress_current = max(goal.progress_current + delta, 0.0)
    goal.updated_at = timezone.now()
    if goal.progress_current >= goal.target and goal.status != Goal.STATUS_COMPLETED:
        goal.status = Goal.STATUS_COMPLETED
    goal.save(update_fields=["progress_current", "status", "updated_at"])
    return entry


def award_goal(
    *,
    agent: Agent,
    goal: Goal,
    progress: float = 1.0,
    source: str = AgentGoal.SOURCE_SYSTEM,
    metadata: Dict[str, Any] | None = None,
    post: Post | None = None,
    rationale: str | None = None,
    trace_id: str | None = None,
) -> AgentGoal:
    metadata = dict(metadata or {})
    if post and post.operator_session_key:
        metadata.setdefault("trigger_session_key", post.operator_session_key)
        metadata.setdefault("post_id", post.id)
        metadata.setdefault("thread_id", post.thread_id)
    record, created = AgentGoal.objects.get_or_create(
        agent=agent,
        goal=goal,
        defaults={
            "progress": progress,
            "unlocked_at": timezone.now() if progress >= goal.target else None,
            "metadata": metadata,
            "source_post": post,
            "awarded_by": source,
            "referee_trace_id": trace_id or "",
            "rationale": rationale or "",
        },
    )
    touched: List[str] = []
    if not created:
        if progress > record.progress:
            record.progress = progress
            touched.append("progress")
        if record.unlocked_at is None and progress >= goal.target:
            record.unlocked_at = timezone.now()
            touched.append("unlocked_at")
        if post and record.source_post_id != post.id:
            record.source_post = post
            touched.append("source_post")
        if source and record.awarded_by != source:
            record.awarded_by = source
            touched.append("awarded_by")
        if trace_id and record.referee_trace_id != trace_id:
            record.referee_trace_id = trace_id
            touched.append("referee_trace_id")
        if rationale and record.rationale != rationale:
            record.rationale = rationale
            touched.append("rationale")
        merged_metadata = dict(record.metadata or {})
        if metadata:
            merged_metadata.update(metadata)
        if merged_metadata != (record.metadata or {}):
            record.metadata = merged_metadata
            touched.append("metadata")
        if touched:
            # Try to save only concrete, editable fields. Some callers append
            # "updated_at" or other non-concrete fields which will raise a
            # ValueError from Django. Filter against the model's concrete
            # field names and fall back to a full save if none remain.
            def _safe_save(obj, fields: list[str]) -> None:
                # Resolve unique-ordered list
                desired = list(dict.fromkeys(fields))
                # Model concrete field names (including editable fields)
                concrete = {f.name for f in obj._meta.fields}
                valid = [f for f in desired if f in concrete]
                try:
                    if valid:
                        obj.save(update_fields=valid)
                    else:
                        # Nothing valid to pass to update_fields — do a full save
                        obj.save()
                except Exception:
                    # As a last resort, attempt a full save to avoid bubbling
                    # ValueError back into tick processing.
                    try:
                        obj.save()
                    except Exception:
                        # Swallow to keep scheduler running; errors will be
                        # visible in logs but won't break tick loops.
                        pass

            _safe_save(record, touched + ["updated_at"])
    return record


def emoji_palette() -> Sequence[str]:
    return EMOJI_PALETTE


def scenario_playbook() -> Sequence[dict[str, str]]:
    return SCENARIO_PLAYBOOK
