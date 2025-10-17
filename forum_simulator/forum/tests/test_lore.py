from __future__ import annotations

from django.test import TestCase

from forum.lore import USER_CANON, store_lore_schedule
from forum.models import Agent, LoreEvent


class LoreScheduleTests(TestCase):
    def test_store_lore_schedule_bootstrap_creates_initial_ghosts(self) -> None:
        schedule = [
            {"key": "boot_thread", "kind": "thread_seed", "tick": 0, "window": {"min": 0, "max": 0}},
            {"key": "u1_join", "kind": "user_join", "tick": 0, "window": {"min": 0, "max": 0}, "meta": {"id": 1}},
            {"key": "u2_join", "kind": "user_join", "tick": 0, "window": {"min": 0, "max": 0}, "meta": {"id": 2}},
            {"key": "u3_join", "kind": "user_join", "tick": 0, "window": {"min": 0, "max": 0}, "meta": {"id": 3}},
            {"key": "u4_join", "kind": "user_join", "tick": 0, "window": {"min": 0, "max": 0}, "meta": {"id": 4}},
            {"key": "u5_join", "kind": "user_join", "tick": 0, "window": {"min": 0, "max": 0}, "meta": {"id": 5}},
            {"key": "u6_join", "kind": "user_join", "tick": 0, "window": {"min": 0, "max": 0}, "meta": {"id": 6}},
        ]

        store_lore_schedule(schedule, processed_up_to_tick=0)

        expected_handles = {entry["handle"] for entry in USER_CANON[:6]}
        created_handles = {
            agent.name
            for agent in Agent.objects.filter(id__in=[entry["id"] for entry in USER_CANON[:6]])
        }
        self.assertSetEqual(created_handles, expected_handles)
        self.assertEqual(LoreEvent.objects.count(), len(schedule))
