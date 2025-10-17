from __future__ import annotations

import random
from typing import Dict, Iterable, List, Optional, Sequence

from forum.models import Agent

from . import sim_config
from ._safe import safe_save


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _mood_label(score: float, bands: Sequence[dict], fallback: str = "neutral") -> str:
    sorted_bands = sorted(
        (band for band in bands if isinstance(band, dict) and "threshold" in band),
        key=lambda item: float(item.get("threshold", 0)),
    )
    for band in sorted_bands:
        threshold = float(band.get("threshold", 1.0))
        label = str(band.get("label") or fallback)
        if score <= threshold:
            return label
    return fallback


def _cooldown_length(action: str) -> int:
    try:
        value = sim_config.cooldowns().get(action)
        return int(value) if value is not None else 0
    except Exception:
        return 0


def bias_for(agent: Agent, action: str) -> float:
    mind_state = agent.mind_state or {}
    action_bias = mind_state.get("action_bias") or {}
    try:
        return float(action_bias.get(action, 0.0))
    except (TypeError, ValueError):
        return 0.0


def progress_agents(tick_number: int, rng: random.Random) -> List[dict[str, object]]:
    """Advance agent needs, mood, suspicion, and action bias for the tick."""

    needs_cfg = sim_config.needs_config()
    baseline = dict(needs_cfg.get("baseline") or {})
    drift = dict(needs_cfg.get("drift") or {})
    drift_jitter = float(needs_cfg.get("drift_jitter", 0.0))
    needs_floor = float(needs_cfg.get("floor", 0.0))
    needs_ceiling = float(needs_cfg.get("ceiling", 1.0))

    mood_cfg = sim_config.mood_config()
    mood_bands = list(mood_cfg.get("bands") or [])
    suspicion_bias = float(mood_cfg.get("suspicion_bias", 0.0))

    suspicion_cfg = sim_config.suspicion_config()
    suspicion_floor = float(suspicion_cfg.get("floor", 0.0))
    suspicion_ceiling = float(suspicion_cfg.get("ceiling", 1.0))
    suspicion_decay = float(suspicion_cfg.get("decay", 0.0))

    reputation_cfg = sim_config.reputation_config()
    reputation_floor = float(reputation_cfg.get("floor", -1.0))
    reputation_ceiling = float(reputation_cfg.get("ceiling", 1.0))
    reputation_decay = float(reputation_cfg.get("decay", 0.0))

    bias_cfg = sim_config.action_bias()

    updates: List[dict[str, object]] = []
    for agent in Agent.objects.all().order_by("id"):
        needs_state = {key: float(value) for key, value in baseline.items()}
        if isinstance(agent.needs, dict):
            for key, value in agent.needs.items():
                try:
                    needs_state[key] = float(value)
                except (TypeError, ValueError):
                    continue

        deltas: Dict[str, float] = {}
        for need, base_value in needs_state.items():
            drift_delta = float(drift.get(need, 0.0))
            if drift_jitter:
                drift_delta += rng.uniform(-drift_jitter, drift_jitter)
            new_value = _clamp(base_value + drift_delta, needs_floor, needs_ceiling)
            deltas[need] = round(new_value - base_value, 3)
            needs_state[need] = round(new_value, 3)

        cooldowns = dict(agent.cooldowns or {})
        for key in list(cooldowns.keys()):
            try:
                cooldowns[key] = max(0, int(cooldowns[key]) - 1)
            except Exception:
                cooldowns[key] = 0

        suspicion = float(getattr(agent, "suspicion_score", 0.0) or 0.0)
        suspicion = _clamp(suspicion - suspicion_decay, suspicion_floor, suspicion_ceiling)

        reputation = dict(agent.reputation or {})
        rep_value = float(reputation.get("global", 0.0) or 0.0)
        rep_value = _clamp(rep_value - reputation_decay, reputation_floor, reputation_ceiling)
        reputation["global"] = round(rep_value, 3)

        mood_score = sum(needs_state.values()) / max(len(needs_state), 1)
        mood_score -= suspicion * suspicion_bias
        mood_label = _mood_label(mood_score, mood_bands, fallback=str(agent.mood or "neutral"))

        action_bias: Dict[str, float] = {}
        for action, rule in bias_cfg.items():
            weights = rule.get("needs") or {}
            score = 0.0
            for need_key, weight in weights.items():
                try:
                    score += needs_state.get(need_key, baseline.get(need_key, 0.5)) * float(weight)
                except (TypeError, ValueError):
                    continue
            if action == "report":
                score += suspicion * float(rule.get("suspicion_weight", 0.0))
            cooldown_penalty = float(rule.get("cooldown_penalty", 0.0))
            if cooldowns.get(action):
                score *= max(0.05, 1.0 - cooldown_penalty)
            action_bias[action] = round(score, 3)

        mind_state = dict(agent.mind_state or {})
        mind_state["action_bias"] = action_bias
        mind_state["last_drift_tick"] = tick_number

        agent.needs = needs_state
        agent.cooldowns = cooldowns
        agent.mood = mood_label
        agent.suspicion_score = round(suspicion, 3)
        agent.reputation = reputation
        agent.mind_state = mind_state
        safe_save(agent, ["needs", "cooldowns", "mood", "suspicion_score", "reputation", "mind_state", "updated_at"])

        updates.append(
            {
                "agent": agent.name,
                "mood": mood_label,
                "needs": needs_state,
                "deltas": deltas,
                "suspicion": agent.suspicion_score,
                "reputation": reputation.get("global"),
                "cooldowns": cooldowns,
                "action_bias": action_bias,
            }
        )
    return updates


