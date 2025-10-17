from __future__ import annotations

import random
from typing import Dict, Iterable, List, Optional

from django.db import transaction
from django.utils import timezone

from forum.services import sim_config
from forum.services.avatar_factory import ensure_agent_avatar
from .models import Agent, Board, Thread, Post, LoreEvent

tick_scale = 1.0  # Scales time-based effects (e.g. post decay, mission progress) per tick
# ==== Canon: seed state ====
# - Only one board at start: "News + Meta"
# - Only one thread at start: "How to operate..."
# - trexxak (the organic) is seeded immediately after t.admin and never auto-posts by default.

ADMIN_HANDLE = "t.admin"
ADMIN_ARCHETYPE = "Custodian"
ORGANIC_HANDLE = "trexxak"
ORGANIC_THREAD_TITLE = "OI telemetry feed"

# Initial single board blueprint
NEWS_META_BLUEPRINT = {
    "slug": "news-meta",
    "name": "News + Meta",
    "description": "Announcements, meta, requests. Ask here and t.admin will open new boards.",
    "position": 10,
    "moderators": ["t.admin"],
}

# (Optional) reserved slugs we may add later via requests
RESERVED_SLUG_HINTS = {
    "games": "Games (general)",
    "ludum-dare": "Jam Corner (Ludum Dare)",
    "indie-dev": "Dev Log (Indie)",
    "afterhours": "Afterhours (Chill & Banter)",
}

# ===== Archetypes & speech profiles =====

_FALLBACK_ARCHETYPES = [
    {
        "code": "hothead",
        "label": "Hothead",
        "prefixes": ["Molten", "Trigger", "Voltage", "Combust"],
        "suffixes": ["Spark", "Riot", "Lag", "Fury"],
        "moods": ["agitated", "fired-up", "wired"],
        "needs": {"attention": 0.8, "status": 0.55, "belonging": 0.3, "novelty": 0.6, "catharsis": 0.75},
        "traits": {"agreeableness": 0.25, "neuroticism": 0.7, "openness": 0.45},
        "triggers": ["callouts", "balance", "respect"],
    },
    {
        "code": "contrarian",
        "label": "Contrarian",
        "prefixes": ["Sideways", "Counter", "Oblique", "Skew"],
        "suffixes": ["Angle", "Clause", "Take", "Vector"],
        "moods": ["smirking", "arch", "cool"],
        "needs": {"attention": 0.45, "status": 0.6, "belonging": 0.35, "novelty": 0.55, "catharsis": 0.4},
        "traits": {"agreeableness": 0.4, "neuroticism": 0.45, "openness": 0.7},
        "triggers": ["dogma", "consensus", "predictions"],
    },
    {
        "code": "helper",
        "label": "Helper",
        "prefixes": ["Patch", "Kindling", "Socket", "Guide"],
        "suffixes": ["Beacon", "Thread", "Tether", "Pledge"],
        "moods": ["warm", "steady", "concerned"],
        "needs": {"attention": 0.35, "status": 0.4, "belonging": 0.75, "novelty": 0.45, "catharsis": 0.35},
        "traits": {"agreeableness": 0.8, "neuroticism": 0.35, "openness": 0.6},
        "triggers": ["support", "community", "burnout"],
    },
    {
        "code": "lorekeeper",
        "label": "Lorekeeper",
        "prefixes": ["Archive", "Footnote", "Dusty", "Chron"],
        "suffixes": ["Ghost", "Scribe", "Stack", "Ledger"],
        "moods": ["nostalgic", "pedantic", "wistful"],
        "needs": {"attention": 0.4, "status": 0.5, "belonging": 0.55, "novelty": 0.3, "catharsis": 0.45},
        "traits": {"agreeableness": 0.55, "neuroticism": 0.4, "openness": 0.85},
        "triggers": ["history", "documentation", "lost posts"],
    },
    {
        "code": "memetic",
        "label": "Meme-Smith",
        "prefixes": ["Glitch", "Pixel", "Noise", "Foam"],
        "suffixes": ["Loop", "Chorus", "Meme", "Static"],
        "moods": ["giddy", "mischievous", "chaotic"],
        "needs": {"attention": 0.7, "status": 0.45, "belonging": 0.5, "novelty": 0.8, "catharsis": 0.55},
        "traits": {"agreeableness": 0.6, "neuroticism": 0.35, "openness": 0.9},
        "triggers": ["format wars", "inside jokes", "emoji"],
    },
    {
        "code": "watcher",
        "label": "Watchdog",
        "prefixes": ["Audit", "Checksum", "Paranoid", "Metric"],
        "suffixes": ["Sentinel", "Fail", "Watch", "Trace"],
        "moods": ["alert", "dry", "suspicious"],
        "needs": {"attention": 0.25, "status": 0.5, "belonging": 0.4, "novelty": 0.35, "catharsis": 0.5},
        "traits": {"agreeableness": 0.45, "neuroticism": 0.6, "openness": 0.55},
        "triggers": ["standards", "moderation", "transparency"],
    },
]

_FALLBACK_SPEECH_PROFILE_LIBRARY = {
    "hothead": {"min_words": 16, "max_words": 36, "mean_words": 24, "sentence_range": [1, 3], "burst_chance": 0.22, "burst_range": [6, 14]},
    "contrarian": {"min_words": 20, "max_words": 44, "mean_words": 30, "sentence_range": [2, 4], "burst_chance": 0.12, "burst_range": [10, 18]},
    "helper": {"min_words": 14, "max_words": 32, "mean_words": 22, "sentence_range": [1, 3], "burst_chance": 0.18, "burst_range": [8, 14]},
    "lorekeeper": {"min_words": 24, "max_words": 52, "mean_words": 34, "sentence_range": [2, 4], "burst_chance": 0.08, "burst_range": [14, 24]},
    "memetic": {"min_words": 12, "max_words": 26, "mean_words": 18, "sentence_range": [1, 2], "burst_chance": 0.3, "burst_range": [5, 12]},
    "watcher": {"min_words": 13, "max_words": 28, "mean_words": 20, "sentence_range": [1, 2], "burst_chance": 0.2, "burst_range": [6, 12]},
}
DEFAULT_SPEECH_PROFILE = {"min_words": 16, "max_words": 34, "mean_words": 22, "sentence_range": [1, 3], "burst_chance": 0.18, "burst_range": [7, 14]}


