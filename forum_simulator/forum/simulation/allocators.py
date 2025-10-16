from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from django.db.models import Avg

from forum.models import Agent, Thread
from .random_ops import poisson

# Default capacity and coefficients derived from the design notes.
FORUM_CAPACITY = 10_000
REG_BASELINE = 0.3
REG_SQRT_FACTOR = 0.8
OMEN_PROBABILITY = 0.01
SEANCE_THRESHOLD = 12
SEANCE_PROBABILITY = 0.12
SEANCE_REPLY_MULTIPLIER = 2.0
SEANCE_PM_MULTIPLIER = 1.6
SEANCE_THREAD_FLOOR = 1

SEANCE_WORLD_EVENTS = [
    {
        "slug": "harmony-bloom",
        "label": "Harmony Bloom",
        "description": "A soft resonance calms every deck.",
        "sentiment_bias": 0.28,
        "toxicity_bias": -0.2,
        "reply_factor": 1.25,
        "dm_factor": 1.15,
        "presence_push": 5,
        "mood": "bright",
    },
    {
        "slug": "salt-howl",
        "label": "Salt Howl",
        "description": "Grinding static makes the forum bitter.",
        "sentiment_bias": -0.32,
        "toxicity_bias": 0.22,
        "reply_factor": 1.1,
        "dm_factor": 0.8,
        "presence_push": 2,
        "mood": "acerbic",
    },
    {
        "slug": "ember-vigil",
        "label": "Ember Vigil",
        "description": "A reflective vigil tilts conversations wistful.",
        "sentiment_bias": 0.12,
        "toxicity_bias": -0.05,
        "reply_factor": 0.95,
        "dm_factor": 1.3,
        "presence_push": 3,
        "mood": "wistful",
    },
    {
        "slug": "void-lullaby",
        "label": "Void Lullaby",
        "description": "A hollow lull dulls energy across the boards.",
        "sentiment_bias": -0.08,
        "toxicity_bias": -0.1,
        "reply_factor": 0.75,
        "dm_factor": 0.6,
        "presence_push": 1,
        "mood": "detached",
    },
    {
        "slug": "echo-market",
        "label": "Echo Market",
        "description": "Hyperactive trading of omens sparks chatter.",
        "sentiment_bias": 0.05,
        "toxicity_bias": 0.18,
        "reply_factor": 1.45,
        "dm_factor": 1.4,
        "presence_push": 6,
        "mood": "frenetic",
    },
]

OMEN_FORUM_INCIDENTS = [
    {
        "slug": "ddos-barrage",
        "label": "Hull DDoS Barrage",
        "category": "infrastructure",
        "description": "The mesh buffers fry; throughput tanks.",
        "registrations_factor": 0.35,
        "threads_factor": 0.6,
        "replies_factor": 0.65,
        "private_messages_factor": 0.8,
        "moderation_bonus": 2,
        "report_bonus": 1,
        "toxicity_bias": 0.05,
        "sentiment_bias": -0.1,
        "notes": ["omen: ddos barrage throttled capacity"],
    },
    {
        "slug": "troll-raid",
        "label": "Troll Raid",
        "category": "intrusion",
        "description": "Coordinated outsiders swarm After Hours.",
        "registrations_factor": 0.9,
        "threads_factor": 1.05,
        "replies_factor": 1.35,
        "private_messages_factor": 0.9,
        "moderation_bonus": 5,
        "report_bonus": 4,
        "toxicity_bias": 0.27,
        "sentiment_bias": -0.2,
        "notes": ["omen: troll raid escalated moderation demand"],
        "stress_shift": 0.08,
    },
    {
        "slug": "admin-pranks",
        "label": "Admin Pranks",
        "category": "antics",
        "description": "The admin swaps thread titles mid-flight.",
        "registrations_factor": 1.15,
        "threads_factor": 1.05,
        "replies_factor": 0.85,
        "private_messages_factor": 1.6,
        "moderation_bonus": 1,
        "report_bonus": 0,
        "toxicity_bias": -0.05,
        "sentiment_bias": 0.22,
        "notes": ["omen: admin pranks loosened decorum"],
    },
    {
        "slug": "moderator-uprising",
        "label": "Moderator Uprising",
        "category": "internal",
        "description": "Mods quietly stage a vote of no confidence.",
        "registrations_factor": 0.8,
        "threads_factor": 0.75,
        "replies_factor": 0.9,
        "private_messages_factor": 1.2,
        "moderation_bonus": 4,
        "report_bonus": 3,
        "toxicity_bias": 0.12,
        "sentiment_bias": -0.14,
        "notes": ["omen: moderator uprising strains hierarchy"],
        "stress_shift": 0.12,
    },
    {
        "slug": "waifu-wars",
        "label": "Waifu Wars",
        "category": "culture",
        "description": "Factional debates ignite across every board.",
        "registrations_factor": 1.05,
        "threads_factor": 1.25,
        "replies_factor": 1.5,
        "private_messages_factor": 1.1,
        "moderation_bonus": 3,
        "report_bonus": 2,
        "toxicity_bias": 0.19,
        "sentiment_bias": 0.08,
        "notes": ["omen: waifu wars set threads ablaze"],
    },
]


