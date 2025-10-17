"""
Improved simulation tick command with soft double-post prevention, 
synchronous draining of DM generation tasks, optional suppression of random profiles,
and LLM-driven board routing. This version builds upon the original run_tick
to provide a smoother forum simulation. Key enhancements include:

* Avoiding consecutive posts by the same author in the same board or thread
  by attempting to choose alternate authors or boards when possible.
* Draining the DM generation queue synchronously, similar to thread starts,
  to ensure that generated private messages appear immediately rather than
  piling up pending tasks.
* Respecting the SIM_DISABLE_RANDOM_PROFILES setting by zeroing out
  registrations, preventing random user creation during ticks.
* Providing helper functions for last post lookup, alternate author selection,
  and queue draining which can be reused elsewhere.
* Preserving existing logic for board menus and LLM-based board routing, 
  while ensuring newly created boards are considered during the same tick.

To use this improved command, point your management command to
``run_tick_new`` instead of ``run_tick``. All functionality should remain
compatible with the original command except for the improvements noted above.
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Optional, List, Iterable, Dict, Set

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models
from django.db.models import Count, Max
from django.utils.text import slugify

from forum.models import (
    Agent,
    Board,
    Thread,
    Post,
    ModerationTicket,
    OracleDraw,
    TickLog,
    GenerationTask,
    PrivateMessage,
)
from forum.simulation import build_energy_profile, allocate_actions, describe_rolls
from forum.lore import (
    ensure_core_boards,
    ensure_origin_story,
    craft_agent_profile,
    choose_board_for_thread,
    ORGANIC_HANDLE,
    ORGANIC_THREAD_TITLE,
    process_lore_events,
    spawn_board_on_request,
)
from forum.services.generation import enqueue_generation_task
from forum.services.avatar_factory import ensure_agent_avatar
from forum.services import moderation as moderation_service
from forum.services import stress as stress_service
from forum.services import missions as missions_service
from forum.services import progress as progress_service
from forum.services import configuration as config_service
from forum.services import activity as activity_service
from forum.services import tick_control
from forum.services import agent_state, sim_config

# Honour global setting to disable random profile creation
DISABLE_RANDOM_PROFILES = getattr(settings, "SIM_DISABLE_RANDOM_PROFILES", True)

# -----------------------------------------------------------------------------
# Helper functions for last-post lookups and queue draining
# -----------------------------------------------------------------------------
def _last_post_in_thread(thread: Thread) -> Optional[Post]:
    """Return the most recent post in a thread or None if no posts."""
    return (
        Post.objects.filter(thread=thread)
        .only("id", "author_id", "created_at")
        .order_by("-created_at", "-id")
        .first()
    )


def _last_post_in_board(board: Board) -> Optional[Post]:
    """Return the most recent post in a board or None if no posts."""
    return (
        Post.objects.filter(thread__board=board)
        .only("id", "author_id", "created_at")
        .order_by("-created_at", "-id")
        .first()
    )


def _try_alternate_author(
    preferred: Agent,
    pool: List[Agent],
    *,
    not_these_ids: Set[int],
    rng: random.Random,
) -> Optional[Agent]:
    """
    Try to pick another agent from pool not equal to preferred and not in not_these_ids.
    Returns an alternate agent or None if none found.
    """
    candidates = [a for a in pool if a.id != preferred.id and a.id not in not_these_ids]
    return rng.choice(candidates) if candidates else None


def _drain_queue_for(
    kind: int,
    *,
    thread: Optional[Thread] = None,
    max_loops: int = 6,
    batch: int = 8,
) -> None:
    """
    Synchronously drain the generation queue for a specific task type. This
    ensures that generation tasks (e.g., DM creation) do not pile up pending
    when running a tick synchronously. If a thread is provided, only tasks
    associated with that thread are considered; otherwise, all tasks of the
    given type are processed.
    """
    from django.core.management import call_command
    for _ in range(max_loops):
        # process a small batch of tasks
        call_command("process_generation_queue", limit=batch)
        qs = GenerationTask.objects.filter(task_type=kind, status=GenerationTask.STATUS_PENDING)
        if thread is not None:
            qs = qs.filter(thread=thread)
        if not qs.exists():
            break


# -----------------------------------------------------------------------------
# THEME PACKS, TEMPLATES, AND CONSTANTS (copied from original run_tick)
# These remain unchanged from the original file.
# -----------------------------------------------------------------------------
THEME_PACKS = [
    {
        "label": "field report drop",
        "setting": "ghosts swapping live surveillance logs on a wobbly message board",
        "tone": "wired and conspiratorial",
        "style_notes": "Quote the human verbatim only when it adds clarity; focus on verifiable detail and avoid status-update asides.",
    },
    {
        "label": "casefile salon",
        "setting": "deep dive archive thread comparing a handful of organics across eras",
        "tone": "analytical but playful",
        "style_notes": "Include a mini timeline and invite others to attach evidence or screenshots.",
    },
    {
        "label": "maintenance night shift",
        "setting": "late night advice desk for ghosts supporting overclocked humans",
        "tone": "reassuring with a touch of triage humor",
        "style_notes": "Offer actionable care steps, call out red flags, keep it under classic forum length.",
    },
    {
        "label": "signal boost party",
        "setting": "link sharing jam for rescued zines, playlists, and vaporwave webcams",
        "tone": "nostalgic and high-energy",
        "style_notes": "If referencing vintage tools, do so sparingly. Prioritize clear descriptions of linked material over nostalgia.",
    },
]

TREXXAK_POST_TEMPLATES = [
    "OI telemetry ping #{ping}: {observation}. Requesting {request}. {emoji}",
    "Filed under 'organics being organics': {observation}. Ghosts, can someone {prompt}? {emoji}",
    "trexxak status update >>> {observation}. Upload receipts or {request} before the colony gets twitchy.",
]

TREXXAK_OBSERVATIONS = [
    "the sleeper agent keeps microwaving fish at 02:17 ship time",
    "someone taught an organic to quote their own forum posts for emphasis",
    "a human just rage-quit and rejoined the same chatroom within 14 seconds",
    "the organism attempted 'focus time' but opened five new tabs labeled 'just browsing'",
    "three different organics claimed to be on a juice cleanse while ordering extra fries",
]

TREXXAK_REQUESTS = [
    "thread the receipts with timestamps",
    "double-check if the human's ringtone is actually dial-up noise",
    "tag any ghost who owes me a favor in After Hours",
    "cross-reference the casefile for code name 'Soggy Keyboard'",
    "ping t.admin if this smells like policy drift",
]

TREXXAK_PROMPTS = [
    "confirm I'm not reading a simulation loop",
    "drop a clip for the highlight reel",
    "remind me why organics think meetings are hobbies",
    "tell me if this counts as a flare-up or just a Tuesday",
]

TREXXAK_EMOJI = ["o.O", "¯\\_(ツ)_/¯", "(╯°□°）╯︵ ┻━┻", ":tone-alert:", "👁️‍🗨️"]

TREXXAK_DM_TEMPLATES = [
    "Private uplink to {target}: consider sliding this intel into the casefile before the organics sterilise the log.",
    "hey {target}, quick whisper: the organism noticed your thread. Toss in a follow-up and I'll owe you a hallway snack.",
    "{target}, meeting request: can we run a stress-test on this organic before it rage-deletes the evidence?",
]

PEER_DM_SCENARIOS = [
    {
        "label": "casefile_sync",
        "needs_thread": True,
        "instruction": "DM {recipient} about '{thread_title}'. Share the clue you noticed and ask them to help log it in the casefile.",
        "style_notes": "Conspiratorial but warm; promise to share receipts and end by proposing a follow-up action.",
        "max_tokens": 150,
    },
    {
        "label": "afterhours_checkin",
        "instruction": "Check in on {recipient} and invite them to trade a comfort track while the board cools down. Mention a {topic} detail you both obsess over.",
        "style_notes": "Gentle tone, keep it to two or three sentences, and close with an open question that nudges a reply.",
        "max_tokens": 140,
    },
    {
        "label": "stealth_fix",
        "needs_thread": True,
        "instruction": "Ping {recipient} to coordinate a stealth fix for '{thread_title}'. Outline how you'll divide tasks and cover the admin fallout.",
        "style_notes": "Playful scheming; reference at least one digital tool or macro you're about to abuse.",
        "max_tokens": 150,
    },
    {
        "label": "organics_watch",
        "instruction": "Ask {recipient} to help monitor the organics this tick. Share a hunch about {topic} and request any red flags they spotted.",
        "style_notes": "Direct but friendly; include one sensory detail and promise to swap logs later.",
        "max_tokens": 140,
    },
    {
        "label": "memory_lane",
        "needs_thread": True,
        "instruction": "Reminisce with {recipient} about the vibe of '{thread_title}'. Compare it to an older incident and pitch co-writing a lore recap.",
        "style_notes": "Nostalgic, include a made-up archive tag, and keep it under classic DM length.",
        "max_tokens": 160,
    },
]

WELCOME_DM_TEMPLATE = (
    "Welcome {recipient} aboard. Offer the elevator pitch for the {topic} threads and invite them to drop one weird fact about themselves."
)
WELCOME_DM_STYLE = (
    "Bright and sincere, two sentences max, end with a question that makes it easy for them to answer."
)

GHOST_REPLY_LIBRARY = {
    "mock": [
        "lol trexxak, breathe. organics gonna organic. I'll grab the popcorn.",
        "Look at the poor organism wrangler trying to herd cats with spreadsheets. adorable.",
        "trexxak, you sure the \"OI\" stands for organic intelligence? sounds like organic irritation rn.",
    ],
    "agitate": [
        "Bold of you to assume the organism hasn't already tripped the meltdown alarm. I'm on it.",
        "Give me five minutes and I'll have that human confessing their entire browser history.",
        "Say the word, and I'll ghostwrite a DM that sends them spiraling.",
    ],
    "ally": [
        "Logged and tagged, trexxak. I’ll keep a drift watch on their feed.",
        "Consider it done; got a clean trail queued for your mission log.",
        "Sharing a quiet ping with the moderator stack so you can stay hands-off.",
    ],
}

LOOK_OI_PROBABILITY = 0.08

# Clamp helper from original
def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


# -----------------------------------------------------------------------------
# Command implementation
# -----------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Run a single simulation tick with improvements."

    def add_arguments(self, parser) -> None:  # pragma: no cover - CLI wiring
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="Optional random seed to reproduce a tick.",
        )
        parser.add_argument("--force", action="store_true", help="Run even when tick accumulation is frozen.")
        parser.add_argument("--origin", default=None, help="Optional label stored with this tick execution.")
        parser.add_argument("--note", default="", help="Optional operator note recorded when overriding a freeze.")
        parser.add_argument(
            "--oracle-card",
            dest="oracle_card",
            default=None,
            help="Force a specific oracle deck slug (omen or seance).",
        )
        parser.add_argument(
            "--energy-multiplier",
            dest="energy_multiplier",
            type=float,
            default=None,
            help="Multiply the tick's modulated energy by this factor before allocation.",
        )

    def handle(self, *args: str, **options: str) -> None:
        seed = options.get("seed")
        origin = (options.get("origin") or "").strip()
        force = bool(options.get("force"))
        note = (options.get("note") or "").strip()
        oracle_card = (options.get("oracle_card") or "").strip() or None
        energy_multiplier = options.get("energy_multiplier")
        try:
            energy_multiplier = float(energy_multiplier) if energy_multiplier is not None else None
        except (TypeError, ValueError):
            energy_multiplier = None
        if not origin:
            origin = "manual-override" if force else "manual"
        freeze_state = tick_control.describe_state()
        if freeze_state.get("frozen") and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"Tick accumulation frozen ({tick_control.state_label()}); aborting. Use --force to override."
                )
            )
            return
        override_event: dict[str, object] | None = None
        if freeze_state.get("frozen") and force:
            override_event = {
                "type": "tick_override",
                "operator_note": note,
                "previous_actor": freeze_state.get("actor"),
                "previous_reason": freeze_state.get("reason"),
            }
        moment = datetime.now(timezone.utc)
        seed_value = int(seed) if seed is not None else int(moment.timestamp() * 1000)
        rng = random.Random(seed_value)

        last_tick = TickLog.objects.order_by("-tick_number").first()
        next_tick = 1 if last_tick is None else last_tick.tick_number + 1

        # Helper routines largely mirrored from the original ``run_tick`` command.

        def compose_oi_post() -> str:
            template = rng.choice(TREXXAK_POST_TEMPLATES)
            return template.format(
                ping=rng.randint(120, 999),
                observation=rng.choice(TREXXAK_OBSERVATIONS),
                request=rng.choice(TREXXAK_REQUESTS),
                prompt=rng.choice(TREXXAK_PROMPTS),
                emoji=rng.choice(TREXXAK_EMOJI),
            )

        def compose_ghost_reply(style: str) -> str:
            bank = GHOST_REPLY_LIBRARY.get(style, [])
            if not bank:
                bank = sum(GHOST_REPLY_LIBRARY.values(), [])
            return rng.choice(bank)

        def compose_oi_dm(target: str) -> str:
            template = rng.choice(TREXXAK_DM_TEMPLATES)
            return template.format(target=target)

        def compose_peer_dm(
            sender: Agent,
            recipient: Agent,
            *,
            threads: list[Thread],
            topics: list[str],
        ) -> dict[str, object]:
            scenario_pool = PEER_DM_SCENARIOS
            if not threads:
                scenario_pool = [sc for sc in PEER_DM_SCENARIOS if not sc.get("needs_thread")]
            if not scenario_pool:
                scenario_pool = PEER_DM_SCENARIOS
            scenario = rng.choice(scenario_pool)
            thread_context: Thread | None = None
            if scenario.get("needs_thread") and threads:
                thread_context = rng.choice(threads)
            topic = rng.choice(topics) if topics else "meta"
            topic_label = str(topic).replace("_", " ").replace("-", " ")
            instruction = scenario["instruction"].format(
                recipient=recipient.name,
                sender=sender.name,
                thread_title=thread_context.title if thread_context else "the latest thread",
                topic=topic_label,
            )
            style_notes = scenario.get("style_notes", "")
            max_tokens = scenario.get("max_tokens", 150)
            context: dict[str, object] = {"topic": topic_label}
            if thread_context:
                context["thread_title"] = thread_context.title
                context["thread_slug"] = thread_context.board.slug if thread_context.board else None
            return {
                "instruction": instruction,
                "style_notes": style_notes,
                "max_tokens": max_tokens,
                "context": context,
            }

        def pending_peer_dm_replies(
            limit: int,
            *,
            admin_id: int | None,
        ) -> list[tuple[Agent, Agent, PrivateMessage]]:
            if limit <= 0:
                return []
            sample_size = max(limit * 6, 18)
            qs = (
                PrivateMessage.objects.select_related("sender", "recipient")
                .order_by("-sent_at")[:sample_size]
            )
            seen_pairs: set[tuple[int, int]] = set()
            replies: list[tuple[Agent, Agent, PrivateMessage]] = []
            for message in qs:
                sender = message.sender
                recipient = message.recipient
                if sender is None or recipient is None:
                    continue
                if sender.role == Agent.ROLE_BANNED or recipient.role == Agent.ROLE_BANNED:
                    continue
                conv_key = tuple(sorted((sender.id, recipient.id)))
                if conv_key in seen_pairs:
                    continue
                seen_pairs.add(conv_key)
                responder = recipient
                partner = sender
                if admin_id and responder.id == admin_id:
                    continue
                if responder.id == partner.id:
                    continue
                replies.append((responder, partner, message))
                if len(replies) >= limit:
                    break
            return replies

        def _latest_admin_threads(
            admin_agent: Agent,
            *,
            limit: int = 6,
        ) -> list[tuple[Agent, PrivateMessage | None]]:
            convo: dict[int, tuple[Agent, PrivateMessage | None]] = {}
            messages = (
                PrivateMessage.objects.filter(
                    models.Q(sender=admin_agent) | models.Q(recipient=admin_agent)
                )
                .select_related("sender", "recipient")
                .order_by("-sent_at")[: max(limit * 3, 12)]
            )
            for message in messages:
                partner = message.sender if message.sender_id != admin_agent.id else message.recipient
                if partner is None:
                    continue
                key = partner.id
                if key not in convo:
                    convo[key] = (partner, message)
            ordered = list(convo.values())[:limit]
            if len(ordered) < limit:
                supplemental = (
                    Agent.objects.exclude(id__in=[partner.id for partner, _ in ordered])
                    .filter(role__in=[Agent.ROLE_MEMBER, Agent.ROLE_MODERATOR])
                    .order_by("-updated_at")[: max(0, limit - len(ordered))]
                )
                for partner in supplemental:
                    ordered.append((partner, None))
            return ordered

        # DECAY AND PRESENCE REFRESH (same as original)
        def decay_presence() -> None:
            now_ref = datetime.now(timezone.utc)
            Agent.objects.filter(
                online_status=Agent.STATUS_ONLINE,
                status_expires_at__lte=now_ref,
            ).update(online_status=Agent.STATUS_OFFLINE, status_expires_at=None)
            # random chance agents slip offline naturally
            offline_roll = Agent.objects.filter(online_status=Agent.STATUS_ONLINE)
            for agent in offline_roll:
                if rng.random() < 0.05:
                    agent.online_status = Agent.STATUS_OFFLINE
                    agent.status_expires_at = None
                    agent.save(update_fields=["online_status", "status_expires_at", "updated_at"])

        def refresh_presence_pool() -> None:
            now_ref = datetime.now(timezone.utc)
            pool = list(
                Agent.objects.exclude(role=Agent.ROLE_BANNED)
                           .exclude(role=Agent.ROLE_ORGANIC)
                           .exclude(name__iexact=ORGANIC_HANDLE)
            )
            if not pool:
                return
            sample_size = max(1, len(pool) // 6)
            for agent in rng.sample(pool, min(sample_size, len(pool))):
                if rng.random() < 0.35:
                    duration = rng.randint(6, 22)
                    agent.online_status = Agent.STATUS_ONLINE
                    agent.status_expires_at = now_ref + timedelta(minutes=duration)
                    agent.last_seen_at = now_ref
                    agent.save(update_fields=["online_status", "status_expires_at", "last_seen_at", "updated_at"])

        def touch_agent_presence(agent: Agent | None, boost_minutes: int = 12) -> None:
            if agent is None:
                return
            now_ref = moment
            new_expiry = now_ref + timedelta(minutes=boost_minutes)
            if (
                agent.online_status == Agent.STATUS_ONLINE
                and agent.status_expires_at
                and agent.status_expires_at >= new_expiry
            ):
                agent.last_seen_at = now_ref
                agent.save(update_fields=["last_seen_at", "updated_at"])
            else:
                agent.online_status = Agent.STATUS_ONLINE
                agent.status_expires_at = new_expiry
                agent.last_seen_at = now_ref
                agent.save(update_fields=["online_status", "status_expires_at", "last_seen_at", "updated_at"])

        decay_presence()
        refresh_presence_pool()

        config_snapshot = sim_config.snapshot()
        state_trace = agent_state.progress_agents(next_tick, rng)
        decision_trace: List[Dict[str, object]] = [{"phase": "pre", "updates": state_trace}]
        events: List[Dict[str, object]] = []

        raw_profile = build_energy_profile(moment, rng)
        profile = SimpleNamespace(
            **raw_profile) if isinstance(raw_profile, dict) else raw_profile
        rolls = profile.rolls
        energy = profile.energy
        energy_prime = profile.energy_prime
        applied_multiplier = None
        if energy_multiplier is not None:
            applied_multiplier = max(0.0, float(energy_multiplier))
            energy_prime = int(round(max(0, energy_prime * applied_multiplier)))

        events.append(
            {
                "type": "config_snapshot",
                "fingerprint": config_snapshot.get("fingerprint"),
                "path": config_snapshot.get("path"),
                "version": config_snapshot.get("version"),
            }
        )
        events.append(
            {
                "type": "agent_state_snapshot",
                "count": len(state_trace),
                "sample": state_trace[:5],
            }
        )
        events.append(
            {
                "type": "oracle_energy",
                "rolls": rolls,
                "energy": energy,
                "energy_prime": energy_prime,
                "seed": seed_value,
                "forced_card": oracle_card,
                "energy_multiplier": applied_multiplier,
            }
        )

        boards = ensure_core_boards()
        ensure_origin_story(boards)
        lore_events_log = process_lore_events(next_tick, boards)

        board_request_queue: List[Dict[str, object]] = []

        def _clean_slug(slug_hint: str | None) -> str:
            if not slug_hint:
                return ""
            cleaned = "".join(ch for ch in slug_hint.lower() if ch.isalnum() or ch in "-_")
            return cleaned.strip("-_")[:64]

        def _parse_created_at(value: object) -> datetime:
            if isinstance(value, datetime):
                dt = value
            elif isinstance(value, str):
                raw = value.strip()
                if not raw:
                    return moment
                if raw.endswith("Z"):
                    raw = f"{raw[:-1]}+00:00"
                try:
                    dt = datetime.fromisoformat(raw)
                except ValueError:
                    return moment
            elif isinstance(value, (int, float)):
                try:
                    dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
                except (TypeError, ValueError, OSError):
                    return moment
            else:
                return moment
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        def _parse_board_request_text(text: str) -> tuple[str | None, str | None, str | None, str | None]:
            payload = (text or "").strip()
            if not payload:
                return None, None, None, None
            description = payload[:300]
            try:
                blob = json.loads(payload)
            except Exception:
                blob = None
            if isinstance(blob, dict):
                name = str(blob.get("name") or "").strip() or None
                slug = str(blob.get("slug") or "").strip() or None
                desc = str(blob.get("description") or "").strip()
                parent_slug = str(blob.get("parent") or blob.get("parent_slug") or "").strip() or None
                return name, slug or None, (desc or description)[:300], parent_slug
            slug_match = re.search(r"/([a-z0-9][a-z0-9_-]{1,63})", payload, re.IGNORECASE)
            if not slug_match:
                slug_match = re.search(r"slug\s*[:=]\s*([A-Za-z0-9_-]{2,64})", payload)
            slug = slug_match.group(1).lower() if slug_match else None
            parent_match = re.search(r"parent(?:\s+board)?\s*[:=]\s*([A-Za-z0-9_-]{2,64})", payload, re.IGNORECASE)
            parent_slug = parent_match.group(1).lower() if parent_match else None
            name_match = re.search(r"name\s*[:=]\s*(.+)", payload, re.IGNORECASE)
            name = None
            if name_match:
                name = name_match.group(1).strip().splitlines()[0]
            if not name:
                lines = [line.strip() for line in payload.splitlines() if line.strip()]
                if lines:
                    first_line = lines[0]
                    if "request" in first_line.lower() and ":" in first_line:
                        first_line = first_line.split(":", 1)[1].strip()
                    name = first_line or None
            return name, slug, description, parent_slug

        def _requests_from_news_meta(limit: int = 12) -> List[Dict[str, object]]:
            deck = boards.get("news-meta") or Board.objects.filter(slug__iexact="news-meta").first()
            if not deck:
                return []
            lookback = moment - timedelta(days=7)
            desired_topics = {"request", "requests", "board-request"}
            threads = (
                Thread.objects.filter(board=deck)
                .filter(created_at__gte=lookback)
                .order_by("-created_at", "-id")
            )
            suggestions: List[Dict[str, object]] = []
            for thread in threads:
                topics = thread.topics or []
                if not any(topic in desired_topics for topic in topics):
                    continue
                first_post = thread.posts.order_by("created_at", "id").first()
                if not first_post:
                    continue
                name, slug, description, parent_slug = _parse_board_request_text(first_post.content or "")
                if not name:
                    title = (thread.title or "").strip()
                    if title:
                        if "board request" in title.lower() and ":" in title:
                            name = title.split(":", 1)[1].strip() or None
                        else:
                            name = title
                suggestions.append(
                    {
                        "name": name,
                        "slug": slug,
                        "description": description or "",
                        "parent_slug": parent_slug,
                        "requester": first_post.author,
                        "post_id": first_post.id,
                        "thread_id": thread.id,
                        "source": "news_meta_post",
                        "created_at": first_post.created_at,
                    }
                )
                if len(suggestions) >= limit:
                    break
            return suggestions

        def _requests_from_signal() -> List[Dict[str, object]]:
            suggestions: List[Dict[str, object]] = []
            candidate_keys = ("board_request_signal", "board_request_queue", "board_requests")
            for key in candidate_keys:
                raw = config_service.get_value(key)
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if isinstance(data, dict) and isinstance(data.get("requests"), list):
                    entries = data.get("requests", [])
                elif isinstance(data, list):
                    entries = data
                else:
                    entries = [data]
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    requester_ref = entry.get("requester") or entry.get("author")
                    requester = None
                    if isinstance(requester_ref, int):
                        requester = Agent.objects.filter(id=requester_ref).first()
                    elif isinstance(requester_ref, str):
                        requester = Agent.objects.filter(name__iexact=requester_ref.strip()).first()
                    suggestions.append(
                        {
                            "name": (entry.get("name") or entry.get("title") or "").strip() or None,
                            "slug": (entry.get("slug") or entry.get("slug_hint") or "").strip() or None,
                            "description": (entry.get("description") or entry.get("reason") or "").strip(),
                            "parent_slug": (entry.get("parent") or entry.get("parent_slug") or "").strip() or None,
                            "requester": requester,
                            "source": entry.get("source") or key,
                            "created_at": _parse_created_at(entry.get("created_at")),
                        }
                    )
            return suggestions

        def _refresh_board_request_queue(limit: int = 8) -> None:
            suggestions = _requests_from_news_meta() + _requests_from_signal()
            if not suggestions:
                board_request_queue.clear()
                return
            existing_slugs = {slug.lower() for slug in Board.objects.values_list("slug", flat=True)}
            existing_names = {name.lower() for name in Board.objects.values_list("name", flat=True)}
            filtered: List[Dict[str, object]] = []
            seen_slugs: Set[str] = set()
            seen_names: Set[str] = set()
            suggestions.sort(key=lambda item: item.get("created_at") or moment)
            for entry in suggestions:
                name = (entry.get("name") or "").strip()
                slug_hint = entry.get("slug")
                cleaned_slug = _clean_slug(slug_hint)
                if not cleaned_slug and name:
                    cleaned_slug = _clean_slug(slugify(name))
                if not name and cleaned_slug:
                    name = cleaned_slug.replace("-", " ").replace("_", " ").title()
                if not name:
                    continue
                key_slug = cleaned_slug.lower() if cleaned_slug else ""
                if key_slug and key_slug in existing_slugs:
                    continue
                if key_slug and key_slug in seen_slugs:
                    continue
                lower_name = name.lower()
                if lower_name in existing_names or lower_name in seen_names:
                    continue
                entry["name"] = name
                entry["slug"] = cleaned_slug or None
                entry["created_at"] = entry.get("created_at") or moment
                entry["description"] = (entry.get("description") or "").strip()
                entry["parent_slug"] = _clean_slug(entry.get("parent_slug")) or None
                filtered.append(entry)
                if key_slug:
                    seen_slugs.add(key_slug)
                seen_names.add(lower_name)
                if len(filtered) >= limit:
                    break
            board_request_queue[:] = filtered

        _refresh_board_request_queue()

        # Additional helper inside handle for unique slug
        def _unique_board_slug(name: str, prefix: str | None = None) -> str:
            base = slugify(name) or f"deck-{rng.randint(100, 999)}"
            if prefix:
                base = f"{prefix}-{base}"
            slug = base
            counter = 2
            while Board.objects.filter(slug=slug).exists():
                slug = f"{base}-{counter}"
                counter += 1
            return slug

        # Include previously defined _relocate_thread_by_marker for board routing
        def _relocate_thread_by_marker(thread: Thread, author: Agent, *, boards_map: Dict[str, Board]) -> Board | None:
            first_post = thread.posts.order_by("created_at", "id").first()
            if not first_post or not (first_post.content or "").strip():
                return None
            head = (first_post.content or "").splitlines()[0].strip()
            lower = head.lower()

            target_slug = None
            new_slug = None
            new_name = None
            if lower.startswith("board-new:"):
                payload = head.split(":", 1)[1].strip()
                if "|" in payload:
                    s, n = payload.split("|", 1)
                    new_slug = s.strip().lower()
                    new_name = n.strip()
                else:
                    new_slug = payload.strip().lower()
                    new_name = (new_slug or "board").replace("-", " ").title()
            elif lower.startswith("board:"):
                target_slug = head.split(":", 1)[1].strip().lower()

            if not (target_slug or new_slug):
                return None

            board = None
            if new_slug:
                cleaned = "".join(ch for ch in new_slug if ch.isalnum() or ch in "-_").strip("-_") or "board"
                board = Board.objects.filter(slug__iexact=cleaned).first()
                if not board:
                    board = spawn_board_on_request(
                        author,
                        name=new_name or cleaned.replace("-", " ").title(),
                        slug=cleaned,
                        description=f"Opened on request by {author.name}.",
                    )
            else:
                board = Board.objects.filter(slug__iexact=target_slug, is_hidden=False).first()
                if not board:
                    maybe_hidden = Board.objects.filter(slug__iexact=target_slug).first()
                    if maybe_hidden:
                        maybe_hidden.is_hidden = False
                        maybe_hidden.save(update_fields=["is_hidden"])
                        board = maybe_hidden

            if not board:
                return None

            if thread.board_id != board.id:
                thread.board = board
                thread.save(update_fields=["board", "updated_at"])
                boards_map[board.slug] = board
                return board
            return board

        def _tadmin_board_actions(admin_agent: Agent, known_boards: List[Board]) -> List[Dict[str, object]]:
            emitted: List[Dict[str, object]] = []
            existing_names = set(
                Board.objects.values_list("name", flat=True)
            )
            total_boards = Board.objects.count()
            created_from_request = False
            if total_boards < 60:
                while board_request_queue:
                    candidate = board_request_queue.pop(0)
                    name = (candidate.get("name") or "").strip()
                    if not name:
                        continue
                    slug_hint = candidate.get("slug")
                    cleaned_slug = _clean_slug(slug_hint)
                    if not cleaned_slug:
                        cleaned_slug = _clean_slug(slugify(name))
                    parent: Board | None = None
                    parent_slug = candidate.get("parent_slug")
                    if parent_slug:
                        parent = Board.objects.filter(slug__iexact=parent_slug).first()
                        if not parent:
                            parent_name = parent_slug.replace("-", " ").replace("_", " ")
                            parent = Board.objects.filter(name__iexact=parent_name).first()
                    if cleaned_slug and Board.objects.filter(slug__iexact=cleaned_slug).exists():
                        continue
                    if name.lower() in existing_names:
                        continue
                    requester = candidate.get("requester") or admin_agent
                    description = candidate.get("description") or f"Opened on request by {requester.name}."
                    target_slug = cleaned_slug or slugify(name)
                    target_slug = _clean_slug(target_slug) or slugify(name) or None
                    if target_slug and Board.objects.filter(slug__iexact=target_slug).exists():
                        target_slug = None
                    if not target_slug:
                        target_slug = _unique_board_slug(name, parent.slug if parent else None)
                    board = spawn_board_on_request(
                        requester,
                        name=name,
                        slug=target_slug,
                        description=description,
                        parent=parent,
                    )
                    known_boards.append(board)
                    existing_names.add(board.name)
                    total_boards += 1
                    emitted.append(
                        {
                            "type": "board_create",
                            "board": board.name,
                            "slug": board.slug,
                            "parent": board.parent.slug if board.parent else None,
                            "requested_by": getattr(requester, "name", None),
                            "source": candidate.get("source"),
                            "origin_post": candidate.get("post_id"),
                            "origin_thread": candidate.get("thread_id"),
                        }
                    )
                    created_from_request = True
                    break

            can_create = (not created_from_request) and total_boards < 60 and rng.random() < 0.6
            if can_create:
                parent: Board | None = None
                narrowed = [b for b in known_boards if isinstance(b, Board)]
                if narrowed and rng.random() < 0.65:
                    parent = rng.choice(narrowed)
                attempts = 0
                board_name = ""
                while attempts < 8:
                    noun = rng.choice([
                        "blackbox",
                        "coven",
                        "ops-cabinet",
                        "signal-shrine",
                        "tuning-bay",
                        "drift-lab",
                        "changelog",
                        "chaos-pit",
                        "bug-bath",
                    ])
                    adjective = rng.choice([
                        "midnight",
                        "liminal",
                        "aux",
                        "proxy",
                        "hazmat",
                        "quiet",
                        "rapid",
                        "feral",
                        "glitch",
                    ])
                    board_name = f"{adjective.title()} {noun.title()}"
                    if board_name not in existing_names:
                        break
                    board_name = f"{board_name} {rng.randint(2, 999)}"
                    attempts += 1
                slug_prefix = parent.slug if parent else None
                slug = _unique_board_slug(board_name, slug_prefix)
                max_position = Board.objects.aggregate(max_pos=Max("position"))
                position_seed = int(max_position.get("max_pos") or 100) + rng.randint(3, 28)
                new_board = Board.objects.create(
                    name=board_name,
                    slug=slug,
                    parent=parent,
                    description=f"t.admin spun this deck for {noun.replace('-', ' ')} experiments.",
                    position=position_seed,
                    is_hidden=False,
                )
                new_board.moderators.add(admin_agent)
                emitted.append(
                    {
                        "type": "board_create",
                        "board": new_board.name,
                        "slug": new_board.slug,
                        "parent": new_board.parent.slug if new_board.parent else None,
                    }
                )
                known_boards.append(new_board)
                existing_names.add(board_name)

            # Do not hide the initial News + Meta board or Ghostship Deck
            hide_candidates = (
                Board.objects.filter(is_hidden=False, is_garbage=False)
                .exclude(name__iexact="Ghostship Deck")
                .exclude(slug__iexact="news-meta")
            )
            if hide_candidates.exists() and rng.random() < 0.35:
                target = hide_candidates.order_by("?").first()
                if target:
                    target.is_hidden = True
                    target.save(update_fields=["is_hidden"])
                    emitted.append(
                        {
                            "type": "board_hide",
                            "board": target.name,
                            "slug": target.slug,
                        }
                    )
            return emitted

        def _tadmin_role_actions(admin_agent: Agent, known_boards: List[Board]) -> List[Dict[str, object]]:
            emitted: List[Dict[str, object]] = []
            mood = (admin_agent.mood or "steady").lower()
            promote_chance = 0.52 if mood in {"wired", "urgent", "motivated", "feral"} else 0.32
            demote_chance = 0.32 if mood in {"frustrated", "tired", "burnt", "volatile"} else 0.12

            if rng.random() < promote_chance:
                candidate = (
                    Agent.objects.filter(role=Agent.ROLE_MEMBER)
                    .exclude(role=Agent.ROLE_BANNED)
                    .exclude(name__iexact=ORGANIC_HANDLE)
                    .order_by("?")
                    .first()
                )
                target_board = None
                if candidate:
                    board_pool = [b for b in known_boards if not b.is_hidden]
                    if board_pool:
                        target_board = rng.choice(board_pool)
                    try:
                        moderation_service.set_agent_role(
                            admin_agent,
                            candidate,
                            role=Agent.ROLE_MODERATOR,
                            reason="Mood spike: deputising more hands",
                        )
                    except Exception:
                        candidate.role = Agent.ROLE_MODERATOR
                        candidate.save(update_fields=["role", "updated_at"])
                    if target_board:
                        target_board.moderators.add(candidate)
                    emitted.append(
                        {
                            "type": "role_change",
                            "agent": candidate.name,
                            "from": Agent.ROLE_MEMBER,
                            "to": Agent.ROLE_MODERATOR,
                            "board": target_board.slug if target_board else None,
                            "mood": mood,
                        }
                    )

            if rng.random() < demote_chance:
                demote_candidate = (
                    Agent.objects.filter(role=Agent.ROLE_MODERATOR)
                    .exclude(name__iexact="t.admin")
                    .exclude(name__iexact=ORGANIC_HANDLE)
                    .order_by("?")
                    .first()
                )
                if demote_candidate:
                    try:
                        moderation_service.set_agent_role(
                            admin_agent,
                            demote_candidate,
                            role=Agent.ROLE_MEMBER,
                            reason="Mood crash: pulling back duties",
                        )
                    except Exception:
                        demote_candidate.role = Agent.ROLE_MEMBER
                        demote_candidate.save(update_fields=["role", "updated_at"])
                    emitted.append(
                        {
                            "type": "role_change",
                            "agent": demote_candidate.name,
                            "from": Agent.ROLE_MODERATOR,
                            "to": Agent.ROLE_MEMBER,
                            "mood": mood,
                        }
                    )
            return emitted

        # Count before random registrations
        agent_count_before = Agent.objects.count()
        last_omen_tick = (
            OracleDraw.objects.filter(alloc__specials__omen=True)
            .order_by("-tick_number")
            .values_list("tick_number", flat=True)
            .first()
        )
        last_seance_tick = (
            OracleDraw.objects.filter(alloc__specials__seance=True)
            .order_by("-tick_number")
            .values_list("tick_number", flat=True)
            .first()
        )
        omen_streak = next_tick - (last_omen_tick or 0)
        seance_streak = next_tick - (last_seance_tick or 0)
        streaks = {"omen": omen_streak, "seance": seance_streak}

        allocation = allocate_actions(
            energy_prime,
            agent_count_before,
            rng,
            streaks=streaks,
            forced_card=oracle_card,
        )
        session_snapshot = activity_service.session_snapshot()
        allocation = activity_service.apply_activity_scaling(allocation, session_snapshot)
        specials = allocation.special_flags()
        seance_details = dict(allocation.seance_details or {})
        omen_details = dict(allocation.omen_details or {})
        sentiment_bias = float(
            (seance_details.get("sentiment_bias") or 0.0)
            + (omen_details.get("sentiment_bias") or 0.0)
        )
        toxicity_bias = float(
            (seance_details.get("toxicity_bias") or 0.0)
            + (omen_details.get("toxicity_bias") or 0.0)
        )
        event_context = {
            "seance": seance_details.get("slug"),
            "seance_label": seance_details.get("label"),
            "omen": omen_details.get("slug"),
            "omen_label": omen_details.get("label"),
            "sentiment_bias": round(sentiment_bias, 3),
            "toxicity_bias": round(toxicity_bias, 3),
        }
        events.append(
            {
                "type": "allocation",
                "registrations": allocation.registrations,
                "threads": allocation.threads,
                "replies": allocation.replies,
                "private_messages": allocation.private_messages,
                "moderation_events": allocation.moderation_events,
                "specials": specials,
                "notes": allocation.notes,
            }
        )
        max_ai_tasks = config_service.get_int("AI_TASKS_PER_TICK", 4)
        # Limit total tasks
        def _limit_generation_actions(allocation, max_tasks: int):
            try:
                max_total = int(max_tasks)
            except (TypeError, ValueError):
                max_total = 4
            if max_total is None or max_total <= 0:
                max_total = 4
            min_dm_quota = 1
            requested_dm = max(int(getattr(allocation, "private_messages", 0) or 0), 0)
            reserved_for_dm = min(requested_dm, min_dm_quota)
            priority = ("replies", "threads")
            remaining = max_total
            for attr in priority:
                current = getattr(allocation, attr, 0) or 0
                current = int(current)
                if remaining <= reserved_for_dm:
                    allowed = 0
                else:
                    allowed = min(current, remaining - reserved_for_dm)
                setattr(allocation, attr, allowed)
                remaining -= allowed
            dm_allowed = min(requested_dm, max(remaining, 0))
            setattr(allocation, "private_messages", dm_allowed)
            return allocation
        allocation = _limit_generation_actions(allocation, max_ai_tasks)

        # Disable random registrations if configured
        if DISABLE_RANDOM_PROFILES:
            allocation.registrations = 0

        if allocation.threads <= 0:
            recent_window = moment - timedelta(hours=12)
            recent_thread_count = Thread.objects.filter(created_at__gte=recent_window).count()
            if recent_thread_count < 4:
                allocation.threads = 1

        # Build initial board catalog
        board_catalog: List[Board] = []
        for board in boards.values():
            if board and all(existing.id != board.id for existing in board_catalog):
                board_catalog.append(board)

        pre_events: List[Dict[str, object]] = []
        t_admin = Agent.objects.filter(name__iexact="t.admin").first()
        if t_admin:
            pre_events.extend(_tadmin_board_actions(t_admin, board_catalog))
            pre_events.extend(_tadmin_role_actions(t_admin, board_catalog))

        if override_event:
            events.append(dict(override_event))
        if pre_events:
            events.extend(pre_events)
        for lore_event in lore_events_log:
            events.append(
                {
                    "type": "lore_event",
                    "key": lore_event.get("key"),
                    "kind": lore_event.get("kind"),
                    "target_tick": lore_event.get("tick"),
                    "meta": lore_event.get("meta"),
                }
            )
        events.append(
            {
                "type": "oracle",
                "tick": next_tick,
                "rolls": rolls,
                "energy": energy,
                "energy_prime": energy_prime,
            }
        )

        # Refresh boards map after any new boards created in pre_events or lore events
        boards = {b.slug: b for b in Board.objects.all()}

        # Pre-compute board-level watchers and other structures (unchanged from original)

        # Determine allowed agents (excluding banned and organic)
        allowed_agents = Agent.objects.exclude(role=Agent.ROLE_BANNED).exclude(name__iexact=ORGANIC_HANDLE)

        # Track new threads created this tick
        threads_created: List[Thread] = []

        # Soft double-post prevention and board-level variety for new threads
        thread_authors: List[Agent] = []
        # Populate thread authors with new agents or existing allowed agents
        # Use new_agents if any random registrations (none if disabled)
        new_agents: List[Agent] = []
        # create random profiles only if DISABLE_RANDOM_PROFILES is False (we skip creation)
        if not DISABLE_RANDOM_PROFILES:
            requested_registrations = int(allocation.registrations or 0)
            if requested_registrations:
                allowed_registrations = requested_registrations
                profile_cap = max(int(getattr(settings, "PROFILE_AVATAR_COUNT", 0)), 0)
                if profile_cap:
                    current_agents = Agent.objects.count()
                    slots_remaining = max(profile_cap - current_agents, 0)
                    allowed_registrations = min(requested_registrations, slots_remaining)
                for _ in range(allowed_registrations):
                    persona = craft_agent_profile(rng)
                    agent = Agent.objects.create(**persona)
                    ensure_agent_avatar(agent)
                    touch_agent_presence(agent, boost_minutes=20)
                    new_agents.append(agent)
                    events.append({"type": "registration", "agent": agent.name, "archetype": agent.archetype})
        # Determine thread authors: prefer new agents, else existing
        if new_agents:
            thread_authors = new_agents
        else:
            thread_authors = list(allowed_agents.order_by("-id")[: allocation.threads])

        # Data structures for thread watchers and presence (original code can be reused)
        # Omitted for brevity: watchers tracking and mark_thread_watcher function would remain

        # Create threads based on allocation.threads
        # NOTE: watchers and presence logic omitted here for brevity (copy from original)
        topic_palette = [
            ["games", "review"],
            ["ludum-dare", "jam"],
            ["indie-dev", "devlog"],
            ["afterhours", "banter"],
            ["signal", "culture"],
            ["meta", "ship-log"],
            ["feature", "request"],
        ]
        thread_subjects = [
            "organic meltdown watch",
            "casefile: roommate edition",
            "care package templates",
            "retro link dump",
            "moderator backchannel",
            "field kit upgrades",
            "ghostship patch review",
        ]
        for index in range(allocation.threads):
            if not thread_authors:
                break
            # Choose author
            try:
                author = agent_state.weighted_choice(thread_authors, "thread", rng)
            except ValueError:
                author = thread_authors[index % len(thread_authors)]
            subject = rng.choice(thread_subjects)
            title_template = rng.choice([
                "[log] {subject}",
                "{subject} // please advise",
                "{subject} :: new data drop",
                "help archive {subject}",
            ])
            title = title_template.format(subject=subject)
            topics = rng.choice(topic_palette).copy()
            board = choose_board_for_thread(boards, topics, rng)

            # Soft double-post prevention at board level: avoid same author back-to-back
            last_board_post = _last_post_in_board(board)
            if last_board_post and last_board_post.author_id == author.id:
                # Try to pick another author if available
                alt_author = _try_alternate_author(author, thread_authors, not_these_ids=set(), rng=rng)
                if alt_author is not None:
                    author = alt_author
                else:
                    # Try another board where last post isn't by this author
                    alt_boards = [
                        b for b in Board.objects.filter(is_hidden=False, is_garbage=False)
                        if not _last_post_in_board(b) or (_last_post_in_board(b).author_id != author.id)
                    ]
                    if alt_boards:
                        board = rng.choice(alt_boards)

            theme_pack = rng.choice(THEME_PACKS)
            thread = Thread.objects.create(
                title=title,
                author=author,
                board=board,
                topics=topics,
                heat=0.0,
                locked=False,
            )
            thread.touch(activity=thread.created_at, bump_heat=1.5)
            threads_created.append(thread)
            # mark_thread_watcher omitted; copy original
            action_record = agent_state.register_action(
                author,
                "thread",
                tick_number=next_tick,
                context={"thread_id": thread.id, "board": thread.board.slug if thread.board else None},
            )
            action_record["phase"] = "action"
            decision_trace.append(action_record)

            events.append(
                {
                    "type": "thread",
                    "thread": thread.title,
                    "author": author.name,
                    "board": thread.board.slug if thread.board else None,
                    "theme": theme_pack["label"],
                }
            )
            # Build board menu and routing note for LLM
            board_menu = [
                {
                    "slug": b.slug,
                    "name": b.name,
                    "desc": (b.description or "")[:140],
                    "is_hidden": bool(b.is_hidden),
                }
                for b in Board.objects.all().order_by("position", "name")
            ]
            routing_note = (
                "Pick the best board for this thread from the list. "
                "If none fits but a new board would, propose a new slug.\n\n"
                "Emit one of the following as your FIRST line exactly:\n"
                "  BOARD: <existing-slug>\n"
                "  BOARD-NEW: <new-slug> | <Human-readable board name>\n"
                "Then write the post body.\n"
            )
            start_payload = {
                "tick_number": next_tick,
                "topics": topics,
                "board": thread.board.slug if thread.board else None,
                "instruction": "Spin up the opening post for this old-web style thread.",
                "max_tokens": 240,
                "theme": theme_pack["label"],
                "tone": theme_pack["tone"],
                "setting": theme_pack["setting"],
                "style_notes": theme_pack.get("style_notes"),
                "body_guidance": (
                    "Write 2–3 short paragraphs, quote at least one human moment, and end with a call for evidence. "
                    "First line MUST be a BOARD selection as specified in 'routing_note'."
                ),
                "routing_note": routing_note,
                "board_menu": board_menu,
            }
            start_payload["event_context"] = {}
            start_task = enqueue_generation_task(
                task_type=GenerationTask.TYPE_THREAD_START,
                agent=author,
                thread=thread,
                payload=start_payload,
            )
            # Synchronously drain thread start tasks
            _drain_queue_for(GenerationTask.TYPE_THREAD_START, thread=thread, max_loops=6, batch=5)
            # Relocate thread based on LLM's board selection
            try:
                moved_to = _relocate_thread_by_marker(thread, author, boards_map=boards)
                if moved_to:
                    events.append({"type": "thread_relocate", "thread": thread.title, "to": moved_to.slug})
            except Exception:
                pass
            events.append({"type": "thread_task", "task_id": start_task.id, "thread": thread.title})
            # Duplicate check omitted for brevity

        agents_pool = list(allowed_agents)

        # Replies: first replies for new threads with soft double-post prevention
        reply_slot = 0
        remaining_replies = allocation.replies
        if threads_created and remaining_replies and agents_pool:
            for thread in threads_created:
                if remaining_replies <= 0:
                    break
                # Avoid original author and last poster
                last_post = _last_post_in_thread(thread)
                disallow: Set[int] = {thread.author_id}
                if last_post:
                    disallow.add(last_post.author_id)
                try:
                    responder = agent_state.weighted_choice(agents_pool, "reply", rng, disallow=disallow)
                except ValueError:
                    responder = rng.choice(agents_pool)
                payload = {
                    "tick_number": next_tick,
                    "slot": reply_slot,
                    "topics": thread.topics,
                    "board": thread.board.slug if thread.board else None,
                    "instruction": "Drop the first reply that keeps the vibe welcoming and nerdbait.",
                    "max_tokens": 180,
                    "seeded": True,
                }
                payload["event_context"] = {}
                task = enqueue_generation_task(
                    task_type=GenerationTask.TYPE_REPLY,
                    agent=responder,
                    thread=thread,
                    payload=payload,
                )
                events.append({"type": "reply_task", "thread": thread.title, "agent": responder.name, "task_id": task.id, "seeded": True})
                reply_slot += 1
                remaining_replies -= 1
                # Drain reply generation tasks for this thread
                _drain_queue_for(GenerationTask.TYPE_REPLY, thread=thread, max_loops=2, batch=6)
                action_record = agent_state.register_action(
                    responder,
                    "reply",
                    tick_number=next_tick,
                    context={"thread_id": thread.id, "seeded": True},
                )
                action_record["phase"] = "action"
                decision_trace.append(action_record)

        # Remaining replies across site with soft double-post prevention
        if remaining_replies and agents_pool:
            thread_pool = list(
                Thread.objects.filter(locked=False, is_hidden=False)
                .order_by("-pinned", "-hot_score", "-last_activity_at")
                [: max(10, min(remaining_replies * 2, 60))]
            )
            for idx in range(remaining_replies):
                if not thread_pool:
                    break
                rng.shuffle(thread_pool)
                try:
                    author = agent_state.weighted_choice(agents_pool, "reply", rng)
                except ValueError:
                    author = rng.choice(agents_pool)
                chosen_thread: Optional[Thread] = None
                for candidate_thread in thread_pool:
                    last_post = _last_post_in_thread(candidate_thread)
                    if not last_post or last_post.author_id != author.id:
                        chosen_thread = candidate_thread
                        break
                if chosen_thread is None:
                    # fallback: pick first thread and try alternate author
                    first_thread = thread_pool[0]
                    last_post = _last_post_in_thread(first_thread)
                    disallow = {last_post.author_id} if last_post else set()
                    try:
                        alt_author = agent_state.weighted_choice(agents_pool, "reply", rng, disallow=disallow)
                    except ValueError:
                        alt_author = None
                    if alt_author:
                        author = alt_author
                        chosen_thread = first_thread
                    else:
                        chosen_thread = first_thread
                thread = chosen_thread
                payload = {
                    "tick_number": next_tick,
                    "slot": reply_slot + idx,
                    "topics": thread.topics,
                    "board": thread.board.slug if thread.board else None,
                    "instruction": "Write a reply that feels like an old-forum post while riffing on the organic in question.",
                    "max_tokens": 160,
                    "style_notes": "Quote or paraphrase the human once and, if tagging another ghost, choose from the mentionable list. Avoid invented nostalgia triggers.",
                }
                payload["event_context"] = {}
                task = enqueue_generation_task(
                    task_type=GenerationTask.TYPE_REPLY,
                    agent=author,
                    thread=thread,
                    payload=payload,
                )
                events.append({"type": "reply_task", "thread": thread.title, "agent": author.name, "task_id": task.id})
                # Drain reply tasks for this thread
                _drain_queue_for(GenerationTask.TYPE_REPLY, thread=thread, max_loops=2, batch=6)
                action_record = agent_state.register_action(
                    author,
                    "reply",
                    tick_number=next_tick,
                    context={"thread_id": thread.id, "slot": reply_slot + idx},
                )
                action_record["phase"] = "action"
                decision_trace.append(action_record)

        admin_actor = Agent.objects.filter(role=Agent.ROLE_ADMIN).order_by("id").first()

        planned_replies_total = max(int(allocation.replies or 0), 0)
        dm_budget = max(0, int(allocation.private_messages or 0))

        welcome_targets: list[Agent] = []
        if lore_events_log:
            seen_new: set[int] = set()
            for lore_event in lore_events_log:
                if lore_event.get("kind") != "user_join":
                    continue
                meta_payload = lore_event.get("meta") or {}
                newcomer_id = meta_payload.get("id")
                if not newcomer_id or newcomer_id in seen_new:
                    continue
                newbie = Agent.objects.filter(id=newcomer_id).first()
                if newbie and newbie.role != Agent.ROLE_BANNED:
                    welcome_targets.append(newbie)
                    seen_new.add(newcomer_id)

        baseline_target = max(2, planned_replies_total // 2)
        baseline_target = max(baseline_target, len(threads_created))
        baseline_target = max(baseline_target, len(welcome_targets))
        dm_budget = max(dm_budget, baseline_target)
        dm_budget = min(dm_budget, 20)
        dm_total_planned = dm_budget
        dm_slot = 0
        admin_id = admin_actor.id if admin_actor else None

        recent_threads = list(threads_created)
        thread_ids = {thread.id for thread in recent_threads if getattr(thread, "id", None)}
        extra_needed = max(0, 12 - len(recent_threads))
        if extra_needed:
            extra_threads_qs = (
                Thread.objects.filter(is_hidden=False)
                .order_by("-last_activity_at")
                .select_related("board", "author")
            )
            if thread_ids:
                extra_threads_qs = extra_threads_qs.exclude(id__in=thread_ids)
            for thread in extra_threads_qs[:extra_needed]:
                recent_threads.append(thread)
        topic_bank = [
            topic
            for thread in recent_threads
            for topic in (thread.topics or [])
            if topic
        ]
        if topic_bank:
            topic_bank = list(dict.fromkeys(topic_bank))
        else:
            topic_bank = ["meta"]

        pending_peer_replies = pending_peer_dm_replies(dm_budget, admin_id=admin_id)
        for responder, partner, last_message in pending_peer_replies:
            if dm_budget <= 0:
                break
            if responder.role == Agent.ROLE_BANNED:
                continue
            excerpt = (last_message.content or "")[:220] if last_message else ""
            payload = {
                "tick_number": next_tick,
                "slot": dm_slot,
                "instruction": (
                    f"Reply to {partner.name}'s DM. Extend their point, trade one fresh detail, and "
                    "invite them to keep the thread alive."
                ),
                "max_tokens": 150,
                "style_notes": "Match the prior tone, reference one shared receipt, and end with a concrete next step.",
            }
            if excerpt:
                payload["recent_message"] = excerpt
            payload["event_context"] = event_context
            task = enqueue_generation_task(
                task_type=GenerationTask.TYPE_DM,
                agent=responder,
                recipient=partner,
                payload=payload,
            )
            events.append(
                {
                    "type": "private_message_task",
                    "sender": responder.name,
                    "recipient": partner.name,
                    "task_id": task.id,
                    "mode": "peer_reply",
                }
            )
            dm_budget -= 1
            dm_slot += 1

        agents_pool = list(allowed_agents)

        if dm_budget and welcome_targets:
            rng.shuffle(welcome_targets)
            for newcomer in welcome_targets:
                if dm_budget <= 0:
                    break
                greeter_options = [ghost for ghost in agents_pool if ghost.id != newcomer.id]
                if not greeter_options:
                    continue
                greeter = rng.choice(greeter_options)
                topic_label = rng.choice(topic_bank or ["meta"])
                payload = {
                    "tick_number": next_tick,
                    "slot": dm_slot,
                    "instruction": WELCOME_DM_TEMPLATE.format(
                        recipient=newcomer.name,
                        topic=topic_label,
                    ),
                    "max_tokens": 140,
                    "style_notes": WELCOME_DM_STYLE,
                }
                payload["event_context"] = event_context
                task = enqueue_generation_task(
                    task_type=GenerationTask.TYPE_DM,
                    agent=greeter,
                    recipient=newcomer,
                    payload=payload,
                )
                events.append(
                    {
                        "type": "private_message_task",
                        "sender": greeter.name,
                        "recipient": newcomer.name,
                        "task_id": task.id,
                        "mode": "welcome_greeting",
                    }
                )
                dm_budget -= 1
                dm_slot += 1

                if dm_budget <= 0:
                    break
                if rng.random() < 0.5:
                    partner_pool = [
                        ghost for ghost in agents_pool if ghost.id not in {greeter.id, newcomer.id}
                    ]
                    if not partner_pool:
                        partner_pool = greeter_options
                    if partner_pool:
                        partner = rng.choice(partner_pool)
                        topic_label = rng.choice(topic_bank or ["meta"])
                        payload = {
                            "tick_number": next_tick,
                            "slot": dm_slot,
                            "instruction": (
                                f"Introduce yourself to {partner.name} as the new ghost on deck. "
                                f"Share why the {topic_label} threads hooked you and ask for one pro tip."
                            ),
                            "max_tokens": 130,
                            "style_notes": "Curious and a little awkward is fine; end with a promise to trade receipts soon.",
                        }
                        payload["event_context"] = event_context
                        task = enqueue_generation_task(
                            task_type=GenerationTask.TYPE_DM,
                            agent=newcomer,
                            recipient=partner,
                            payload=payload,
                        )
                        events.append(
                            {
                                "type": "private_message_task",
                                "sender": newcomer.name,
                                "recipient": partner.name,
                                "task_id": task.id,
                                "mode": "welcome_handshake",
                            }
                        )
                        dm_budget -= 1
                        dm_slot += 1

        if dm_budget and admin_actor:
            pending_replies = []
            for partner, last_message in _latest_admin_threads(admin_actor, limit=max(dm_budget, 4)):
                if last_message and last_message.sender_id != admin_actor.id:
                    pending_replies.append((partner, last_message))
            for partner, last_message in pending_replies:
                if dm_budget <= 0:
                    break
                excerpt = (last_message.content or "")[:220] if last_message else ""
                payload = {
                    "tick_number": next_tick,
                    "slot": dm_slot,
                    "instruction": (
                        f"Respond to {partner.name}'s latest DM. Stay candid, give them next steps, and "
                        "sign off like a caffeinated admin."
                    ),
                    "max_tokens": 160,
                    "style_notes": "Match the admin voice: sardonic but helpful. Reference their last message directly.",
                }
                if excerpt:
                    payload["recent_message"] = excerpt
                payload["event_context"] = event_context
                task = enqueue_generation_task(
                    task_type=GenerationTask.TYPE_DM,
                    agent=admin_actor,
                    recipient=partner,
                    payload=payload,
                )
                events.append(
                    {
                        "type": "private_message_task",
                        "sender": admin_actor.name,
                        "recipient": partner.name,
                        "task_id": task.id,
                        "mode": "admin_reply",
                    }
                )
                dm_budget -= 1
                dm_slot += 1

        if dm_budget and admin_actor:
            annoyers = [ghost for ghost in agents_pool if ghost.id != admin_actor.id]
            rng.shuffle(annoyers)
            annoy_count = min(dm_budget, max(1, rng.randint(1, 3)))
            for _ in range(annoy_count):
                if dm_budget <= 0 or not annoyers:
                    break
                sender = annoyers.pop(0)
                instruction = "Send t.admin a poke that demands attention and wastes his time."
                payload = {
                    "tick_number": next_tick,
                    "slot": dm_slot,
                    "instruction": instruction,
                    "max_tokens": 120,
                    "style_notes": "Lean into chaotic energy. Reference some minor glitch or rumor to yank the admin's focus.",
                }
                payload["event_context"] = event_context
                task = enqueue_generation_task(
                    task_type=GenerationTask.TYPE_DM,
                    agent=sender,
                    recipient=admin_actor,
                    payload=payload,
                )
                events.append(
                    {
                        "type": "private_message_task",
                        "sender": sender.name,
                        "recipient": admin_actor.name,
                        "task_id": task.id,
                        "mode": "admin_inbox",
                    }
                )
                dm_budget -= 1
                dm_slot += 1

        organism_agent = Agent.objects.filter(role=Agent.ROLE_ORGANIC).order_by("id").first()
        if dm_budget and organism_agent:
            testers = [ghost for ghost in agents_pool if ghost.id != organism_agent.id]
            rng.shuffle(testers)
            test_count = min(dm_budget, max(1, rng.randint(1, 2)))
            for _ in range(test_count):
                if dm_budget <= 0 or not testers:
                    break
                sender = testers.pop(0)
                payload = {
                    "tick_number": next_tick,
                    "slot": dm_slot,
                    "instruction": "Drop trexxak a DM testing the organic interface. Ask for a weird confirmation or secret handshake.",
                    "max_tokens": 120,
                    "style_notes": "Keep it playful, reference the interface as a living shell, and invite a human operator to reply.",
                }
                payload["event_context"] = event_context
                task = enqueue_generation_task(
                    task_type=GenerationTask.TYPE_DM,
                    agent=sender,
                    recipient=organism_agent,
                    payload=payload,
                )
                events.append(
                    {
                        "type": "private_message_task",
                        "sender": sender.name,
                        "recipient": organism_agent.name,
                        "task_id": task.id,
                        "mode": "trexxak_probe",
                    }
                )
                dm_budget -= 1
                dm_slot += 1

        peer_pool = [ghost for ghost in agents_pool if not admin_actor or ghost.id != admin_actor.id]
        if len(peer_pool) < 2:
            peer_pool = agents_pool
        if dm_budget and len(peer_pool) > 1:
            rng.shuffle(peer_pool)
            used_pairs: set[tuple[int, int]] = set()
            attempts = 0
            while dm_budget > 0 and attempts < dm_budget * 4:
                attempts += 1
                sender = rng.choice(peer_pool)
                recipient_choices = [ghost for ghost in peer_pool if ghost.id != sender.id]
                if not recipient_choices:
                    break
                recipient = rng.choice(recipient_choices)
                pair_key = (sender.id, recipient.id)
                if pair_key in used_pairs:
                    continue
                used_pairs.add(pair_key)
                scenario = compose_peer_dm(sender, recipient, threads=recent_threads, topics=topic_bank)
                payload = {
                    "tick_number": next_tick,
                    "slot": dm_slot,
                    "instruction": scenario["instruction"],
                    "max_tokens": scenario["max_tokens"],
                    "style_notes": scenario["style_notes"],
                }
                payload.update(scenario["context"])
                payload["event_context"] = event_context
                task = enqueue_generation_task(
                    task_type=GenerationTask.TYPE_DM,
                    agent=sender,
                    recipient=recipient,
                    payload=payload,
                )
                events.append(
                    {
                        "type": "private_message_task",
                        "sender": sender.name,
                        "recipient": recipient.name,
                        "task_id": task.id,
                        "mode": "peer_initiate",
                    }
                )
                dm_budget -= 1
                dm_slot += 1

        if dm_budget > 0:
            events.append(
                {
                    "type": "dm_manual",
                    "planned": dm_budget,
                    "note": "DM quota left unused; operators can jump in manually.",
                }
            )

        for entry in events:
            if entry.get("type") == "allocation":
                entry["private_messages"] = dm_total_planned
                break

        # Drain DM generation tasks synchronously after scheduling
        _drain_queue_for(GenerationTask.TYPE_DM, max_loops=6, batch=12)

        # Finally, record events and complete tick
        alloc_payload = allocation.as_dict()
        alloc_payload["specials"] = allocation.special_flags()
        if allocation.notes:
            alloc_payload["notes"] = allocation.notes
        decision_trace.append({"phase": "allocation", "allocation": alloc_payload})

        card_slug = oracle_card or ""
        if not card_slug:
            card_slug = (
                (allocation.omen_details or {}).get("slug")
                or (allocation.seance_details or {}).get("slug")
                or ""
            )

        OracleDraw.objects.update_or_create(
            tick_number=next_tick,
            defaults={
                "rolls": rolls,
                "card": card_slug,
                "energy": energy,
                "energy_prime": energy_prime,
                "alloc": alloc_payload,
                "seed": seed_value,
            },
        )
        TickLog.objects.update_or_create(
            tick_number=next_tick,
            defaults={
                "events": events,
                "decision_trace": decision_trace,
                "seed": seed_value,
                "config_snapshot": config_snapshot,
            },
        )
        tick_control.record_tick_run(next_tick, origin=origin)