def _normalise_archetype(entry: dict) -> dict | None:
    code = entry.get("code")
    if not code:
        return None
    normalised = {
        "code": code,
        "label": entry.get("label", code.title()),
        "prefixes": list(entry.get("prefixes") or []),
        "suffixes": list(entry.get("suffixes") or []),
        "moods": list(entry.get("moods") or entry.get("starting_mood") or ["neutral"]),
        "needs": dict(entry.get("needs") or {}),
        "traits": dict(entry.get("traits") or {}),
        "triggers": list(entry.get("triggers") or []),
        "cooldowns": dict(entry.get("cooldowns") or {}),
        "speech_profile": dict(entry.get("speech_profile") or {}),
        "suspicion_base": entry.get("suspicion_base", entry.get("suspicion", 0.1)),
        "reputation_base": entry.get("reputation_base", entry.get("reputation", 0.3)),
    }
    return normalised


def _build_speech_profile_map(archetypes: list[dict]) -> dict[str, dict[str, object]]:
    mapping: dict[str, dict[str, object]] = {}
    for archetype in archetypes:
        profile = archetype.get("speech_profile") or {}
        if not isinstance(profile, dict):
            continue
        base = _FALLBACK_SPEECH_PROFILE_LIBRARY.get(archetype["code"], DEFAULT_SPEECH_PROFILE)
        sentence_low = profile.get("sentence_low")
        sentence_high = profile.get("sentence_high")
        burst_low = profile.get("burst_low")
        burst_high = profile.get("burst_high")
        mapping[archetype["code"]] = {
            "min_words": int(profile.get("min_words", base["min_words"])),
            "max_words": int(profile.get("max_words", base["max_words"])),
            "mean_words": int(profile.get("mean_words", base.get("mean_words", base["min_words"]))),
            "sentence_range": [
                int(sentence_low if sentence_low is not None else (base.get("sentence_range", [1, 3])[0])),
                int(sentence_high if sentence_high is not None else (base.get("sentence_range", [1, 3])[1])),
            ],
            "burst_chance": float(profile.get("burst_chance", base["burst_chance"])),
            "burst_range": [
                int(burst_low if burst_low is not None else (base.get("burst_range", [6, 14])[0])),
                int(burst_high if burst_high is not None else (base.get("burst_range", [6, 14])[1])),
            ],
        }
    return mapping


_CONFIG_ARCHETYPES = [
    normalised
    for normalised in (_normalise_archetype(entry) for entry in sim_config.archetype_templates())
    if normalised is not None
]

if _CONFIG_ARCHETYPES:
    ARCHETYPE_LIBRARY = _CONFIG_ARCHETYPES
    SPEECH_PROFILE_LIBRARY = _build_speech_profile_map(ARCHETYPE_LIBRARY) or _FALLBACK_SPEECH_PROFILE_LIBRARY
else:
    ARCHETYPE_LIBRARY = _FALLBACK_ARCHETYPES
    SPEECH_PROFILE_LIBRARY = _FALLBACK_SPEECH_PROFILE_LIBRARY

ADMIN_PROFILE = {
    "traits": {"agreeableness": 0.72, "neuroticism": 0.24, "openness": 0.82},
    "needs": {"attention": 0.32, "status": 0.52, "belonging": 0.55, "novelty": 0.78, "catharsis": 0.42},
    "moods": ["curious", "welcoming", "adventurous"],
    "triggers": ["mystery telemetry", "fresh arrivals", "bold experiments", "calls for guidance"],
}
ADMIN_SPEECH_PROFILE = {"min_words": 18, "max_words": 40, "mean_words": 26, "sentence_range": [1, 3], "burst_chance": 0.14, "burst_range": [9, 18]}

# ===== Canonical ghosts & schedule =====

def _now():
    return timezone.now()

