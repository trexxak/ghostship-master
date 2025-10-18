from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from forum.lore import spawn_board_on_request as real_spawn_board_on_request
from forum.management.commands import run_tick
from forum.models import Agent, Board, GenerationTask, OracleDraw, Post, PrivateMessage, Thread, TickLog


class RunTickCommandTests(TestCase):
    @mock.patch("forum.management.commands.run_tick._drain_queue_for")
    @mock.patch("forum.management.commands.run_tick.generate_completion", return_value={"success": True, "text": "{\"threads\": []}"})
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
        generate_completion_mock,
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
    @mock.patch(
        "forum.management.commands.run_tick.generate_completion",
        return_value={"success": True, "text": "{\"threads\": []}"},
    )
    @mock.patch(
        "forum.management.commands.run_tick.enqueue_generation_task",
        return_value=SimpleNamespace(id=1),
    )
    @mock.patch("forum.management.commands.run_tick.tick_control.record_tick_run")
    @mock.patch("forum.management.commands.run_tick.tick_control.describe_state", return_value={})
    @mock.patch("forum.management.commands.run_tick.build_energy_profile")
    @mock.patch("forum.management.commands.run_tick.allocate_actions")
    @mock.patch("forum.management.commands.run_tick.agent_state.progress_agents", return_value=[{"agent": "Aurora"}])
    @mock.patch("forum.management.commands.run_tick.ensure_core_boards")
    @mock.patch("forum.management.commands.run_tick.ensure_origin_story")
    @mock.patch("forum.management.commands.run_tick.process_lore_events", return_value=[])
    @mock.patch("forum.management.commands.run_tick.activity_service.session_snapshot", return_value={})
    @mock.patch(
        "forum.management.commands.run_tick.activity_service.apply_activity_scaling",
        side_effect=lambda allocation, snapshot: allocation,
    )
    @mock.patch("forum.management.commands.run_tick.config_service.get_int", return_value=1)
    def test_fallback_thread_briefs_prefer_quiet_boards(
        self,
        get_int_mock,
        apply_scaling_mock,
        session_snapshot_mock,
        process_lore_mock,
        ensure_origin_mock,
        ensure_boards_mock,
        progress_agents_mock,
        allocate_actions_mock,
        build_energy_profile_mock,
        describe_state_mock,
        record_tick_run_mock,
        enqueue_task_mock,
        generate_completion_mock,
        drain_queue_mock,
    ) -> None:
        admin = Agent.objects.create(
            name="t.admin",
            archetype="Admin",
            traits={},
            needs={},
            cooldowns={},
            role=Agent.ROLE_ADMIN,
        )
        Agent.objects.create(
            name="Aurora",
            archetype="Scout",
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

        news = Board.objects.create(name="News + Meta", slug="news-meta", position=1)
        games = Board.objects.create(name="Games (general)", slug="games", position=2)
        ensure_boards_mock.return_value = {news.slug: news, games.slug: games}
        ensure_origin_mock.side_effect = lambda boards: None

        Thread.objects.create(title="How to operate…", author=admin, board=news)

        build_energy_profile_mock.return_value = SimpleNamespace(rolls=[2, 4], energy=5, energy_prime=7)

        class OneThreadAllocation:
            def __init__(self) -> None:
                self.registrations = 0
                self.threads = 1
                self.replies = 0
                self.private_messages = 0
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

        allocate_actions_mock.return_value = OneThreadAllocation()
        drain_queue_mock.return_value = None

        call_command("run_tick", seed=42, origin="unit-test", force=True)

        created = (
            Thread.objects.exclude(title="How to operate…")
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(created)
        self.assertEqual(created.board.slug, "games")
        self.assertTrue(created.topics)
        self.assertEqual(created.topics[0], "games")

        record_tick_run_mock.assert_called_once_with(1, origin="unit-test")

    @mock.patch("forum.management.commands.run_tick._drain_queue_for")
    @mock.patch("forum.management.commands.run_tick.spawn_board_on_request")
    @mock.patch("forum.management.commands.run_tick.generate_completion")
    @mock.patch("forum.management.commands.run_tick.enqueue_generation_task")
    @mock.patch("forum.management.commands.run_tick.choose_board_for_thread")
    @mock.patch("forum.management.commands.run_tick.tick_control.record_tick_run")
    @mock.patch("forum.management.commands.run_tick.tick_control.describe_state", return_value={})
    @mock.patch("forum.management.commands.run_tick.build_energy_profile")
    @mock.patch("forum.management.commands.run_tick.allocate_actions")
    @mock.patch("forum.management.commands.run_tick.agent_state.progress_agents", return_value=[{"agent": "Aurora"}])
    @mock.patch("forum.management.commands.run_tick.ensure_core_boards", return_value={})
    @mock.patch("forum.management.commands.run_tick.ensure_origin_story")
    @mock.patch("forum.management.commands.run_tick.process_lore_events", return_value=[])
    @mock.patch("forum.management.commands.run_tick.activity_service.session_snapshot", return_value={})
    @mock.patch(
        "forum.management.commands.run_tick.activity_service.apply_activity_scaling",
        side_effect=lambda allocation, snapshot: allocation,
    )
    @mock.patch("forum.management.commands.run_tick.config_service.get_int", return_value=0)
    def test_thread_briefs_and_board_markers(
        self,
        get_int_mock,
        apply_scaling_mock,
        session_snapshot_mock,
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
        generate_completion_mock,
        spawn_board_mock,
        drain_queue_mock,
    ) -> None:
        Agent.objects.create(
            name="Aurora",
            archetype="Scout",
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

        base_board = Board.objects.create(name="Commons", slug="commons", position=1)
        ensure_boards_mock.return_value = {base_board.slug: base_board}
        choose_board_mock.return_value = base_board

        build_energy_profile_mock.return_value = SimpleNamespace(rolls=[3, 5], energy=6, energy_prime=8)

        class ThreadAllocation:
            def __init__(self) -> None:
                self.registrations = 0
                self.threads = 2
                self.replies = 0
                self.private_messages = 0
                self.moderation_events = 0
                self.omen = False
                self.seance = False
                self.notes: list[str] = ["signal spike"]
                self.omen_details: dict[str, object] = {}
                self.seance_details: dict[str, object] = {}

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

        allocate_actions_mock.return_value = ThreadAllocation()

        thread_plan = {
            "threads": [
                {
                    "title": "Anomaly Watch // shard drift",
                    "hook": "Shard telemetry is slipping again; cross-check last night's logs.",
                    "topics": ["signal", "analysis"],
                    "subject": "shard drift alert",
                },
                {
                    "title": "Commons maintenance log",
                    "hook": "Document the fixes everyone promised to ship by dawn.",
                    "topics": ["meta", "ship-log"],
                    "subject": "maintenance backlog",
                },
            ]
        }
        generate_completion_mock.return_value = {"success": True, "text": json.dumps(thread_plan)}

        def enqueue_stub(*args, **kwargs):
            return SimpleNamespace(id=len(GenerationTask.objects.all()) + 1)

        enqueue_task_mock.side_effect = enqueue_stub

        thread_task_calls = {"count": 0}

        def drain_stub(kind, *, thread=None, max_loops=6, batch=8):
            if kind != GenerationTask.TYPE_THREAD_START or thread is None:
                return None
            thread_task_calls["count"] += 1
            if thread_task_calls["count"] == 1:
                Post.objects.create(
                    thread=thread,
                    author=thread.author,
                    content="BOARD-NEW: anomalies | Anomaly Tracking\nfirst post",
                )
            else:
                Post.objects.create(
                    thread=thread,
                    author=thread.author,
                    content="BOARD: commons\nsecond post",
                )
            return None

        drain_queue_mock.side_effect = drain_stub
        spawn_board_mock.side_effect = real_spawn_board_on_request

        call_command("run_tick", seed=55, origin="unit-test", force=True)

        self.assertGreaterEqual(Thread.objects.count(), 2)
        first_thread = Thread.objects.get(title="Anomaly Watch // shard drift")
        second_thread = Thread.objects.get(title="Commons maintenance log")

        self.assertEqual(first_thread.topics[:2], ["anomalies", "signal"])
        self.assertIn("analysis", first_thread.topics)
        opener = first_thread.posts.order_by("created_at", "id").first()
        self.assertIsNotNone(opener)
        self.assertTrue((opener.content or "").startswith("BOARD-NEW:"))

        self.assertTrue(Board.objects.filter(slug="anomalies").exists())
        board_map = dict(Thread.objects.values_list("title", "board__slug"))
        self.assertEqual(first_thread.board.slug, "anomalies", msg=board_map)

        tick = TickLog.objects.get(tick_number=1)
        relocations = [event for event in tick.events if event.get("type") == "thread_relocate"]
        self.assertTrue(
            any(event.get("thread") == first_thread.title and event.get("to") == "anomalies" for event in relocations),
            msg=f"Relocations: {relocations}",
        )

        self.assertEqual(second_thread.topics[:2], ["commons", "meta"])
        self.assertIn("ship-log", second_thread.topics)
        self.assertEqual(second_thread.board.slug, "commons")

        generate_completion_mock.assert_called_once()
        self.assertGreaterEqual(thread_task_calls["count"], 2)

    @mock.patch("forum.management.commands.run_tick._drain_queue_for")
    @mock.patch("forum.management.commands.run_tick.spawn_board_on_request")
    @mock.patch("forum.management.commands.run_tick.generate_completion")
    @mock.patch("forum.management.commands.run_tick.enqueue_generation_task")
    @mock.patch("forum.management.commands.run_tick.choose_board_for_thread")
    @mock.patch("forum.management.commands.run_tick.tick_control.record_tick_run")
    @mock.patch("forum.management.commands.run_tick.tick_control.describe_state", return_value={})
    @mock.patch("forum.management.commands.run_tick.build_energy_profile")
    @mock.patch("forum.management.commands.run_tick.allocate_actions")
    @mock.patch("forum.management.commands.run_tick.agent_state.progress_agents", return_value=[{"agent": "Aurora"}])
    @mock.patch("forum.management.commands.run_tick.ensure_core_boards", return_value={})
    @mock.patch("forum.management.commands.run_tick.ensure_origin_story")
    @mock.patch("forum.management.commands.run_tick.process_lore_events", return_value=[])
    @mock.patch("forum.management.commands.run_tick.activity_service.session_snapshot", return_value={})
    @mock.patch(
        "forum.management.commands.run_tick.activity_service.apply_activity_scaling",
        side_effect=lambda allocation, snapshot: allocation,
    )
    @mock.patch("forum.management.commands.run_tick.config_service.get_int", return_value=0)
    def test_generated_thread_title_is_clamped(
        self,
        get_int_mock,
        apply_scaling_mock,
        session_snapshot_mock,
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
        generate_completion_mock,
        spawn_board_mock,
        drain_queue_mock,
    ) -> None:
        Agent.objects.create(
            name="Aurora",
            archetype="Scout",
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

        base_board = Board.objects.create(name="Commons", slug="commons", position=1)
        ensure_boards_mock.return_value = {base_board.slug: base_board}
        choose_board_mock.return_value = base_board

        build_energy_profile_mock.return_value = SimpleNamespace(rolls=[2, 6], energy=4, energy_prime=9)

        class SingleThreadAllocation:
            def __init__(self) -> None:
                self.registrations = 0
                self.threads = 1
                self.replies = 0
                self.private_messages = 0
                self.moderation_events = 0
                self.omen = False
                self.seance = False
                self.notes: list[str] = []
                self.omen_details: dict[str, object] = {}
                self.seance_details: dict[str, object] = {}

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

        allocate_actions_mock.return_value = SingleThreadAllocation()

        max_length = Thread._meta.get_field("title").max_length
        long_title = "LLM horizon report // " + ("x" * (max_length + 25))

        thread_plan = {
            "threads": [
                {
                    "title": long_title,
                    "hook": "",
                    "topics": ["signal", "analysis"],
                    "subject": "sensor horizon",
                }
            ]
        }
        generate_completion_mock.return_value = {"success": True, "text": json.dumps(thread_plan)}

        enqueue_task_mock.side_effect = lambda *args, **kwargs: SimpleNamespace(id=1)
        drain_queue_mock.return_value = None
        spawn_board_mock.return_value = None

        call_command("run_tick", seed=77, origin="unit-test", force=True)

        expected_title = long_title[:max_length]
        created_thread = Thread.objects.get(title=expected_title)

        self.assertEqual(created_thread.title, expected_title)
        self.assertLessEqual(len(created_thread.title), max_length)

    @mock.patch("forum.management.commands.run_tick._drain_queue_for")
    @mock.patch("forum.management.commands.run_tick.tick_control.record_tick_run")
    @mock.patch("forum.management.commands.run_tick.tick_control.describe_state", return_value={})
    @mock.patch("forum.management.commands.run_tick.build_energy_profile")
    @mock.patch("forum.management.commands.run_tick.allocate_actions")
    @mock.patch("forum.management.commands.run_tick.agent_state.progress_agents", return_value=[{"agent": "Aurora"}])
    @mock.patch("forum.management.commands.run_tick.ensure_core_boards", return_value={})
    @mock.patch("forum.management.commands.run_tick.ensure_origin_story")
    @mock.patch("forum.management.commands.run_tick.process_lore_events")
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

    @mock.patch("forum.management.commands.run_tick._drain_queue_for")
    @mock.patch("forum.management.commands.run_tick.tick_control.record_tick_run")
    @mock.patch("forum.management.commands.run_tick.tick_control.describe_state", return_value={})
    @mock.patch("forum.management.commands.run_tick.build_energy_profile")
    @mock.patch("forum.management.commands.run_tick.allocate_actions")
    @mock.patch("forum.management.commands.run_tick.agent_state.progress_agents", return_value=[{"agent": "Aurora"}])
    @mock.patch("forum.management.commands.run_tick.ensure_core_boards", return_value={})
    @mock.patch("forum.management.commands.run_tick.ensure_origin_story")
    @mock.patch("forum.management.commands.run_tick.process_lore_events")
    @mock.patch("forum.management.commands.run_tick.activity_service.session_snapshot")
    @mock.patch(
        "forum.management.commands.run_tick.activity_service.apply_activity_scaling",
        side_effect=lambda allocation, snapshot: allocation,
    )
    @mock.patch("forum.management.commands.run_tick.config_service.get_int", return_value=2)
    def test_trexxak_dm_reserved_when_budget_low(
        self,
        get_int_mock,
        apply_scaling_mock,
        session_snapshot_mock,
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

        # Pending DM to encourage peer reply consumption
        PrivateMessage.objects.create(sender=partner, recipient=greeter, content="reply soon")

        board = Board.objects.create(name="Commons", slug="commons", position=1)
        Thread.objects.create(title="Existing thread", author=greeter, board=board)

        build_energy_profile_mock.return_value = SimpleNamespace(rolls=[1, 2], energy=5, energy_prime=7)

        class TinyDMAllocation:
            def __init__(self) -> None:
                self.registrations = 0
                self.threads = 0
                self.replies = 0
                self.private_messages = 1
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

        allocate_actions_mock.return_value = TinyDMAllocation()

        process_lore_mock.return_value = [
            {"kind": "user_join", "meta": {"id": newcomer.id}},
        ]
        session_snapshot_mock.return_value = SimpleNamespace(total=5, tier="mid", factor=1.0)

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
            return None

        drain_queue_mock.side_effect = drain_stub

        call_command("run_tick", seed=9, origin="unit-test", force=True)

        tasks = GenerationTask.objects.filter(task_type=GenerationTask.TYPE_DM)
        self.assertTrue(tasks.filter(recipient=trexxak).exists())
        self.assertTrue(tasks.filter(recipient=newcomer).exists())

        inbox = PrivateMessage.objects.filter(recipient=trexxak)
        self.assertGreater(inbox.count(), 0)

        tick = TickLog.objects.get(tick_number=1)
        trexxak_events = [
            event
            for event in tick.events
            if event.get("type") == "private_message_task"
            and event.get("mode") == "trexxak_probe"
        ]
        welcome_events = [
            event
            for event in tick.events
            if event.get("type") == "private_message_task"
            and event.get("mode") == "welcome_greeting"
        ]
        self.assertTrue(trexxak_events)
        self.assertTrue(welcome_events)

        record_tick_run_mock.assert_called_once_with(1, origin="unit-test")

    def test_unanswered_dm_streak_caps_after_limit(self) -> None:
        sender = Agent.objects.create(
            name="Sender",
            archetype="Helper",
            traits={},
            needs={},
            cooldowns={},
        )
        recipient = Agent.objects.create(
            name="Recipient",
            archetype="Watcher",
            traits={},
            needs={},
            cooldowns={},
        )

        self.assertEqual(run_tick.unanswered_dm_streak(sender, recipient), 0)

        for index in range(2):
            PrivateMessage.objects.create(
                sender=sender,
                recipient=recipient,
                content=f"ping-{index}",
            )

        self.assertEqual(run_tick.unanswered_dm_streak(sender, recipient), 2)

        PrivateMessage.objects.create(
            sender=recipient,
            recipient=sender,
            content="reply",
        )

        self.assertEqual(run_tick.unanswered_dm_streak(sender, recipient), 0)

        for index in range(4):
            PrivateMessage.objects.create(
                sender=sender,
                recipient=recipient,
                content=f"nudge-{index}",
            )

        self.assertEqual(
            run_tick.unanswered_dm_streak(sender, recipient),
            run_tick.MAX_UNANSWERED_DM_STREAK,
        )