def weighted_choice(
    agents: Sequence[Agent],
    action: str,
    rng: random.Random,
    *,
    disallow: Optional[Iterable[int]] = None,
) -> Agent:
    """Pick an agent weighted by their current action bias."""

    disallow_ids = set(disallow or [])
    pool = [agent for agent in agents if agent.id not in disallow_ids]
    if not pool:
        raise ValueError("No agents available for weighted choice")

    weights: List[float] = []
    for agent in pool:
        weight = bias_for(agent, action)
        if weight <= 0:
            weight = 0.05
        weights.append(weight)

    total = sum(weights)
    if total <= 0:
        return rng.choice(pool)

    pivot = rng.uniform(0.0, total)
    cumulative = 0.0
    for agent, weight in zip(pool, weights):
        cumulative += weight
        if pivot <= cumulative:
            return agent
    return pool[-1]


def register_action(
    agent: Agent,
    action: str,
    *,
    tick_number: int,
    context: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    """Update cooldowns/suspicion for an executed action and emit a trace entry."""

    cooldowns = dict(agent.cooldowns or {})
    cooldown_value = _cooldown_length(action)
    if cooldown_value:
        cooldowns[action] = cooldown_value

    suspicion_cfg = sim_config.suspicion_config()
    suspicion_floor = float(suspicion_cfg.get("floor", 0.0))
    suspicion_ceiling = float(suspicion_cfg.get("ceiling", 1.0))
    suspicion = float(getattr(agent, "suspicion_score", 0.0) or 0.0)
    if action == "report":
        suspicion -= float(suspicion_cfg.get("report_relief", 0.0))
    elif action in {"dm", "private_message"}:
        suspicion += float(suspicion_cfg.get("dm_penalty", 0.0))
    suspicion = _clamp(suspicion, suspicion_floor, suspicion_ceiling)
    agent.suspicion_score = round(suspicion, 3)

    reputation = dict(agent.reputation or {})
    reputation_cfg = sim_config.reputation_config()
    if action == "report":
        boost = float(reputation_cfg.get("boost_per_report", 0.0))
        if boost:
            rep_value = float(reputation.get("global", 0.0) or 0.0)
            rep_value = _clamp(rep_value + boost, float(reputation_cfg.get("floor", -1.0)), float(reputation_cfg.get("ceiling", 1.0)))
            reputation["global"] = round(rep_value, 3)
    agent.reputation = reputation

    mind_state = dict(agent.mind_state or {})
    action_log = list(mind_state.get("action_log") or [])
    record = {
        "action": action,
        "tick": tick_number,
        "context": context or {},
    }
    action_log.append(record)
    mind_state["action_log"] = action_log[-12:]
    mind_state["last_action"] = record
    mind_state.setdefault("action_bias", {})

    agent.cooldowns = cooldowns
    agent.mind_state = mind_state
    safe_save(agent, ["cooldowns", "suspicion_score", "reputation", "mind_state", "updated_at"])

    return {
        "agent": agent.name,
        "action": action,
        "tick": tick_number,
        "context": context or {},
        "bias": bias_for(agent, action),
        "cooldown": cooldowns.get(action, 0),
        "suspicion": agent.suspicion_score,
    }