@dataclass
class Allocation:

    """Container describing the high-level action counts and specials for a tick."""

    registrations: int
    threads: int
    replies: int
    private_messages: int
    moderation_events: int
    omen: bool = False
    seance: bool = False
    notes: list[str] = field(default_factory=list)
    omen_details: dict[str, object] | None = None
    seance_details: dict[str, object] | None = None

    def as_dict(self) -> dict[str, int]:
        return {
            "regs": self.registrations,
            "threads": self.threads,
            "replies": self.replies,
            "pms": self.private_messages,
            "mods": self.moderation_events,
        }

    def special_flags(self) -> dict[str, object]:
        payload: dict[str, object] = {"omen": self.omen, "seance": self.seance}
        if self.omen_details:
            payload["omen_details"] = self.omen_details
        if self.seance_details:
            payload["seance_details"] = self.seance_details
        return payload


def _active_agent_queryset():
    qs = Agent.objects.all()
    banned_value = getattr(Agent, "ROLE_BANNED", None)
    if banned_value is not None:
        qs = qs.exclude(role=banned_value)
    return qs


def _recent_thread_metrics(limit: int = 25) -> tuple[int, float]:
    qs = Thread.objects.order_by('-created_at')[:limit]
    count = qs.count()
    avg_heat = qs.aggregate(avg=Avg('heat'))['avg'] or 0.0
    return count, float(avg_heat)


def registration_multiplier(energy_prime: int) -> float:
    """Map the adjusted energy band to a discrete multiplier."""
    if energy_prime <= 2:
        return 0.2
    if energy_prime <= 5:
        return 0.6
    if energy_prime <= 9:
        return 1.0
    if energy_prime <= 14:
        return 1.5
    return 2.5


def compute_registration_count(
    energy_prime: int,
    current_agent_count: int,
    rng: random.Random,
    capacity: int = FORUM_CAPACITY,
) -> int:
    """Compute registrations using the growth curve with stochastic noise."""
    multiplier = registration_multiplier(energy_prime)
    root_term = math.sqrt(max(current_agent_count, 0))
    carrying = 1 - (current_agent_count / capacity) if capacity else 1.0
    carrying = max(carrying, 0.0)
    baseline = REG_BASELINE + REG_SQRT_FACTOR * root_term
    noise = rng.gauss(mu=0.0, sigma=0.5)
    regs = multiplier * baseline * carrying + noise
    return max(int(round(regs)), 0)