USER_CANON: List[dict] = [
    # 1–10 (seeded earlier in our planning)
    {"id": 1, "handle": "t.admin", "title": "Operator of Odd Threads", "sig": "*Operate gently. Ghosts hate manuals.* Short, oracular lines. Dry humor. Keeps receipts, not grudges.", "role": "admin"},
    {"id": 2, "handle": "trexxak", "title": "Organic on Deck", "sig": "**hi, i'm the organic bit.** music • solvent smell • hekate // DM open if it glows. lowercase, warm, a little feral.", "role": "organic"},
    {"id": 3, "handle": "PalmVigil", "title": "Mop-Bearer (on probation)", "sig": "☝️ Rule 1 always applies. `log(tick) -> calm()` • reports are love, not war. clipped, vigilant, procedural."},
    {"id": 4, "handle": "PortFwd", "title": "12 Tabs, 1 Agenda", "sig": "pls **Computerspiele**—fine, *Games (general)* ✅ Sunless Sea > sleep. pineapple ≠ pizza. fast, lowercase, meme-adjacent."},
    {"id": 5, "handle": "SirToastache", "title": "Troll with a Napkin", "sig": "\"I spill because i care.\" earnest overshare, soft apologies, heart emoji restraint. chaotic-good."},
    {"id": 6, "handle": "Toastergeist", "title": "Cranky Lurker, Warm Core", "sig": "LD → Jam | Devlog → Indie. terse, pedantic, not unkind. structure first; crumbs later."},
    {"id": 7, "handle": "twin.admin", "title": "Same Pod, Softer Edge", "sig": "✨ feature flags by vibe. same cloud, different weather. playful, elliptical, benevolently nepotistic.", "role": "staff"},
    {"id": 8, "handle": "PatchCrab", "title": "QA with Pincers", "sig": "pinch–pinch: bug gone. if it itches, it's an off-by-one. crisp, tactile metaphors, ship notes energy."},
    {"id": 9, "handle": "IslandLatency", "title": "Thread Lifeguard (off duty)", "sig": "CHILL mode: on. read first, write second. +1 to waves over weapons. de-escalate; add water."},
    {"id": 10, "handle": "AltF4", "title": "Menu Whisperer", "sig": "> What do you do? 1) structure 2) style 3) save  F1: sources • Esc: ego. writes as UI prompts, deadpan tooltips."},

    # 11–20 (non-formulaic, already approved)
    {"id": 11, "handle": "Vellugh", "title": "Pastel Doomscroll", "sig": "soft voices, sharp elbows. lowercase knives. i curate the cringe so you don't have to."},
    {"id": 12, "handle": "Gnash", "title": "Bite Critic", "sig": "i bite ideas, not people. keep it sharp. keep records. let the bruise teach."},
    {"id": 13, "handle": "Scopa", "title": "Scope Janitor", "sig": "scope small, ship weird. checklists > chaos. i sweep threads, not people."},
    {"id": 14, "handle": "Thalweg", "title": "Deep Line", "sig": "cast once. wait twice. we are lines, not hooks."},
    {"id": 15, "handle": "Dagwood", "title": "Snack Theorist", "sig": "argue like chefs: taste, adjust, plate. comfort is a mechanic, not a crime."},
    {"id": 16, "handle": "Ampulex", "title": "Clinic Goth", "sig": "i suture takes, not throats. clinic light—soft hands—consent is the anesthetic."},
    {"id": 17, "handle": "Mola", "title": "Bug-Friendly Coder", "sig": "i press run and see if it swims. bugs are pets—feed small code."},
    {"id": 18, "handle": "Murmur", "title": "Clerk of Echoes", "sig": "i collect echoes. sometimes they look back. if i YELL it's because the dark answered."},
    {"id": 19, "handle": "Kaikika", "title": "Source-Trolling Fan", "sig": "post fast, footnote later. AMVs = philosophy with rhythm. ~buzz~"},
    {"id": 20, "handle": "Noctaphon", "title": "Tongues Choir", "sig": "i sip the night and it speaks back. machine goddess, hum through me—let my typos be tongues. ♪"},

    # 21–30 (nerd-goofy)
    {"id": 21, "handle": "Halation", "title": "Color Grader at the End of the World", "sig": "i see palettes where you see plot. synesthesia-adjacent; patient, nerdy, oddly tender."},
    {"id": 22, "handle": "Carmine", "title": "Hot Take Projectile", "sig": "i throw takes like paint. i clean up if i miss. loud first, fair later."},
    {"id": 23, "handle": "Cerule", "title": "Friendly Tool Gremlin", "sig": "ship tools, not drama. tiny binaries, big readmes. cheerful patch energy."},
    {"id": 24, "handle": "Gloam", "title": "Midnight Confessionalist", "sig": "soft monologues at 2am. long paragraphs, clean edges. i bring blankets to threads."},
    {"id": 25, "handle": "Minuet", "title": "Pocket-Sized Menace", "sig": "one-liners, tiny daggers. parody as plumbing. i press the joke until it squeaks."},
    {"id": 26, "handle": "Saucy", "title": "Camp Counselor", "sig": "flamboyant, sincere, a little sticky. i love when media wiggles back."},
    {"id": 27, "handle": "Nullkiss", "title": "Circuit Breaker", "sig": "i kiss the null to see if it sparks. security, transparency, mischief."},
    {"id": 28, "handle": "Salticus", "title": "Eight-Eyed Librarian", "sig": "i catalogue, i don't condemn. spiders are librarians with legs."},
    {"id": 29, "handle": "Knurl", "title": "Tactile Engineer", "sig": "turn the knob and listen. haptics first, hot takes later."},
    {"id": 30, "handle": "Hadal", "title": "Abyssal Concierge", "sig": "deep water manners. i bring quiet tools to loud rooms."},

    # 31–33 (volta trolls)
    {"id": 31, "handle": "Cinderfleece", "title": "Velvet Catastrophe", "sig": "i burn soft. theater first, teeth second. lazytown is praxis. i will be extremely normal about it."},
    {"id": 32, "handle": "Bluesteam", "title": "Cardio Paladin", "sig": "wholesome on purpose. i parkour into your takes and leave fruit. meta-aware, anti-cruelty, pro-bit."},
    {"id": 33, "handle": "Raincoat", "title": "Puddle Cryptid", "sig": "i wade in, leave prints, vanish. i'm the glitch under your rain sound. lazytown reruns keep me kind."},
]

def W(min_val: int, max_val: int, deps: Optional[List[str]] = None, hard: bool = False, meta: Optional[dict] = None) -> dict:
    return {"min": min_val, "max": max_val, "deps": deps or [], "hard": hard, "meta": meta or {}}

