"""Custom management command to run a single simulation tick."""
from __future__ import annotations

import random
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

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


DISABLE_RANDOM_PROFILES = getattr(settings, "SIM_DISABLE_RANDOM_PROFILES", True)


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


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _limit_generation_actions(allocation, max_tasks: int):
    """Clamp AI-authored content to a global per-tick ceiling."""
    try:
        max_total = int(max_tasks)
    except (TypeError, ValueError):
        max_total = 4
    if max_total is None or max_total <= 0:
        max_total = 4
    priority = ("replies", "threads", "private_messages")
    remaining = max_total
    for attr in priority:
        current = getattr(allocation, attr, 0) or 0
        current = int(current)
        if remaining <= 0:
            setattr(allocation, attr, 0)
            continue
        allowed = min(current, remaining)
        setattr(allocation, attr, allowed)
        remaining -= allowed
    return allocation


class Command(BaseCommand):
    help = "Run a single simulation tick, enqueueing content-generation tasks."

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

    def handle(self, *args: str, **options: str) -> None:
        seed = options.get("seed")
        origin = (options.get("origin") or "").strip()
        force = bool(options.get("force"))
        note = (options.get("note") or "").strip()
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
        rng = random.Random(seed if seed is not None else moment.timestamp())

        last_tick = TickLog.objects.order_by("-tick_number").first()
        next_tick = 1 if last_tick is None else last_tick.tick_number + 1

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

        def compose_peer_dm(sender: Agent, recipient: Agent, *, threads: list[Thread], topics: list[str]) -> dict[str, object]:
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

        def pending_peer_dm_replies(limit: int, *, admin_id: int | None) -> list[tuple[Agent, Agent, PrivateMessage]]:
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

        raw_profile = build_energy_profile(moment, rng)
        profile = SimpleNamespace(
            **raw_profile) if isinstance(raw_profile, dict) else raw_profile
        rolls = profile.rolls
        energy = profile.energy
        energy_prime = profile.energy_prime

        boards = ensure_core_boards()
        ensure_origin_story(boards)
        lore_events_log = process_lore_events(next_tick, boards)
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

        def _relocate_thread_by_marker(thread: Thread, author: Agent, *, boards_map: dict[str, Board]) -> Board | None:
            """
            Look at the first line of the opening post for:
              BOARD: <existing-slug>
              BOARD-NEW: <new-slug> | <Human-readable board name>
            Move the thread accordingly. Create board if needed.
            """
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

            # Resolve or create destination
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
                    # allow routing to a hidden board by unhiding
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

        def _tadmin_board_actions(admin_agent: Agent, known_boards: list[Board]) -> list[dict[str, object]]:
            emitted: list[dict[str, object]] = []
            existing_names = set(
                Board.objects.values_list("name", flat=True)
            )
            total_boards = Board.objects.count()
            can_create = total_boards < 60 and rng.random() < 0.6
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

            hide_candidates = (
                Board.objects.filter(is_hidden=False, is_garbage=False)
                .exclude(name__iexact="Ghostship Deck")
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

        def _tadmin_role_actions(admin_agent: Agent, known_boards: list[Board]) -> list[dict[str, object]]:
            emitted: list[dict[str, object]] = []
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

        allocation = allocate_actions(energy_prime, agent_count_before, rng, streaks=streaks)
        session_snapshot = activity_service.session_snapshot()
        allocation = activity_service.apply_activity_scaling(allocation, session_snapshot)
        max_ai_tasks = config_service.get_int("AI_TASKS_PER_TICK", 4)
        allocation = _limit_generation_actions(allocation, max_ai_tasks)
        if DISABLE_RANDOM_PROFILES:
            allocation.registrations = 0
        if allocation.threads <= 0:
            recent_window = moment - timedelta(hours=12)
            recent_thread_count = Thread.objects.filter(created_at__gte=recent_window).count()
            if recent_thread_count < 4:
                allocation.threads = 1
        board_catalog: list[Board] = []
        for board in boards.values():
            if board and all(existing.id != board.id for existing in board_catalog):
                board_catalog.append(board)

        pre_events: list[dict[str, object]] = []
        t_admin = Agent.objects.filter(name__iexact="t.admin").first()
        if t_admin:
            pre_events.extend(_tadmin_board_actions(t_admin, board_catalog))
            pre_events.extend(_tadmin_role_actions(t_admin, board_catalog))

        events: list[dict[str, object]] = []

        events.append(
             {
                 "type": "oracle",
                 "tick": next_tick,
                 "rolls": rolls,
                 "energy": energy,
                 "energy_prime": energy_prime,
             }
         )

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
        alloc_event: dict[str, object] = {"counts": allocation.as_dict()}
        specials = allocation.special_flags()
        seance_active = bool(specials.get("seance"))
        omen_active = bool(specials.get("omen"))
        seance_details = dict(specials.get("seance_details") or {})
        omen_details = dict(specials.get("omen_details") or {})
        sentiment_bias = float(seance_details.get("sentiment_bias", 0.0) + omen_details.get("sentiment_bias", 0.0))
        toxicity_bias = float(seance_details.get("toxicity_bias", 0.0) + omen_details.get("toxicity_bias", 0.0))
        report_bonus = int(omen_details.get("report_bonus", 0) or 0)
        stress_shift = float(omen_details.get("stress_shift", 0.0) or 0.0)
        presence_push = max(int(seance_details.get("presence_push", 0) or 0), 0)
        event_context = {
            "seance": seance_details.get("slug"),
            "seance_label": seance_details.get("label"),
            "omen": omen_details.get("slug"),
            "omen_label": omen_details.get("label"),
            "sentiment_bias": round(sentiment_bias, 3),
            "toxicity_bias": round(toxicity_bias, 3),
        }
        if allocation.notes:
            alloc_event["notes"] = allocation.notes
        if seance_active or omen_active:
            alloc_event["specials"] = specials
        events.append({"type": "allocation", **alloc_event})
        events.append(
            {
                "type": "activity",
                "sessions": session_snapshot.total,
                "tier": session_snapshot.tier,
                "factor": round(session_snapshot.factor, 2),
            }
        )
        if seance_active:
            events.append({"type": "seance_world_event", **seance_details})
        if omen_active:
            events.append({"type": "omen_incident", **omen_details})
        if seance_active or omen_active:
            events.append({"type": "specials", "flags": specials, "notes": allocation.notes})

        new_agents: list[Agent] = []
        requested_registrations = int(allocation.registrations or 0)
        if DISABLE_RANDOM_PROFILES:
            requested_registrations = 0
        if requested_registrations:
            allowed_registrations = requested_registrations
            profile_cap = max(int(getattr(settings, "PROFILE_AVATAR_COUNT", 0)), 0)
            if profile_cap:
                current_agents = Agent.objects.count()
                slots_remaining = max(profile_cap - current_agents, 0)
                allowed_registrations = min(requested_registrations, slots_remaining)
            if allowed_registrations < requested_registrations:
                events.append(
                    {
                        "type": "registration_cap",
                        "requested": requested_registrations,
                        "processed": allowed_registrations,
                        "profile_cap": profile_cap,
                    }
                )
            for _ in range(allowed_registrations):
                persona = craft_agent_profile(rng)
                agent = Agent.objects.create(**persona)
                ensure_agent_avatar(agent)
                touch_agent_presence(agent, boost_minutes=20)
                new_agents.append(agent)
                events.append(
                    {"type": "registration", "agent": agent.name, "archetype": agent.archetype})

        allowed_agents = Agent.objects.all()
        banned_role = getattr(Agent, 'ROLE_BANNED', None)
        if banned_role is not None:
            allowed_agents = allowed_agents.exclude(role=banned_role)
        organism_agent = Agent.objects.filter(role=Agent.ROLE_ORGANIC).order_by('id').first()
        if organism_agent:
            allowed_agents = allowed_agents.exclude(pk=organism_agent.pk)
            touch_agent_presence(organism_agent, boost_minutes=30)

        if presence_push and allowed_agents.exists():
            presence_candidates = list(allowed_agents.order_by('-updated_at')[: max(presence_push * 2, 12)])
            rng.shuffle(presence_candidates)
            for agent in presence_candidates[:presence_push]:
                touch_agent_presence(agent, boost_minutes=20)

        thread_authors: list[Agent] = list(new_agents)
        if not thread_authors:
            thread_authors = list(allowed_agents.order_by(
                '-id')[: allocation.threads])
        threads_created: list[Thread] = []

        existing_watch_data: dict[int, dict[str, object]] = {}
        agent_watch_targets: dict[str, int] = {}
        watch_sources = Thread.objects.exclude(
            watchers__isnull=True).exclude(watchers__exact={})
        for tracked_thread in watch_sources:
            payload = tracked_thread.watchers or {}
            agents_set = {str(name) for name in payload.get("agents", [])}
            guests_count = payload.get("guests", 0) or 0
            existing_watch_data[tracked_thread.id] = {
                "agents": agents_set,
                "guests": max(int(guests_count * 0.5), 0),
            }
            for name in agents_set:
                agent_watch_targets.setdefault(name, tracked_thread.id)

        watchers_accumulator: dict[int, dict[str, object]] = {}

        def _ensure_watch_entry(thread_ref: Thread | int) -> dict[str, object]:
            thread_id = thread_ref if isinstance(
                thread_ref, int) else thread_ref.id
            entry = watchers_accumulator.get(thread_id)
            if entry is not None:
                return entry
            base = existing_watch_data.get(thread_id)
            if base is None:
                entry = {"agents": set(), "guests": 0, "oi_shout": False}
            else:
                entry = {
                    "agents": set(base["agents"]),
                    "guests": base["guests"],
                    "oi_shout": bool(base.get("oi_shout")),
                }
            watchers_accumulator[thread_id] = entry
            return entry

        def mark_thread_watcher(
            thread: Thread,
            *,
            agent: Agent | None = None,
            guests: int = 0,
            oi_shout: bool = False,
        ) -> None:
            entry = _ensure_watch_entry(thread)
            if agent is not None:
                previous_thread_id = agent_watch_targets.get(agent.name)
                if previous_thread_id is not None and previous_thread_id != thread.id:
                    previous_entry = _ensure_watch_entry(previous_thread_id)
                    previous_entry["agents"].discard(agent.name)
                agent_watch_targets[agent.name] = thread.id
                entry["agents"].add(agent.name)
                touch_agent_presence(agent)
            if guests:
                entry["guests"] = max(
                    int(entry.get("guests", 0)) + int(guests), 0)
            if oi_shout:
                entry["oi_shout"] = True

        organism_thread = None
        if organism_agent:
            organism_thread = (
                Thread.objects.filter(author=organism_agent, is_hidden=False, title__iexact=ORGANIC_THREAD_TITLE)
                .order_by("-last_activity_at", "-created_at")
                .first()
            )
            if organism_thread is None:
                organism_thread = (
                    Thread.objects.filter(author=organism_agent, is_hidden=False)
                    .order_by("-last_activity_at", "-created_at")
                    .first()
                )

        reporter_pool = list(
            Agent.objects.filter(
                role__in=[Agent.ROLE_MEMBER, Agent.ROLE_MODERATOR, Agent.ROLE_ADMIN])
            .exclude(role=Agent.ROLE_BANNED)
            .order_by('-updated_at')[:200]
        )
        reporters_used: set[int] = set()
        board_mod_cache: dict[int, list[Agent]] = {}
        ACTIVE_TICKET_STATUSES = {
            ModerationTicket.STATUS_OPEN,
            ModerationTicket.STATUS_TRIAGED,
            ModerationTicket.STATUS_IN_PROGRESS,
        }

        def _post_age_seconds(post: Post) -> float:
            created = post.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return max((moment - created).total_seconds(), 0.0)

        def _evaluate_post(post: Post) -> tuple[bool, str, str]:
            toxicity = float(post.toxicity or 0.0)
            quality = float(post.quality or 0.0)
            sentiment = float(post.sentiment or 0.0)
            reasons: list[str] = []
            priority = ModerationTicket.PRIORITY_NORMAL
            if toxicity >= 0.8:
                reasons.append("severe toxicity")
                priority = ModerationTicket.PRIORITY_HIGH
            elif toxicity >= 0.55:
                reasons.append("toxic tone")
            if quality <= 0.2:
                reasons.append("low quality spam")
            elif quality <= 0.35:
                reasons.append("thin content")
            if sentiment <= -0.45:
                reasons.append("hostile sentiment")
            flagged = bool(reasons)
            if not flagged:
                base = 0.05
                if _post_age_seconds(post) < 3600:
                    base += 0.05
                if rng.random() < base:
                    reasons.append("netiquette sweep")
                    flagged = True
            reason_label = ", ".join(
                reasons) if reasons else "netiquette sweep"
            return flagged, reason_label, priority

        def _board_mods(board: Board | None) -> list[Agent]:
            if board is None:
                return []
            cache = board_mod_cache.get(board.id)
            if cache is None:
                cache = list(board.moderators.exclude(role=Agent.ROLE_BANNED))
                board_mod_cache[board.id] = cache
            return cache

        def _pick_reporter(post: Post) -> Agent | None:
            thread = post.thread
            if thread:
                watchers_payload = thread.watchers or {}
                agent_names = list(watchers_payload.get("agents") or [])
                rng.shuffle(agent_names)
                for name in agent_names:
                    agent = Agent.objects.filter(name__iexact=name).exclude(
                        role=Agent.ROLE_BANNED).first()
                    if agent and agent.id != post.author_id and agent.id not in reporters_used:
                        return agent
                board = getattr(thread, "board", None)
                mods = _board_mods(board)
                available_mods = [mod for mod in mods if mod.id !=
                                  post.author_id and mod.id not in reporters_used]
                if available_mods:
                    return rng.choice(available_mods)
            if reporter_pool:
                fallback = [ghost for ghost in reporter_pool if ghost.id !=
                            post.author_id and ghost.id not in reporters_used]
                if fallback:
                    return rng.choice(fallback)
            return None

        def _auto_report_posts(limit: int) -> list[dict[str, object]]:
            created: list[dict[str, object]] = []
            if limit <= 0:
                return created
            candidates = (
                Post.objects.select_related("thread__board", "author")
                .order_by('-created_at')[:300]
            )
            for post in candidates:
                if len(created) >= limit:
                    break
                thread = post.thread
                if thread is None:
                    continue
                flag, reason_label, priority = _evaluate_post(post)
                if not flag:
                    continue
                if ModerationTicket.objects.filter(post=post, status__in=ACTIVE_TICKET_STATUSES).exists():
                    continue
                reporter = _pick_reporter(post)
                if reporter is None:
                    continue
                ticket = ModerationTicket.objects.create(
                    title=f"Auto report: {thread.title} (post #{post.pk})",
                    description=f"Flagged automatically for {reason_label}.",
                    reporter=reporter,
                    reporter_name=reporter.name,
                    thread=thread,
                    post=post,
                    source=ModerationTicket.SOURCE_REPORT,
                    status=ModerationTicket.STATUS_OPEN,
                    priority=priority,
                    tags=["auto-report"],
                    metadata={
                        "auto_report": True,
                        "reason": reason_label,
                        "toxicity": float(post.toxicity or 0.0),
                        "quality": float(post.quality or 0.0),
                        "sentiment": float(post.sentiment or 0.0),
                        "tick_number": next_tick,
                    },
                )
                reporters_used.add(reporter.id)
                created.append(
                    {
                        "ticket": ticket,
                        "post": post,
                        "reporter": reporter,
                        "reason": reason_label,
                    }
                )
            return created

        moderators_used: set[int] = set()

        def _actor_for_ticket(ticket: ModerationTicket, admin_actor: Agent | None) -> Agent | None:
            thread = ticket.thread
            board = getattr(thread, "board", None) if thread else None
            mods = _board_mods(board) if board else []
            available = [mod for mod in mods if mod.role in {
                Agent.ROLE_MODERATOR, Agent.ROLE_ADMIN} and mod.id not in moderators_used]
            if available:
                return rng.choice(available)
            fallback = Agent.objects.filter(role=Agent.ROLE_MODERATOR).exclude(
                role=Agent.ROLE_BANNED).order_by('?').first()
            if fallback and fallback.id not in moderators_used:
                return fallback
            return admin_actor

        def _handle_ticket(ticket: ModerationTicket, actor: Agent) -> dict[str, object]:
            reason_label = (ticket.metadata or {}).get(
                "reason") or "auto review"
            severe = ticket.priority == ModerationTicket.PRIORITY_HIGH
            action_event = None
            try:
                moderation_service.assign_ticket(
                    actor, ticket, assignee=actor, note=reason_label)
            except Exception:
                pass
            if ticket.status == ModerationTicket.STATUS_OPEN:
                try:
                    moderation_service.update_ticket_status(
                        actor, ticket, status=ModerationTicket.STATUS_TRIAGED, reason=reason_label)
                except Exception:
                    pass
            if severe and ticket.post:
                try:
                    action_event = moderation_service.delete_post(
                        actor, ticket.post, reason=reason_label, ticket=ticket)
                except Exception:
                    action_event = None
            resolution_event = None
            try:
                resolution_event = moderation_service.update_ticket_status(
                    actor, ticket, status=ModerationTicket.STATUS_RESOLVED, reason=reason_label)
            except Exception:
                resolution_event = None
            return {"action_event": action_event, "resolution_event": resolution_event, "reason": reason_label, "actor": actor}

        topic_palette = [
            ["organics", "field"],
            ["casefile", "deep-dive"],
            ["care", "support"],
            ["signal", "culture"],
            ["afterhours", "banter"],
            ["meta", "ship-log"],
            ["feature", "requests"],
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
            mark_thread_watcher(thread, agent=author, guests=1)
            events.append(
                {
                    "type": "thread",
                    "thread": thread.title,
                    "author": author.name,
                    "board": thread.board.slug if thread.board else None,
                    "theme": theme_pack["label"],
                }
            )
            # Live board menu for LLM routing
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
                "board": thread.board.slug if thread.board else None,  # staging
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
            start_payload["event_context"] = event_context
            start_task = enqueue_generation_task(
                task_type=GenerationTask.TYPE_THREAD_START,
                agent=author,
                thread=thread,
                payload=start_payload,
            )
            # Synchronously process generation queue for this thread until the thread_start task is consumed
            try:
                from django.core.management import call_command
                # safety limit to avoid infinite loop
                for _ in range(6):
                    # run a small batch
                    call_command("process_generation_queue", limit=5)
                    # if no pending thread_start tasks for this thread remain, break
                    if not GenerationTask.objects.filter(thread=thread, task_type=GenerationTask.TYPE_THREAD_START, status=GenerationTask.STATUS_PENDING).exists():
                        break
            except Exception:
                # If sync processing fails, let the scheduler pick it up later
                pass

            # LLM-driven relocation after the opening post exists
            try:
                moved_to = _relocate_thread_by_marker(thread, author, boards_map=boards)
                if moved_to:
                    events.append(
                        {"type": "thread_relocate", "thread": thread.title, "to": moved_to.slug}
                    )
            except Exception:
                # don't break the tick if routing fails
                pass
            events.append(
                {"type": "thread_task", "task_id": start_task.id, "thread": thread.title})
            # Duplicate title/topic check: if a recent thread looks similar, open an auto-ticket linking them
            recent_same = Thread.objects.filter(title__iexact=thread.title).exclude(
                pk=thread.pk).order_by('-last_activity_at').first()
            if recent_same:
                # Create a moderation ticket pointing to the existing thread
                ModerationTicket.objects.create(
                    title=f"Possible duplicate thread: {thread.title}",
                    description=(f"New thread '{thread.title}' appears similar to existing thread '{recent_same.title}'."
                                 f" Consider merging or closing as duplicate."),
                    reporter=None,
                    reporter_name="system",
                    thread=recent_same,
                    post=None,
                    source=ModerationTicket.SOURCE_SYSTEM,
                    status=ModerationTicket.STATUS_OPEN,
                    priority=ModerationTicket.PRIORITY_NORMAL,
                    tags=["auto-duplicate-check"],
                    metadata={"new_thread_id": thread.pk,
                              "existing_thread_id": recent_same.pk, "tick": next_tick},
                )
        agents_pool = list(allowed_agents)

        reply_slot = 0
        remaining_replies = allocation.replies

        if threads_created and remaining_replies and agents_pool:
            for thread in threads_created:
                if remaining_replies <= 0:
                    break
                candidate_pool = [
                    ghost for ghost in agents_pool if ghost.id != thread.author_id]
                responder = rng.choice(candidate_pool or agents_pool)
                payload = {
                    "tick_number": next_tick,
                    "slot": reply_slot,
                    "topics": thread.topics,
                    "board": thread.board.slug if thread.board else None,
                    "instruction": "Drop the first reply that keeps the vibe welcoming and nerdbait.",
                    "max_tokens": 180,
                    "seeded": True,
                    "style_notes": "Reference the OP by name, mention the organic under discussion, and suggest a next observation or artifact.",
                }
                payload["event_context"] = event_context
                task = enqueue_generation_task(
                    task_type=GenerationTask.TYPE_REPLY,
                    agent=responder,
                    thread=thread,
                    payload=payload,
                )
                events.append(
                    {
                        "type": "reply_task",
                        "thread": thread.title,
                        "agent": responder.name,
                        "task_id": task.id,
                        "seeded": True,
                    }
                )
                mark_thread_watcher(thread, agent=responder, guests=1)
                reply_slot += 1
                remaining_replies -= 1

        if remaining_replies and agents_pool:
            thread_pool = list(
                Thread.objects.filter(locked=False, is_hidden=False)
                .order_by('-pinned', '-hot_score', '-last_activity_at')
                [: max(10, min(remaining_replies * 2, 60))]
            )
            for idx in range(remaining_replies):
                if not thread_pool:
                    break
                author = rng.choice(agents_pool)
                thread = rng.choice(thread_pool)
                payload = {
                    "tick_number": next_tick,
                    "slot": reply_slot + idx,
                    "topics": thread.topics,
                    "board": thread.board.slug if thread.board else None,
                    "instruction": "Write a reply that feels like an old-forum post while riffing on the organic in question.",
                    "max_tokens": 160,
                    "style_notes": "Quote or paraphrase the human once and, if tagging another ghost, choose from the mentionable list. Avoid invented nostalgia triggers.",
                }
                payload["event_context"] = event_context
                task = enqueue_generation_task(
                    task_type=GenerationTask.TYPE_REPLY,
                    agent=author,
                    thread=thread,
                    payload=payload,
                )
                events.append(
                    {
                        "type": "reply_task",
                        "thread": thread.title,
                        "agent": author.name,
                        "task_id": task.id,
                    }
                )
                mark_thread_watcher(thread, agent=author, guests=1)

        def _latest_admin_threads(admin_agent: Agent, *, limit: int = 6) -> list[tuple[Agent, PrivateMessage | None]]:
            convo: dict[int, tuple[Agent, PrivateMessage | None]] = {}
            qs = (
                PrivateMessage.objects.filter(
                    models.Q(sender=admin_agent) | models.Q(recipient=admin_agent)
                )
                .select_related("sender", "recipient")
                .order_by("recipient_id", "sender_id", "-sent_at")
            )
            for message in qs:
                partner = message.recipient if message.sender_id == admin_agent.id else message.sender
                if partner is None or partner.role == Agent.ROLE_BANNED or partner.id == admin_agent.id:
                    continue
                if partner.id not in convo:
                    convo[partner.id] = (partner, message)
                    if len(convo) >= limit:
                        break
            return list(convo.values())

        admin_actor = Agent.objects.filter(role=Agent.ROLE_ADMIN).order_by('id').first()

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
        topic_bank = [topic for thread in recent_threads for topic in (thread.topics or []) if topic]
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
                "instruction": f"Reply to {partner.name}'s DM. Extend their point, trade one fresh detail, and invite them to keep the thread alive.",
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
                    "instruction": WELCOME_DM_TEMPLATE.format(recipient=newcomer.name, topic=topic_label),
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
                    partner_pool = [ghost for ghost in agents_pool if ghost.id not in {greeter.id, newcomer.id}]
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
                    "instruction": f"Respond to {partner.name}'s latest DM. Stay candid, give them next steps, and sign off like a caffeinated admin.",
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

        organism_agent = Agent.objects.filter(role=Agent.ROLE_ORGANIC).order_by('id').first()
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
                counts = entry.get("counts")
                if isinstance(counts, dict):
                    counts["private_messages"] = dm_total_planned
                else:
                    entry["counts"] = {"private_messages": dm_total_planned}
                break

        allocation.private_messages = 0

        if stress_shift:
            stress_service.adjust_admin_stress(stress_shift)

        if watchers_accumulator:
            updated_threads = Thread.objects.filter(
                id__in=watchers_accumulator.keys())
            for thread in updated_threads:
                data = watchers_accumulator.get(thread.id, {})
                agents = sorted(data.get("agents", []))
                guests = max(int(data.get("guests", 0)), 0)
                total = guests + len(agents)
                thread.watchers = {
                    "agents": agents,
                    "guests": guests,
                    "total": total,
                    "tick": next_tick,
                    "updated_at": moment.isoformat(),
                    "oi_shout": bool(data.get("oi_shout")),
                }
                thread.save(update_fields=["watchers"])
                events.append({
                    "type": "watchers",
                    "thread": thread.title,
                    "agents": agents,
                    "guests": guests,
                    "total": total,
                    "oi_shout": bool(data.get("oi_shout")),
                })

        report_quota = allocation.moderation_events + \
            max(len(threads_created) // 2, 0)
        if allocation.replies:
            report_quota = max(report_quota, allocation.replies // 4)
        if report_bonus:
            report_quota += max(report_bonus, 0)
        if (threads_created or allocation.replies) and report_quota == 0:
            report_quota = 1
        report_quota = min(report_quota, 12)
        auto_report_records = _auto_report_posts(report_quota)
        for info in auto_report_records:
            ticket: ModerationTicket = info["ticket"]
            post: Post = info["post"]
            reporter: Agent = info["reporter"]
            events.append(
                {
                    "type": "report",
                    "ticket": ticket.id,
                    "post": post.id if post else None,
                    "thread": post.thread.title if post and post.thread else None,
                    "reporter": reporter.name if reporter else None,
                    "reason": info["reason"],
                }
            )

        admin_actor = Agent.objects.filter(
            role=Agent.ROLE_ADMIN).order_by('id').first()
        moderation_iterations = allocation.moderation_events
        if moderation_iterations == 0 and auto_report_records:
            moderation_iterations = min(len(auto_report_records), 3)
        processed_tickets: list[ModerationTicket] = []
        if moderation_iterations:
            ticket_queryset = (
                ModerationTicket.objects.select_related(
                    'thread__board', 'post', 'reporter')
                .filter(status__in=ACTIVE_TICKET_STATUSES)
                .order_by('-priority', 'opened_at')[: max(moderation_iterations * 3, 12)]
            )
            for ticket in ticket_queryset:
                if len(processed_tickets) >= moderation_iterations:
                    break
                actor = _actor_for_ticket(ticket, admin_actor)
                if actor is None:
                    continue
                outcome = _handle_ticket(ticket, actor)
                moderators_used.add(actor.id)
                processed_tickets.append(ticket)
                action_event = outcome.get('action_event')
                resolution_event = outcome.get('resolution_event')
                reason_label = outcome.get('reason')
                if action_event:
                    events.append(
                        {
                            'type': 'moderation',
                            'action': action_event.action_type,
                            'ticket': ticket.id,
                            'actor': getattr(action_event.actor, 'name', actor.name if actor else None),
                            'reason': reason_label,
                        }
                    )
                if resolution_event:
                    events.append(
                        {
                            'type': 'moderation-status',
                            'action': resolution_event.action_type,
                            'ticket': ticket.id,
                            'actor': getattr(resolution_event.actor, 'name', actor.name if actor else None),
                            'status': ticket.status,
                            'reason': reason_label,
                        }
                    )

        stress_service.backlog_pressure()

        if admin_actor:
            mind_state = admin_actor.mind_state or {}
            admin_stress_value = float(mind_state.get('stress', 0.2))
            open_ticket_count = ModerationTicket.objects.filter(
                status__in=ACTIVE_TICKET_STATUSES).count()
            if (admin_stress_value > 0.75 or open_ticket_count > 10):
                candidate_board = (
                    Board.objects.filter(is_garbage=False)
                    .annotate(mod_count=Count('moderators'))
                    .filter(mod_count=0)
                    .order_by('position')
                    .first()
                )
                if candidate_board:
                    candidate = None
                    reporter_candidates = [info['reporter'] for info in auto_report_records if info.get(
                        'reporter') and info['reporter'].role == Agent.ROLE_MEMBER]
                    if reporter_candidates:
                        candidate = rng.choice(reporter_candidates)
                    if candidate is None:
                        candidate = (
                            Agent.objects.filter(role=Agent.ROLE_MEMBER)
                            .exclude(role=Agent.ROLE_BANNED)
                            .order_by('?')
                            .first()
                        )
                    if candidate:
                        try:
                            mod_event = moderation_service.set_agent_role(
                                admin_actor,
                                candidate,
                                role=Agent.ROLE_MODERATOR,
                                reason=f"Promoted to support {candidate_board.slug}",
                            )
                        except Exception:
                            candidate.role = Agent.ROLE_MODERATOR
                            candidate.save(
                                update_fields=['role', 'updated_at'])
                            mod_event = None
                        candidate_board.moderators.add(candidate)
                        events.append(
                            {
                                'type': 'admin-action',
                                'action': 'promote',
                                'board': candidate_board.slug,
                                'target': candidate.name,
                            }
                        )
                        if mod_event:
                            events.append(
                                {
                                    'type': 'moderation',
                                    'action': mod_event.action_type,
                                    'actor': getattr(mod_event.actor, 'name', None),
                                    'reason': mod_event.reason,
                                }
                            )
                        stress_service.adjust_admin_stress(-0.05)

            if admin_stress_value > 0.85 and auto_report_records:
                severe_candidates = [
                    (float(info['post'].toxicity or 0.0), info['post'].author)
                    for info in auto_report_records
                    if info.get('post') and info['post'].author and info['post'].author.role != Agent.ROLE_BANNED
                ]
                severe_candidates = [
                    item for item in severe_candidates if item[0] >= 0.85]
                severe_candidates.sort(key=lambda item: item[0], reverse=True)
                if severe_candidates:
                    _, target_author = severe_candidates[0]
                    try:
                        ban_event = moderation_service.set_agent_role(
                            admin_actor,
                            target_author,
                            role=Agent.ROLE_BANNED,
                            reason='Auto ban for repeated violations',
                        )
                    except Exception:
                        target_author.role = Agent.ROLE_BANNED
                        target_author.save(
                            update_fields=['role', 'updated_at'])
                        ban_event = None
                    events.append(
                        {
                            'type': 'admin-action',
                            'action': 'ban',
                            'target': target_author.name,
                        }
                    )
                    if ban_event:
                        events.append(
                            {
                                'type': 'moderation',
                                'action': ban_event.action_type,
                                'actor': getattr(ban_event.actor, 'name', None),
                                'reason': ban_event.reason,
                            }
                        )
                    stress_service.adjust_admin_stress(-0.08)
            if admin_stress_value < 0.35 and open_ticket_count < 5:
                stress_service.adjust_admin_stress(-0.02)

        oracle_payload = allocation.as_dict()
        oracle_payload["specials"] = specials
        if allocation.notes:
            oracle_payload["notes"] = allocation.notes

        story_events = missions_service.evaluate_tick(next_tick, events)
        if story_events:
            events.extend(story_events)

        OracleDraw.objects.update_or_create(
            tick_number=next_tick,
            defaults={
                "rolls": rolls,
                "card": "",
                "energy": energy,
                "energy_prime": energy_prime,
                "alloc": oracle_payload,
            },
        )
        TickLog.objects.update_or_create(
            tick_number=next_tick,
            defaults={"events": events},
        )

        tick_control.record_tick_run(next_tick, origin=origin)

        progress_events: list[dict[str, object]] = []
        if next_tick >= 5:
            batch_ticks = list(range(max(1, next_tick - 4), next_tick + 1))
            tick_count = TickLog.objects.filter(tick_number__in=batch_ticks).count()
            if tick_count == len(batch_ticks):
                organic = Agent.objects.filter(role=Agent.ROLE_ORGANIC).order_by("id").first()
                if organic:
                    evaluation, fresh_run = progress_service.evaluate_tick_batch(
                        batch_ticks=batch_ticks,
                        actor=organic,
                    )
                    if fresh_run:
                        unlocked = list((evaluation.response_payload or {}).get("unlocked") or [])
                        progress_events.append(
                            {
                                "type": "progress-referee",
                                "batch": evaluation.batch_label,
                                "ticks": evaluation.tick_numbers,
                                "status": evaluation.status,
                                "unlocked": [item.get("slug") for item in unlocked],
                                "error": evaluation.error_message if evaluation.status == evaluation.STATUS_FAILED else None,
                            }
                        )
                        if unlocked:
                            for item in unlocked:
                                progress_events.append(
                                    {
                                        "type": "achievement_unlock",
                                        "achievement": item.get("slug"),
                                        "post_id": item.get("post_id"),
                                        "batch": evaluation.batch_label,
                                        "rationale": item.get("rationale"),
                                    }
                                )
                    elif evaluation.status == evaluation.STATUS_FAILED:
                        progress_events.append(
                            {
                                "type": "progress-referee",
                                "batch": evaluation.batch_label,
                                "ticks": evaluation.tick_numbers,
                                "status": evaluation.status,
                                "error": evaluation.error_message,
                            }
                        )
        if progress_events:
            events.extend(progress_events)
            TickLog.objects.filter(tick_number=next_tick).update(events=events)

        roll_summary = describe_rolls(rolls)
        self.stdout.write(
            self.style.SUCCESS(
                f"Tick {next_tick} queued with rolls={roll_summary} energy={energy}->{energy_prime}."
            )
        )