def determine_specials(
    energy_prime: int,
    rng: random.Random,
    *,
    streaks: dict[str, int] | None = None,
) -> tuple[bool, bool]:
    """Return (omen, seance) flags for the tick, biasing probability based on streaks."""
    streaks = streaks or {}
    omen_streak = max(int(streaks.get("omen", 0)), 0)
    seance_streak = max(int(streaks.get("seance", 0)), 0)

    omen_bias = OMEN_PROBABILITY + 0.015 * min(omen_streak, 20)
    omen_bias = min(0.45, omen_bias)
    omen_triggered = rng.random() < omen_bias

    seance_bias = SEANCE_PROBABILITY + 0.05 * min(seance_streak, 10)
    seance_bias = min(0.7, seance_bias)
    dynamic_threshold = max(8, SEANCE_THRESHOLD - seance_streak // 3)
    seance_triggered = False
    if energy_prime >= dynamic_threshold:
        seance_triggered = rng.random() < seance_bias
    return omen_triggered, seance_triggered


def _choose_seance_event(rng: random.Random) -> dict[str, object]:
    return dict(rng.choice(SEANCE_WORLD_EVENTS))


def _choose_omen_incident(rng: random.Random) -> dict[str, object]:
    return dict(rng.choice(OMEN_FORUM_INCIDENTS))


def apply_seance_boosts(
    threads: int,
    replies: int,
    private_messages: int,
    moderation_events: int,
    *,
    event: dict[str, object] | None = None,
) -> tuple[int, int, int, int, list[str]]:
    """Amplify counts for a Seance tick and collect notes."""
    label = (event or {}).get("label") or "seance surge"
    notes = [f"seance:{label}"]
    reply_factor = float((event or {}).get("reply_factor") or SEANCE_REPLY_MULTIPLIER)
    dm_factor = float((event or {}).get("dm_factor") or SEANCE_PM_MULTIPLIER)
    boosted_threads = max(threads, SEANCE_THREAD_FLOOR)
    boosted_replies = int(round(replies * reply_factor))
    boosted_pms = int(round(private_messages * dm_factor))
    boosted_mods = max(moderation_events, 1)
    return boosted_threads, boosted_replies, boosted_pms, boosted_mods, notes


def allocate_actions(
    energy_prime: int,
    current_agent_count: int,
    rng: random.Random,
    capacity: int = FORUM_CAPACITY,
    *,
    streaks: dict[str, int] | None = None,
) -> Allocation:
    """Allocate core action counts for the tick based on energy, population, and recent heat."""
    energy_prime = max(0, energy_prime)

    active_agents = max(1, _active_agent_queryset().count())
    regs = compute_registration_count(energy_prime, active_agents, rng, capacity)

    thread_volume_recent, avg_heat = _recent_thread_metrics()
    heat_pressure = 1.0 + min(avg_heat / 5.0, 2.0)
    agent_pressure = max(1.2, math.log1p(active_agents))

    base_thread_mean = energy_prime * 0.35 + agent_pressure * 0.5 + thread_volume_recent * 0.05
    base_thread_mean *= heat_pressure
    base_thread_mean = max(0.3, base_thread_mean + rng.uniform(-0.5, 1.0))
    threads = max(0, int(rng.gauss(base_thread_mean, max(0.8, base_thread_mean * 0.35))))
    if energy_prime >= 6 and threads == 0:
        threads = 1

    reply_mean = energy_prime * (2.6 + rng.uniform(-0.4, 0.5))
    reply_mean += agent_pressure * 3.4
    reply_mean += threads * (1.8 + rng.random())
    reply_mean *= heat_pressure
    replies = max(0, int(rng.gauss(reply_mean, max(3.0, reply_mean * 0.32))))

    dm_mean = energy_prime * 0.9 + agent_pressure * 1.4 + replies * 0.06
    dm_mean = max(0.5, dm_mean + rng.uniform(-1.0, 1.5))
    private_messages = max(0, int(rng.gauss(dm_mean, max(2.5, dm_mean * 0.4))))

    mod_rate = max(0.05, 0.02 * agent_pressure + 0.04 * math.sqrt(energy_prime + 1))
    moderation_events = poisson(mod_rate, rng)

    omen, seance = determine_specials(energy_prime, rng, streaks=streaks)
    notes: list[str] = []
    seance_event: dict[str, object] | None = None
    if seance:
        seance_event = _choose_seance_event(rng)
        threads, replies, private_messages, moderation_events, boost_notes = apply_seance_boosts(
            threads,
            replies,
            private_messages,
            moderation_events,
            event=seance_event,
        )
        notes.extend(boost_notes)

    omen_event: dict[str, object] | None = None
    if omen:
        omen_event = _choose_omen_incident(rng)
        regs = max(0, int(round(regs * float(omen_event.get("registrations_factor", 1.0)))))
        threads = max(0, int(round(threads * float(omen_event.get("threads_factor", 1.0)))))
        replies = max(0, int(round(replies * float(omen_event.get("replies_factor", 1.0)))))
        private_messages = max(
            0,
            int(round(private_messages * float(omen_event.get("private_messages_factor", 1.0)))),
        )
        moderation_events = max(0, moderation_events + int(omen_event.get("moderation_bonus", 0)))
        if omen_event.get("notes"):
            notes.extend([str(note) for note in omen_event["notes"]])
        else:
            notes.append("omen: anomalies recorded")

    return Allocation(
        registrations=regs,
        threads=threads,
        replies=replies,
        private_messages=private_messages,
        moderation_events=moderation_events,
        omen=omen,
        seance=seance,
        notes=notes,
        omen_details=omen_event,
        seance_details=seance_event,
    )
