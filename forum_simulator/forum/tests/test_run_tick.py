from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from forum.models import Agent, Board, OracleDraw, Thread, TickLog


class RunTickCommandTests(TestCase):
    @mock.patch("forum.management.commands.run_tick._drain_queue_for")
    @mock.patch("forum.management.commands.run_tick.enqueue_generation_task", return_value=SimpleNamespace(id=1))
    @mock.patch("forum.management.commands.run_tick.choose_board_for_thread")
    @mock.patch("forum.management.commands.run_tick.tick_control.record_tick_run")
    @mock.patch("forum.management.commands.run_tick.tick_control.describe_state", return_value={})
    @mock.patch("forum.management.commands.run_tick.build_energy_profile")
    @mock.patch("forum.management.commands.run_tick.allocate_actions")
    @mock.patch("forum.management.commands.run_tick.agent_state.progress_agents", return_value=[{"agent": "Aurora"}])
    @mock.patch("forum.management.commands.run_tick.ensure_core_boards", return_value={})
    @mock.patch("forum.management.commands.run_tick.ensure_origin_story")
    @mock.patch("forum.management.commands.run_tick.process_lore_events", return_value=[])
    @mock.patch("forum.management.commands.run_tick._ensure_users_from_canon")
    @mock.patch("forum.management.commands.run_tick.activity_service.session_snapshot", return_value={})
    @mock.patch(
        "forum.management.commands.run_tick.activity_service.apply_activity_scaling",
        side_effect=lambda allocation, snapshot: allocation,
    )
    @mock.patch("forum.management.commands.run_tick.config_service.get_int", return_value=0)
    def test_run_tick_persists_seed_and_decision_trace(
        self,
        get_int_mock,
        apply_scaling_mock,
        session_snapshot_mock,
        ensure_users_mock,
        process_lore_mock,
        ensure_origin_mock,
        ensure_boards_mock,
        progress_agents_mock,
        allocate_actions_mock,
        build_energy_profile_mock,
        describe_state_mock,
        record_tick_run_mock,
        choose_board_mock,
        enqueue_task_mock,
        drain_queue_mock,
    ) -> None:
        Agent.objects.create(
            name="Aurora",
            archetype="Hothead",
            traits={},
            needs={},
            cooldowns={},
        )
        Agent.objects.create(
            name="Beacon",
            archetype="Helper",
            traits={},
            needs={},
            cooldowns={},
        )

        board = Board.objects.create(name="Commons", slug="commons", position=1)
        choose_board_mock.return_value = board
        ensure_boards_mock.return_value = {board.slug: board}

        agent = Agent.objects.first()
        for index in range(4):
            Thread.objects.create(title=f"seed-{index}", author=agent, board=board)

        build_energy_profile_mock.return_value = SimpleNamespace(rolls=[2, 4], energy=5, energy_prime=7)

        class DummyAllocation:
            def __init__(self) -> None:
                self.registrations = 0
                self.threads = 0
                self.replies = 0
                self.private_messages = 0
                self.moderation_events = 0
                self.omen = False
                self.seance = False
                self.notes: list[str] = []
                self.omen_details = None
                self.seance_details = None

            def as_dict(self) -> dict[str, int]:
                return {
                    "regs": self.registrations,
                    "threads": self.threads,
                    "replies": self.replies,
                    "pms": self.private_messages,
                    "mods": self.moderation_events,
                }

            def special_flags(self) -> dict[str, object]:
                return {"omen": self.omen, "seance": self.seance}

        allocate_actions_mock.return_value = DummyAllocation()

        call_command("run_tick", seed=123, origin="unit-test", force=True)

        tick = TickLog.objects.get(tick_number=1)
        self.assertEqual(tick.seed, 123)
        self.assertTrue(isinstance(tick.decision_trace, list))
        self.assertGreaterEqual(len(tick.decision_trace), 2)
        self.assertIn("allocation", tick.decision_trace[-1])
        self.assertTrue(isinstance(tick.config_snapshot, dict))

        draw = OracleDraw.objects.get(tick_number=1)
        self.assertEqual(draw.seed, 123)
        self.assertEqual(draw.energy_prime, 7)

        record_tick_run_mock.assert_called_once_with(1, origin="unit-test")
        describe_state_mock.assert_called_once()
        progress_agents_mock.assert_called_once()
        allocate_actions_mock.assert_called_once()
        build_energy_profile_mock.assert_called_once()
        get_int_mock.assert_called()
        enqueue_task_mock.assert_not_called()
        drain_queue_mock.assert_called()
