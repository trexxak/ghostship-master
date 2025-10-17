from __future__ import annotations

import random
from unittest import mock

from django.test import TestCase

from forum.models import Agent
from forum.services import agent_state


class AgentStateTests(TestCase):
    def setUp(self) -> None:
        self.agent = Agent.objects.create(
            name="Aurora",
            archetype="Hothead",
            traits={},
            needs={"attention": 0.6, "status": 0.3},
            cooldowns={"reply": 2},
            mind_state={"action_bias": {"reply": 0.4}},
            suspicion_score=0.5,
            reputation={"global": 0.1},
        )

    @mock.patch("forum.services.agent_state.sim_config.action_bias", return_value={"reply": {"needs": {"attention": 1.0}}})
    @mock.patch(
        "forum.services.agent_state.sim_config.reputation_config",
        return_value={"floor": -1.0, "ceiling": 1.0, "decay": 0.05, "boost_per_report": 0.1},
    )
    @mock.patch(
        "forum.services.agent_state.sim_config.suspicion_config",
        return_value={"floor": 0.0, "ceiling": 1.0, "decay": 0.1, "report_relief": 0.2, "dm_penalty": 0.05},
    )
    @mock.patch(
        "forum.services.agent_state.sim_config.mood_config",
        return_value={
            "suspicion_bias": 0.1,
            "bands": [
                {"label": "tired", "threshold": 0.3},
                {"label": "steady", "threshold": 0.6},
                {"label": "bright", "threshold": 1.0},
            ],
        },
    )
    @mock.patch(
        "forum.services.agent_state.sim_config.needs_config",
        return_value={
            "floor": 0.1,
            "ceiling": 0.95,
            "drift_jitter": 0.0,
            "baseline": {"attention": 0.5, "status": 0.4},
            "drift": {"attention": -0.05, "status": -0.02},
        },
    )
    def test_progress_agents_updates_needs_and_cooldowns(self, *_mocks) -> None:
        rng = random.Random(3)
        updates = agent_state.progress_agents(5, rng)
        self.assertEqual(len(updates), 1)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.cooldowns["reply"], 1)
        self.assertLess(self.agent.suspicion_score, 0.5)
        self.assertIn(self.agent.mood, {"tired", "steady", "bright"})
        self.assertIn("action_bias", self.agent.mind_state)
        self.assertAlmostEqual(self.agent.reputation["global"], 0.05, places=3)

    @mock.patch("forum.services.agent_state.sim_config.cooldowns", return_value={"report": 3})
    @mock.patch(
        "forum.services.agent_state.sim_config.reputation_config",
        return_value={"floor": -1.0, "ceiling": 1.0, "boost_per_report": 0.2},
    )
    @mock.patch(
        "forum.services.agent_state.sim_config.suspicion_config",
        return_value={"floor": 0.0, "ceiling": 1.0, "report_relief": 0.3, "dm_penalty": 0.05},
    )
    def test_register_action_adjusts_cooldowns_and_scores(self, *_mocks) -> None:
        record = agent_state.register_action(
            self.agent,
            "report",
            tick_number=2,
            context={"note": "spam"},
        )
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.cooldowns["report"], 3)
        self.assertAlmostEqual(self.agent.suspicion_score, 0.2, places=3)
        self.assertAlmostEqual(self.agent.reputation["global"], 0.3, places=3)
        self.assertEqual(record["action"], "report")
        self.assertEqual(record["tick"], 2)

    def test_weighted_choice_respects_disallow(self) -> None:
        other = Agent.objects.create(
            name="Beacon",
            archetype="Helper",
            traits={},
            needs={},
            cooldowns={},
            mind_state={"action_bias": {"reply": 0.9}},
        )
        rng = random.Random(7)
        chosen = agent_state.weighted_choice([self.agent, other], "reply", rng, disallow=[self.agent.id])
        self.assertEqual(chosen.id, other.id)

    def test_weighted_choice_raises_for_empty_pool(self) -> None:
        rng = random.Random(1)
        with self.assertRaises(ValueError):
            agent_state.weighted_choice([], "reply", rng)