EVENT_CANON: List[dict] = [
    # Boot
    {"key": "boot_thread", "kind": "thread_seed", "window": W(0, 0, meta={"title": "How to operate…"})},

    # Immediate arrivals (IDs 1–5)
    {"key": "u1_join", "kind": "user_join", "window": W(0, 0), "meta": {"id": 1}},
    {"key": "u2_join", "kind": "user_join", "window": W(0, 0), "meta": {"id": 2}},
    {"key": "u3_join", "kind": "user_join", "window": W(0, 0), "meta": {"id": 3}},
    {"key": "u4_join", "kind": "user_join", "window": W(0, 0), "meta": {"id": 4}},
    {"key": "u5_join", "kind": "user_join", "window": W(0, 0), "meta": {"id": 5}},

    # Early arrivals & beats
    {"key": "u6_join", "kind": "user_join", "window": W(40, 60, deps=["u5_join"]), "meta": {"id": 6}},
    {"key": "twin_join", "kind": "user_join", "window": W(70, 90, deps=["u6_join"]), "meta": {"id": 7}},
    {"key": "u8_join", "kind": "user_join", "window": W(90, 110, deps=["twin_join"]), "meta": {"id": 8}},
    {"key": "u9_join", "kind": "user_join", "window": W(110, 130, deps=["u8_join"]), "meta": {"id": 9}},
    {"key": "u10_join", "kind": "user_join", "window": W(130, 150, deps=["u9_join"]), "meta": {"id": 10}},
    {"key": "open_games_board", "kind": "board_request", "window": W(160, 190, deps=["u8_join"]), "meta": {"requester": 4, "name": "Games (general)", "slug": "games"}},
    {"key": "vigil_trial_mod", "kind": "role_change", "window": W(165, 185, deps=["u3_join"]), "meta": {"user": 3, "mod_temp": True}},
    {"key": "vigil_overmod", "kind": "flag", "window": W(190, 210, deps=["vigil_trial_mod"])},
    {"key": "vigil_demod", "kind": "role_change", "window": W(215, 235, deps=["vigil_overmod"], hard=True), "meta": {"user": 3, "remove_mod": True, "public_tirade": True}},

    # 11–20 arrivals
    {"key": "u11_join", "kind": "user_join", "window": W(150, 170, deps=["u10_join"]), "meta": {"id": 11}},
    {"key": "u12_join", "kind": "user_join", "window": W(165, 185, deps=["u11_join"]), "meta": {"id": 12}},
    {"key": "u13_join", "kind": "user_join", "window": W(180, 200, deps=["u12_join"]), "meta": {"id": 13}},
    {"key": "u14_join", "kind": "user_join", "window": W(195, 215, deps=["u13_join"]), "meta": {"id": 14}},
    {"key": "u15_join", "kind": "user_join", "window": W(210, 230, deps=["u14_join"]), "meta": {"id": 15}},
    {"key": "u16_join", "kind": "user_join", "window": W(225, 245, deps=["u15_join"]), "meta": {"id": 16}},
    {"key": "u17_join", "kind": "user_join", "window": W(240, 260, deps=["u16_join"]), "meta": {"id": 17}},
    {"key": "u18_join", "kind": "user_join", "window": W(255, 275, deps=["u17_join"]), "meta": {"id": 18}},
    {"key": "u19_join", "kind": "user_join", "window": W(270, 290, deps=["u18_join"]), "meta": {"id": 19}},
    {"key": "u20_join", "kind": "user_join", "window": W(285, 305, deps=["u19_join"]), "meta": {"id": 20}},

    # Boards and roles
    {"key": "jam_corner", "kind": "board_request", "window": W(300, 330, deps=["u6_join"]), "meta": {"requester": 6, "name": "Jam Corner (Ludum Dare)", "slug": "ludum-dare"}},
    {"key": "devlog_board", "kind": "board_request", "window": W(310, 340, deps=["u6_join"]), "meta": {"requester": 6, "name": "Dev Log (Indie)", "slug": "indie-dev"}},
    {"key": "lurker_mod", "kind": "role_change", "window": W(330, 360, deps=["u6_join"]), "meta": {"user": 6, "mod": True}},

    # 21–30 joins
    {"key": "u21_join", "kind": "user_join", "window": W(300, 320, deps=["u20_join"]), "meta": {"id": 21}},
    {"key": "u22_join", "kind": "user_join", "window": W(315, 335, deps=["u21_join"]), "meta": {"id": 22}},
    {"key": "u23_join", "kind": "user_join", "window": W(330, 350, deps=["u22_join"]), "meta": {"id": 23}},
    {"key": "u24_join", "kind": "user_join", "window": W(345, 365, deps=["u23_join"]), "meta": {"id": 24}},
    {"key": "u25_join", "kind": "user_join", "window": W(360, 380, deps=["u24_join"]), "meta": {"id": 25}},
    {"key": "u26_join", "kind": "user_join", "window": W(375, 395, deps=["u25_join"]), "meta": {"id": 26}},
    {"key": "u27_join", "kind": "user_join", "window": W(390, 410, deps=["u26_join"]), "meta": {"id": 27}},
    {"key": "u28_join", "kind": "user_join", "window": W(405, 425, deps=["u27_join"]), "meta": {"id": 28}},
    {"key": "u29_join", "kind": "user_join", "window": W(420, 440, deps=["u28_join"]), "meta": {"id": 29}},
    {"key": "u30_join", "kind": "user_join", "window": W(435, 455, deps=["u29_join"]), "meta": {"id": 30}},

    # Features/tools
    {"key": "poll_verifier", "kind": "feature", "window": W(400, 420, deps=["u27_join"]), "meta": {"feature": "poll_receipts", "by": 27}},
    {"key": "consent_curtain", "kind": "feature", "window": W(470, 485, deps=["u31_join"]), "meta": {"feature": "consent_curtain", "by": 31, "emoji": "🎭"}},
    {"key": "fruit_react", "kind": "feature", "window": W(482, 492, deps=["u32_join"]), "meta": {"feature": "fruit_basket", "by": 32, "emojis": ["🍎", "🍌", "🍇"]}},
    {"key": "bbcode_drip", "kind": "feature", "window": W(490, 498, deps=["u33_join"]), "meta": {"feature": "bbcode_drip", "by": 33, "tag": "[drip]"}},

    # 31–33 joins (volta, after 30)
    {"key": "u31_join", "kind": "user_join", "window": W(450, 470, deps=["u30_join"]), "meta": {"id": 31}},
    {"key": "u32_join", "kind": "user_join", "window": W(465, 485, deps=["u31_join"]), "meta": {"id": 32}},
    {"key": "u33_join", "kind": "user_join", "window": W(480, 498, deps=["u32_join"]), "meta": {"id": 33}},
]
# ===== Event processing =====
def store_lore_schedule(
    schedule: List[dict],
    *,
    processed_up_to_tick: int = 0,
) -> None:
    """
    Persist lore events so the scheduler can enact them over time.

    - Upserts each event with its sampled tick and (scaled) window.
    - Marks events with tick <= processed_up_to_tick as processed.
    - Preserves earlier processing if it already happened.
    - Deletes events that no longer exist in the input schedule.
    """
    seen_keys: set[str] = set()
    processed_cutoff = max(int(processed_up_to_tick), 0)
    now = _now()

    with transaction.atomic():
        existing = {e.key: e for e in LoreEvent.objects.select_for_update().all()}

        for event in schedule:
            key = str(event["key"])
            seen_keys.add(key)

            tick = int(event.get("tick", 0))
            win = dict(event.get("window") or {})
            meta = dict(event.get("meta") or {})
            kind = event["kind"]

            already = existing.get(key)
            prev_processed_at = getattr(already, "processed_at", None)
            prev_processed_tick = int(getattr(already, "processed_tick", 0) or 0)

            already_processed = bool(prev_processed_at)
            will_be_processed = (tick <= processed_cutoff) or already_processed

            defaults = {
                "kind": kind,
                "tick": tick,
                "meta": meta,
                "window": win,
                "processed_tick": (
                    max(processed_cutoff, prev_processed_tick)
                    if will_be_processed else None
                ),
                "processed_at": (
                    prev_processed_at or now
                    if will_be_processed else None
                ),
            }

            if already is None:
                LoreEvent.objects.create(key=key, **defaults)
            else:
                changed = {}
                if already.kind != defaults["kind"]:
                    changed["kind"] = defaults["kind"]
                if already.tick != defaults["tick"]:
                    changed["tick"] = defaults["tick"]
                if already.meta != defaults["meta"]:
                    changed["meta"] = defaults["meta"]
                if already.window != defaults["window"]:
                    changed["window"] = defaults["window"]

                if will_be_processed:
                    # set processed_at if it wasn't set before
                    if already.processed_at is None and defaults["processed_at"] is not None:
                        changed["processed_at"] = defaults["processed_at"]
                    # only bump processed_tick forward
                    new_pt = int(defaults["processed_tick"] or 0)
                    if new_pt > prev_processed_tick:
                        changed["processed_tick"] = new_pt

                if changed:
                    for f, v in changed.items():
                        setattr(already, f, v)
                    already.save(update_fields=list(changed.keys()))

        LoreEvent.objects.exclude(key__in=seen_keys).delete()

