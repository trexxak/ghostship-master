from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from forum.models import Agent, Board, GenerationTask, OracleDraw, PrivateMessage, Thread, TickLog


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
    @mock.patch("forum.lore._ensure_users_from_canon")
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
        self.assertEqual(enqueue_task_mock.call_count, 2)
        drain_queue_mock.assert_called()

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
    @mock.patch("forum.lore._ensure_users_from_canon")
    @mock.patch("forum.management.commands.run_tick.activity_service.session_snapshot")
    @mock.patch(
        "forum.management.commands.run_tick.activity_service.apply_activity_scaling",
        side_effect=lambda allocation, snapshot: allocation,
    )
    @mock.patch("forum.management.commands.run_tick.config_service.get_int")
    def test_dm_quota_survives_task_limit(
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
        def fake_get_int(key, default=0):
            if key == "AI_TASKS_PER_TICK":
                return 1
            return default

        get_int_mock.side_effect = fake_get_int

        admin = Agent.objects.create(name="t.admin", archetype="Admin", role=Agent.ROLE_ADMIN)
        greeter = Agent.objects.create(name="Aurora", archetype="Scout", role=Agent.ROLE_MEMBER)
        partner = Agent.objects.create(name="Beacon", archetype="Helper", role=Agent.ROLE_MEMBER)

        board = Board.objects.create(name="Commons", slug="commons", position=1)
        Thread.objects.create(title="Existing thread", author=greeter, board=board)

        choose_board_mock.return_value = board
        ensure_boards_mock.return_value = {board.slug: board}

        build_energy_profile_mock.return_value = SimpleNamespace(rolls=[1, 2], energy=4, energy_prime=6)
        session_snapshot_mock.return_value = SimpleNamespace(total=3, tier="low", factor=1.0)

        class TaskLimitedAllocation:
            def __init__(self) -> None:
                self.registrations = 0
                self.threads = 3
                self.replies = 2
                self.private_messages = 2
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

        allocate_actions_mock.return_value = TaskLimitedAllocation()

        drain_queue_mock.return_value = None

        call_command("run_tick", seed=321, origin="unit-test", force=True)

        dm_calls = [
            call_info
            for call_info in enqueue_task_mock.call_args_list
            if call_info.kwargs.get("task_type") == GenerationTask.TYPE_DM
        ]
        self.assertGreaterEqual(len(dm_calls), 1)

        tick = TickLog.objects.get(tick_number=1)
        alloc_entry = tick.decision_trace[-1]["allocation"]
        self.assertGreaterEqual(alloc_entry.get("pms", 0), 1)

        record_tick_run_mock.assert_called_once_with(1, origin="unit-test")

    @mock.patch("forum.management.commands.run_tick._drain_queue_for")
    @mock.patch("forum.management.commands.run_tick.tick_control.record_tick_run")
    @mock.patch("forum.management.commands.run_tick.tick_control.describe_state", return_value={})
    @mock.patch("forum.management.commands.run_tick.build_energy_profile")
    @mock.patch("forum.management.commands.run_tick.allocate_actions")
    @mock.patch("forum.management.commands.run_tick.agent_state.progress_agents", return_value=[{"agent": "Aurora"}])
    @mock.patch("forum.management.commands.run_tick.ensure_core_boards", return_value={})
    @mock.patch("forum.management.commands.run_tick.ensure_origin_story")
    @mock.patch("forum.management.commands.run_tick.process_lore_events")
    @mock.patch("forum.lore._ensure_users_from_canon")
    @mock.patch("forum.management.commands.run_tick.activity_service.session_snapshot")
    @mock.patch(
        "forum.management.commands.run_tick.activity_service.apply_activity_scaling",
        side_effect=lambda allocation, snapshot: allocation,
    )
    @mock.patch("forum.management.commands.run_tick.config_service.get_int", return_value=10)
    def test_trexxak_dm_tasks_create_private_messages(
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
        drain_queue_mock,
    ) -> None:
        admin = Agent.objects.create(name="t.admin", archetype="Admin", role=Agent.ROLE_ADMIN)
        greeter = Agent.objects.create(name="Aurora", archetype="Scout", role=Agent.ROLE_MEMBER)
        partner = Agent.objects.create(name="Beacon", archetype="Helper", role=Agent.ROLE_MEMBER)
        newcomer = Agent.objects.create(name="Comet", archetype="New", role=Agent.ROLE_MEMBER)
        trexxak = Agent.objects.create(name="trexxak", archetype="Interface", role=Agent.ROLE_ORGANIC)

        PrivateMessage.objects.create(sender=partner, recipient=greeter, content="reply soon")
        PrivateMessage.objects.create(sender=greeter, recipient=admin, content="admin ping")

        board = Board.objects.create(name="Commons", slug="commons", position=1)
        Thread.objects.create(title="Existing thread", author=greeter, board=board)

        build_energy_profile_mock.return_value = SimpleNamespace(rolls=[1, 2], energy=5, energy_prime=7)

        class DMHeavyAllocation:
            def __init__(self) -> None:
                self.registrations = 0
                self.threads = 0
                self.replies = 0
                self.private_messages = 6
                self.moderation_events = 0
                self.omen = False
                self.seance = False
                self.notes: list[str] = []
                self.omen_details = {}
                self.seance_details = {}

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

        allocate_actions_mock.return_value = DMHeavyAllocation()

        process_lore_mock.return_value = [
            {"kind": "user_join", "meta": {"id": newcomer.id}},
        ]
        session_snapshot_mock.return_value = SimpleNamespace(total=5, tier="mid", factor=1.0)

        processed_counts = {"dm": 0}

        def drain_stub(kind, *, thread=None, max_loops=6, batch=8):
            if kind != GenerationTask.TYPE_DM:
                return None
            tasks = list(
                GenerationTask.objects.filter(
                    task_type=kind, status=GenerationTask.STATUS_PENDING
                )
            )
            for task in tasks:
                PrivateMessage.objects.create(
                    sender=task.agent,
                    recipient=task.recipient,
                    content=f"[auto]{task.payload.get('instruction', '')[:50]}",
                    tick_number=task.payload.get("tick_number"),
                )
                task.status = GenerationTask.STATUS_COMPLETED
                task.save(update_fields=["status", "updated_at"])
            processed_counts["dm"] += len(tasks)
            return None

        drain_queue_mock.side_effect = drain_stub

        call_command("run_tick", seed=7, origin="unit-test", force=True)

        tasks = GenerationTask.objects.filter(task_type=GenerationTask.TYPE_DM)
        self.assertGreater(tasks.count(), 0)
        self.assertTrue(tasks.filter(recipient=trexxak).exists())

        inbox = PrivateMessage.objects.filter(recipient=trexxak)
        self.assertGreater(inbox.count(), 0)

        tick = TickLog.objects.get(tick_number=1)
        trexxak_events = [
            event
            for event in tick.events
            if event.get("type") == "private_message_task"
            and event.get("mode") == "trexxak_probe"
        ]
        self.assertTrue(trexxak_events)
        self.assertGreater(processed_counts["dm"], 0)

        record_tick_run_mock.assert_called_once_with(1, origin="unit-test")