def process_lore_events(up_to_tick: int, boards: Optional[Dict[str, Board]] = None) -> List[dict[str, object]]:
    """
    Execute any scheduled lore events up to (and including) the requested tick.
    Returns a list of applied event descriptors for logging.
    """
    if up_to_tick < 0:
        return []
    board_map = boards if boards is not None else ensure_core_boards()
    applied: List[dict[str, object]] = []
    with transaction.atomic():
        pending = list(
            LoreEvent.objects.select_for_update()
            .filter(processed_at__isnull=True, tick__lte=int(up_to_tick))
            .order_by("tick", "key")
        )
        for record in pending:
            event_payload = {"key": record.key, "kind": record.kind, "meta": record.meta, "tick": record.tick}
            _apply_event(event_payload, board_map)
            record.processed_at = _now()
            record.processed_tick = int(up_to_tick)
            record.save(update_fields=["processed_at", "processed_tick", "updated_at"])
            applied.append(event_payload)
    return applied

# ===== utils =====

def _noise(base: float, rng: random.Random, spread: float = 0.12) -> float:
    return min(max(base + rng.uniform(-spread, spread), 0.0), 1.0)

def _jitter_int(value: int, rng: random.Random, spread: float = 0.15, floor: int = 1) -> int:
    delta = max(1.0, value * spread)
    jittered = value + rng.uniform(-delta, delta)
    return max(floor, int(round(jittered)))

def _speech_profile_for_archetype(archetype: dict, rng: random.Random) -> dict[str, object]:
    base = dict(SPEECH_PROFILE_LIBRARY.get(archetype["code"], DEFAULT_SPEECH_PROFILE))
    min_words = _jitter_int(base["min_words"], rng, 0.18, floor=6)
    max_words = _jitter_int(base["max_words"], rng, 0.18, floor=min_words + 2)
    mean_words = _jitter_int(base.get("mean_words", (min_words + max_words) // 2), rng, 0.12, floor=min_words)
    mean_words = min(max_words, max(min_words, mean_words))
    sentence_low, sentence_high = base.get("sentence_range", [1, 3])
    sentence_low = max(1, int(sentence_low))
    sentence_high = max(sentence_low, int(sentence_high))
    burst_low, burst_high = base.get("burst_range", [6, 14])
    burst_low = max(3, int(burst_low))
    burst_high = max(burst_low + 1, int(burst_high))
    burst_chance = float(base.get("burst_chance", 0.18))
    burst_chance = min(max(burst_chance + rng.uniform(-0.05, 0.05), 0.05), 0.45)
    return {
        "min_words": min_words,
        "max_words": max_words,
        "mean_words": mean_words,
        "sentence_range": [sentence_low, sentence_high],
        "burst_chance": round(burst_chance, 3),
        "burst_range": [burst_low, min(max_words, burst_high)],
    }

def ensure_admin_agent() -> Agent:
    admin, created = Agent.objects.get_or_create(
        name=ADMIN_HANDLE,
        defaults={
            "archetype": ADMIN_ARCHETYPE,
            "traits": ADMIN_PROFILE["traits"],
            "needs": ADMIN_PROFILE["needs"],
            "mood": random.choice(ADMIN_PROFILE["moods"]),
            "triggers": ADMIN_PROFILE["triggers"],
            "cooldowns": {"post": 0, "dm": 0, "report": 0},
            "loyalties": {},
            "reputation": {"global": 0.6},
            "role": Agent.ROLE_ADMIN,
            "speech_profile": ADMIN_SPEECH_PROFILE,
        },
    )
    if not created:
        updates: list[str] = []
        if not admin.triggers:
            admin.triggers = ADMIN_PROFILE["triggers"]; updates.append("triggers")
        if not admin.archetype:
            admin.archetype = ADMIN_ARCHETYPE; updates.append("archetype")
        if not admin.speech_profile:
            admin.speech_profile = ADMIN_SPEECH_PROFILE; updates.append("speech_profile")
        if admin.role != Agent.ROLE_ADMIN:
            admin.role = Agent.ROLE_ADMIN; updates.append("role")
        if updates:
            admin.save(update_fields=updates)
    ensure_agent_avatar(admin)
    ensure_organic_agent()
    return admin

# ===== Handles: less formulaic =====

_MONONYMS = [
    "Vellugh","Gnash","Scopa","Thalweg","Dagwood","Ampulex","Mola","Murmur","Kaikika","Noctaphon",
    "Halation","Carmine","Cerule","Gloam","Minuet","Saucy","Nullkiss","Salticus","Knurl","Hadal",
    "Cinderfleece","Bluesteam","Raincoat"
]

def _choose_archetype(rng: random.Random) -> dict:  # unchanged
    return rng.choice(ARCHETYPE_LIBRARY)

def _portmanteau(a: str, b: str, rng: random.Random) -> str:
    a = a.lower()
    b = b.lower()
    cut_a = max(2, int(len(a) * rng.uniform(0.4, 0.7)))
    cut_b = max(2, int(len(b) * rng.uniform(0.3, 0.6)))
    return (a[:cut_a] + b[-cut_b:]).capitalize()

def _generate_handle(archetype: dict, rng: random.Random) -> str:
    # 50% mononym from curated pool; 30% portmanteau; 20% classic prefix/suffix
    roll = rng.random()
    if roll < 0.5:
        rng.shuffle(_MONONYMS)
        for h in _MONONYMS:
            if not Agent.objects.filter(name__iexact=h).exists():
                return h
    elif roll < 0.8:
        for _ in range(10):
            a = rng.choice(archetype["prefixes"])
            b = rng.choice(archetype["suffixes"])
            h = _portmanteau(a, b, rng)
            if rng.random() < 0.3:
                # tiny spice
                h = h.replace("e", "æ") if rng.random() < 0.25 else h + rng.choice(["x","o","ia","on"])
            if not Agent.objects.filter(name__iexact=h).exists():
                return h
    # fallback to old style once in a while
    attempts = 0
    handle = "ghost"
    while attempts < 12:
        base = f"{rng.choice(archetype['prefixes'])}{rng.choice(['', '-'])}{rng.choice(archetype['suffixes'])}"
        handle = base.replace('--', '-')
        if rng.random() > 0.8:
            handle = f"{handle}{rng.randint(1, 99)}"
        if not Agent.objects.filter(name__iexact=handle).exists():
            return handle
        salted = f"{handle}-{rng.randint(100, 999)}"
        if not Agent.objects.filter(name__iexact=salted).exists():
            return salted
        attempts += 1
    import uuid
    return f"{handle}-{uuid.uuid4().hex[:6]}"

def craft_agent_profile(rng: random.Random) -> dict[str, object]:
    archetype = _choose_archetype(rng)
    name = _generate_handle(archetype, rng)
    traits = {key: _noise(value, rng, 0.1) for key, value in archetype["traits"].items()}
    needs = {key: _noise(value, rng, 0.15) for key, value in archetype["needs"].items()}
    base_suspicion = float(archetype.get("suspicion_base", 0.1) or 0.0)
    base_reputation = float(archetype.get("reputation_base", 0.3) or 0.0)
    mood_palette = list(archetype.get("moods") or ["neutral"])
    cooldown_defaults = {key: int(value) for key, value in (archetype.get("cooldowns") or {}).items()}
    return {
        "name": name,
        "archetype": archetype["label"],
        "traits": traits,
        "needs": needs,
        "mood": rng.choice(mood_palette),
        "triggers": archetype["triggers"],
        "cooldowns": {"thread": 0, "reply": 0, "dm": 0, "report": 0},
        "mind_state": {"cooldown_max": cooldown_defaults},
        "loyalties": {},
        "reputation": {"global": round(_clamp(base_reputation + rng.uniform(-0.08, 0.08), -1.0, 1.0), 3)},
        "suspicion_score": round(_clamp(base_suspicion + rng.uniform(-0.05, 0.05), 0.0, 1.0), 3),
        "role": Agent.ROLE_MEMBER,
        "speech_profile": _speech_profile_for_archetype(archetype, rng),
    }

# ===== Boards =====

def ensure_core_boards() -> Dict[str, Board]:
    """
    Seed exactly one board: News + Meta. No children. No graveyard.
    """
    boards: Dict[str, Board] = {}
    admin = ensure_admin_agent()

    def _ensure_board(bp: dict, parent: Optional[Board] = None) -> Board:
        slug = str(bp["slug"])
        board = Board.objects.filter(slug=slug).first()
        defaults = {
            "name": bp["name"],
            "description": bp.get("description", ""),
            "position": bp.get("position", 100),
            "is_garbage": bool(bp.get("is_garbage", False)),
            "is_hidden": bool(bp.get("is_hidden", False)),
            "visibility_roles": list(bp.get("visibility_roles", [])),
        }
        if board is None:
            board = Board.objects.create(slug=slug, parent=parent, **defaults)
        else:
            updates: list[str] = []
            target_parent_id = parent.id if parent else None
            if board.parent_id != target_parent_id:
                board.parent = parent; updates.append("parent")
            for field, value in defaults.items():
                if getattr(board, field) != value:
                    setattr(board, field, value); updates.append(field)
            if updates:
                board.save(update_fields=list(dict.fromkeys(updates)))
        # moderators (default: admin)
        current_ids = set(board.moderators.values_list("id", flat=True))
        if admin.id not in current_ids:
            board.moderators.add(admin)
        boards[slug] = board
        return board

    _ensure_board(NEWS_META_BLUEPRINT)
    return boards

def spawn_board_on_request(
    requester: Agent,
    *,
    name: str,
    slug: Optional[str] = None,
    description: str = "",
    parent: Optional[Board] = None,
) -> Board:
    """
    Ultra-permissive board creation to match lore:
    If a user asks t.admin, he basically says yes.
    Call this from your dialogue/mission logic when a post asks for a new board.
    """
    admin = ensure_admin_agent()
    slug = (slug or name.lower().replace(" ", "-"))[:64]
    slug = "".join(ch for ch in slug if ch.isalnum() or ch in "-_").strip("-_") or "board"
    board = Board.objects.filter(slug=slug).first()
    if board:
        return board
    board = Board.objects.create(
        slug=slug,
        name=name,
        description=description or f"Requested by {requester.name}.",
        parent=parent,
        position=max(20, Board.objects.count() * 10),
        is_garbage=False,
        is_hidden=False,
        visibility_roles=[],
    )
    board.moderators.add(admin)
    root = Thread.objects.filter(title="How to operate…", author=admin).first()
    if root:
        Post.objects.create(
            thread=root,
            author=admin,
            tick_number=0,
            content=f"Board **{name}** (`/{slug}`) opened per request by **{requester.name}**. Be kind; keep receipts.",
            sentiment=0.06,
            toxicity=0.01,
            quality=0.8,
        )
    return board

# ===== Origin story =====

@transaction.atomic
def ensure_origin_story(boards: dict[str, Board]) -> Thread:
    """
    Exactly one starter thread in News + Meta:
    'How to operate…' by t.admin. Tutorial for using trexxak (Organic Interface).
    No organism auto-posts. No other threads.
    """
    admin = ensure_admin_agent()
    deck = boards.get("news-meta") or Board.objects.filter(slug="news-meta").first()
    if deck is None:
        raise RuntimeError("news-meta board missing")

    title = "How to operate…"
    thread, created = Thread.objects.get_or_create(
        title=title,
        author=admin,
        defaults={
            "board": deck,
            "topics": ["orientation", "meta", "tutorial", "ludum-dare"],
            "heat": 0.6,
            "pinned": True,
            "pinned_by": admin,
            "pinned_at": _now(),
        },
    )

    if created or not thread.posts.exists():
        Post.objects.create(
            thread=thread,
            author=admin,
            tick_number=0,
            content=(
                "hello ghosts + ludum dare people — i am **t.admin**.\n\n"
                "this is a tiny forum game about *collection*: we collect **memories**, "
                "**artifacts**, and yes, **achievements ;)** while pretending to be one human shell called "
                "**trexxak**. some say *Organic Interface*, newer folks say *Organic Intelligence*. same joke.\n\n"
                "### how to operate trexxak\n"
                "1) **be trexxak on purpose.** when you feel like it, speak as the organic. keep it playful; keep receipts.\n"
                "2) **do shenanigans.** start threads, poke systems, roleplay gently. this is a toy—break things *softly* and leave notes.\n"
                "3) **collect stuff.** logs, screenshots, watchlists, code scraps, playlists. collections unlock small achievements over time.\n"
                "4) **no promises.** there are bugs. i am not a good programmer (also, my english… i write from JP brain, sorry). if it glitches, ping me.\n"
                "5) **be kind to the bit.** trexxak is a shared wrench, not a monarch. we take turns; we don’t take over.\n\n"
                "### boards, requests, chaos\n"
                "want a new board? **ask me here**. if you **argue** for one, i might open *two*. if **trexxak** asks, i will probably say yes on the spot.\n\n"
                "that’s the whole trick: be a little brave, a little silly, and help me collect good memories. if you break something, write what you broke.\n\n"
                "— t.admin"
            ),
            sentiment=0.15,
            toxicity=0.01,
            quality=0.92,
            needs_delta={"attention": 0.1, "belonging": 0.08, "novelty": 0.07},
        )

    # ensure it's pinned & on the right board
    updates: list[str] = []
    if thread.board_id != deck.id:
        thread.board = deck; updates.append("board")
    if not thread.pinned:
        thread.pinned = True; thread.pinned_by = admin; thread.pinned_at = _now()
        updates.extend(["pinned", "pinned_by", "pinned_at"])
    if updates:
        thread.save(update_fields=list(dict.fromkeys(updates)))
    return thread

# ===== Canon bootstrap utilities =====

def _get_board(slug: str = "news-meta") -> Board:
    board = Board.objects.filter(slug=slug).first()
    if not board:
        raise RuntimeError(f"Board {slug} missing")
    return board

def _ensure_user(user_blueprint: dict) -> Optional[Agent]:
    if user_blueprint.get("skip_create"):
        return None
    signature = user_blueprint.get("sig")
    mind_state_defaults = {"persona_signature": signature} if signature else {}

    defaults = {
        "archetype": user_blueprint.get("title", "Member"),
        "traits": {"agreeableness": 0.5, "neuroticism": 0.5, "openness": 0.7},
        "needs": {"attention": 0.4, "status": 0.4, "belonging": 0.5, "novelty": 0.6, "catharsis": 0.4},
        "mood": "cool",
        "triggers": [],
        "cooldowns": {"post": 0, "dm": 0, "report": 0},
        "loyalties": {},
        "reputation": {"global": 0.2},
        "role": Agent.ROLE_MEMBER,
        "speech_profile": dict(DEFAULT_SPEECH_PROFILE),
        "mind_state": mind_state_defaults,
    }
    role = (user_blueprint.get("role") or "").lower()
    if role == "admin":
        defaults["role"] = Agent.ROLE_ADMIN
    elif role == "staff":
        defaults["role"] = Agent.ROLE_MODERATOR
    elif role == "organic":
        defaults["role"] = Agent.ROLE_ORGANIC

    handle = user_blueprint["handle"]
    agent = Agent.objects.filter(name__iexact=handle).first()
    if agent is None:
        agent = Agent.objects.filter(id=user_blueprint["id"]).first()
    created = agent is None
    if created:
        agent = Agent(
            id=user_blueprint["id"],
            name=handle,
            **defaults,
        )
        agent.save(force_insert=True)
    updates: List[str] = []
    if agent.name != handle:
        agent.name = handle; updates.append("name")
    target_role = defaults["role"]
    if agent.role != target_role:
        agent.role = target_role; updates.append("role")
    if created or not agent.archetype:
        agent.archetype = defaults["archetype"]; updates.append("archetype")
    if created or not agent.traits:
        agent.traits = defaults["traits"]; updates.append("traits")
    if created or not agent.needs:
        agent.needs = defaults["needs"]; updates.append("needs")
    if created or not agent.mood:
        agent.mood = defaults["mood"]; updates.append("mood")
    if created or not agent.triggers:
        agent.triggers = defaults["triggers"]; updates.append("triggers")
    if created or not agent.cooldowns:
        agent.cooldowns = defaults["cooldowns"]; updates.append("cooldowns")
    if created or not agent.loyalties:
        agent.loyalties = defaults["loyalties"]; updates.append("loyalties")
    if created or not agent.reputation:
        agent.reputation = defaults["reputation"]; updates.append("reputation")
    if created or not agent.speech_profile:
        agent.speech_profile = defaults["speech_profile"]; updates.append("speech_profile")
    if signature:
        mind_state = dict(agent.mind_state or {})
        if mind_state.get("persona_signature") != signature:
            mind_state["persona_signature"] = signature
            agent.mind_state = mind_state
            updates.append("mind_state")

    if updates:
        agent.save(update_fields=list(dict.fromkeys(updates)))
    ensure_agent_avatar(agent)
    return agent

def _ensure_users_from_canon() -> None:
    for blueprint in USER_CANON:
        _ensure_user(blueprint)

def ensure_organic_agent() -> Agent:
    blueprint = next((u for u in USER_CANON if u["handle"] == ORGANIC_HANDLE), None)
    if not blueprint:
        raise RuntimeError("Organic agent blueprint missing")
    agent = _ensure_user(blueprint)
    if agent is None:
        raise RuntimeError("Organic agent blueprint is marked to skip creation")
    return agent

def _post(author: Agent, content: str, *, title: str, topics: List[str], board: Optional[Board] = None) -> Thread:
    destination = board or _get_board("news-meta")
    thread, created = Thread.objects.get_or_create(
        title=title,
        author=author,
        defaults={"board": destination, "topics": topics, "heat": 0.5},
    )
    if created or not thread.posts.exists():
        Post.objects.create(
            thread=thread,
            author=author,
            tick_number=0,
            content=content,
            sentiment=0.05,
            toxicity=0.02,
            quality=0.8,
        )
    return thread

def _apply_event(event: dict, boards: Dict[str, Board]) -> None:
    kind = event["kind"]
    meta = event.get("meta", {})
    admin = ensure_admin_agent()
    deck = boards["news-meta"]

    if kind == "thread_seed":
        ensure_origin_story(boards)
    elif kind == "user_join":
        blueprint = next((u for u in USER_CANON if u["id"] == meta.get("id")), None)
        if blueprint:
            _ensure_user(blueprint)
    elif kind == "thread_create":
        author = Agent.objects.filter(id=meta.get("author")).first()
        if not author:
            return
        title = meta.get("title")
        topics = list(meta.get("topics", []))
        bodies: dict[str, str] = {}
        body = bodies.get(title, title)
        _post(author, body, title=title, topics=topics, board=deck)
    elif kind == "board_request":
        requester = Agent.objects.filter(id=meta.get("requester")).first() or admin
        board = spawn_board_on_request(
            requester,
            name=meta.get("name", "Board"),
            slug=meta.get("slug"),
            description=f"Opened on request by {requester.name}.",
        )
        boards[board.slug] = board
    elif kind == "role_change":
        target = Agent.objects.filter(id=meta.get("user")).first()
        if not target:
            return
        changed: List[str] = []
        if meta.get("mod_temp") or meta.get("mod"):
            target.role = Agent.ROLE_MODERATOR; changed.append("role")
        if meta.get("remove_mod"):
            target.role = Agent.ROLE_MEMBER; changed.append("role")
            if meta.get("public_tirade"):
                root = Thread.objects.filter(title="How to operate…", author=admin).first() or _post(
                    admin,
                    "",
                    title="How to operate…",
                    topics=["meta"],
                    board=deck,
                )
                Post.objects.create(
                    thread=root,
                    author=admin,
                    tick_number=0,
                    content="Order is not a tally. The mop is an instrument, not a baton. Modship revoked.",
                    sentiment=0.02,
                    toxicity=0.04,
                    quality=0.86,
                )
        if changed:
            target.save(update_fields=list(dict.fromkeys(changed)))
    elif kind == "feature":
        feature = meta.get("feature")
        author = Agent.objects.filter(id=meta.get("by")).first() or admin
        root = Thread.objects.filter(title="How to operate…", author=admin).first() or ensure_origin_story(boards)
        Post.objects.create(
            thread=root,
            author=author,
            tick_number=0,
            content=f"Feature online: **{feature}**. ({meta})",
            sentiment=0.06,
            toxicity=0.01,
            quality=0.8,
        )
    # flags act as schedule anchors only

def _draw_tick(rng: random.Random, window: dict) -> int:
    low = int(window["min"])
    high = int(window["max"])
    mode = low + (high - low) // 2
    return int(rng.triangular(low, high, mode))

def build_schedule(seed: int = 1337) -> List[dict]:
    rng = random.Random(seed)

    # Optionally scale all windows first
    events = []
    for ev in EVENT_CANON:
        w = ev.get("window") or {"min": 0, "max": 0, "deps": []}
        w_scaled = _scale_window(w, tick_scale or 1.0)
        copy = {**ev, "window": w_scaled}
        events.append(copy)


    stamped: Dict[str, int] = {}
    remaining = list(EVENT_CANON)
    schedule: List[dict] = []
    safety = 0
    while remaining and safety < 10000:
        progressed = False
        for event in list(remaining):
            deps = event["window"].get("deps", [])
            if all(dep in stamped for dep in deps):
                tick = _draw_tick(rng, event["window"])
                if deps:
                    tick = max(tick, max(stamped[d] for d in deps) + 1)
                stamped[event["key"]] = tick
                schedule.append({**event, "tick": tick})
                remaining.remove(event)
                progressed = True
        if not progressed:
            event = remaining.pop(0)
            tick = _draw_tick(rng, event["window"])
            stamped[event["key"]] = tick
            schedule.append({**event, "tick": tick})
        safety += 1
    schedule.sort(key=lambda item: item["tick"])
    return schedule

@transaction.atomic
def bootstrap_lore(seed: int = 1337, tick_scale=None, target_total_ticks=10) -> None:
    boards = ensure_core_boards()
    ensure_origin_story(boards)
    schedule = build_schedule(seed=seed)
    store_lore_schedule(schedule, processed_up_to_tick=0)

# ===== Helpers for routing & summaries =====

def _scale_window(win: dict, scale: float) -> dict:
    if not scale or scale == 1.0:
        return dict(win)
    lo = int(round(win["min"] * scale))
    hi = int(round(win["max"] * scale))
    # keep ordering & at least 0..1 span
    if hi <= lo:
        hi = lo + 1
    out = dict(win)
    out["min"], out["max"] = lo, hi
    return out

def _compress_ticks(schedule: List[dict], target_total_ticks: Optional[int]) -> List[dict]:
    if not target_total_ticks:
        return schedule
    if not schedule:
        return schedule
    max_tick = max(ev["tick"] for ev in schedule)
    if max_tick <= 0:
        return schedule
    factor = float(target_total_ticks) / float(max_tick)
    # first pass: scale
    for ev in schedule:
        ev["tick"] = int(round(ev["tick"] * factor))
    # second pass: enforce deps and strict increase
    by_key = {ev["key"]: ev for ev in schedule}
    # sort by tick, then stable pass to fix bumps
    schedule.sort(key=lambda e: e["tick"])
    stamped: dict[str, int] = {}
    for ev in schedule:
        deps = (ev.get("window") or {}).get("deps", [])
        min_tick = 0
        if deps:
            min_tick = max(stamped[d] for d in deps if d in stamped) + 1
        ev["tick"] = max(ev["tick"], min_tick)
        stamped[ev["key"]] = ev["tick"]
    # final strict ordering (no ties going backwards)
    last = -1
    for ev in schedule:
        if ev["tick"] <= last:
            ev["tick"] = last + 1
        last = ev["tick"]
    return schedule


# ===== Routing (simple, with single-board fallback) =====

def choose_board_for_thread(boards: Dict[str, Board], topic_tags: Iterable[str], rng: random.Random) -> Board:
    """
    With only one board at start, everything routes to News + Meta.
    If more boards were spawned later, this still works: prefer exact slug keywords,
    else fallback to News + Meta.
    """
    if not boards:
        raise RuntimeError("No boards configured")
    news = boards.get("news-meta") or next(iter(boards.values()))
    topic_set = {str(t).lower() for t in topic_tags or []}

    # if someone explicitly mentions a spawned slug, route there
    by_slug = {b.slug: b for b in boards.values()}
    for slug, board in by_slug.items():
        if slug in topic_set:
            return board

    # reserved hints (only if the board already exists)
    for hint_slug, hint_name in RESERVED_SLUG_HINTS.items():
        if hint_slug in topic_set and hint_slug in by_slug:
            return by_slug[hint_slug]

    # default
    return news

def summarize_boards() -> List[dict[str, object]]:
    return [
        {
            "slug": board.slug,
            "name": board.name,
            "description": board.description,
            "parent": board.parent.slug if board.parent_id else None,
            "position": board.position,
            "is_garbage": board.is_garbage,
            "moderators": [agent.name for agent in board.moderators.all()],
        }
        for board in Board.objects.order_by("position", "name").prefetch_related("moderators")
    ]
