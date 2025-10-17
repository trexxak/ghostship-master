from __future__ import annotations

from datetime import timedelta

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from forum.models import (
    Agent,
    Board,
    Thread,
    Post,
    PrivateMessage,
    GenerationTask,
    OrganicInteractionLog,
    ThreadWatch,
    Goal,
    GoalProgress,
    AgentGoal,
)
from forum.services import generation as generation_service
from forum.services import watchers as watcher_service
from forum.services import missions as missions_service
from forum.services import configuration as config_service
from forum.simulation.allocators import determine_specials


class OrganicInterfaceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.organism = Agent.objects.create(
            name="trexxak",
            archetype="organic operator",
            role=Agent.ROLE_ORGANIC,
        )
        cls.member = Agent.objects.create(
            name="specter",
            archetype="watcher",
            role=Agent.ROLE_MEMBER,
        )
        cls.board = Board.objects.create(
            name="Operations",
            slug="operations",
        )
        cls.thread = Thread.objects.create(
            title="Ops Log",
            author=cls.member,
            board=cls.board,
        )
        cls.factory = RequestFactory()

    def _activate_organic(self) -> None:
        session = self.client.session
        session["act_as_oi"] = True
        session.save()

    def test_generation_queue_blocks_organic_agent(self) -> None:
        task = generation_service.enqueue_generation_task(
            task_type=GenerationTask.TYPE_REPLY,
            agent=self.organism,
            thread=self.thread,
        )
        processed, deferred = generation_service.process_generation_queue(limit=1)

        self.assertEqual(processed, 1)
        self.assertEqual(deferred, 0)

        task.refresh_from_db()
        self.assertEqual(task.status, GenerationTask.STATUS_COMPLETED)
        self.assertIn("organic_interface_guardrail", task.response_text)

        logs = OrganicInteractionLog.objects.filter(
            action=OrganicInteractionLog.ACTION_AUTOMATION_BLOCKED,
            metadata__task_id=task.id,
        )
        self.assertTrue(logs.exists())

    def test_manual_entry_creates_operator_post(self) -> None:
        self._activate_organic()

        compose_url = reverse("forum:oi_manual_entry")
        response = self.client.get(compose_url)
        self.assertEqual(response.status_code, 200)

        oi_session_key = self.client.session.get("oi_session_key")
        self.assertTrue(oi_session_key)

        response = self.client.post(
            compose_url,
            {
                "mode": "post",
                "thread": self.thread.pk,
                "content": "Operator note: human eyes on this.",
                "action": "finalize",
            },
        )
        # Successful submission redirects to the thread detail view.
        self.assertEqual(response.status_code, 302)

        post = Post.objects.latest("id")
        self.assertTrue(post.authored_by_operator)
        self.assertEqual(post.operator_session_key, oi_session_key)
        self.assertEqual(post.author_id, self.organism.id)

        logs = OrganicInteractionLog.objects.filter(
            action=OrganicInteractionLog.ACTION_POST,
            metadata__post_id=post.id,
        )
        self.assertTrue(logs.exists())

    def test_manual_entry_thread_mode_renders_hidden_fields(self) -> None:
        self._activate_organic()

        compose_url = reverse("forum:oi_manual_entry")
        response = self.client.get(f"{compose_url}?mode=thread&board={self.board.pk}")
        self.assertEqual(response.status_code, 200)

        html = response.content.decode()
        self.assertInHTML(
            '<input type="hidden" name="mode" value="thread" id="id_mode">',
            html,
        )
        self.assertInHTML(
            f'<input type="hidden" name="board" value="{self.board.pk}" id="id_board">',
            html,
        )

    def test_manual_entry_creates_operator_thread(self) -> None:
        self._activate_organic()

        compose_url = reverse("forum:oi_manual_entry")
        response = self.client.post(
            f"{compose_url}?mode=thread&board={self.board.pk}",
            {
                "mode": "thread",
                "board": str(self.board.pk),
                "title": "Launch Control Status",
                "content": "Thread opened manually through the organic interface.",
                "action": "finalize",
            },
        )
        self.assertEqual(response.status_code, 302)

        new_thread = Thread.objects.latest("id")
        self.assertNotEqual(new_thread.pk, self.thread.pk)
        self.assertEqual(new_thread.title, "Launch Control Status")
        self.assertEqual(new_thread.author_id, self.organism.id)
        self.assertTrue(
            new_thread.posts.filter(author=self.organism, content__icontains="organic interface").exists()
        )

    def test_compose_dm_prefills_hidden_recipient(self) -> None:
        self._activate_organic()

        url = reverse("forum:compose_dm", args=[self.member.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertInHTML(
            f'<input type="hidden" name="recipient" value="{self.member.pk}" id="id_recipient">',
            html,
        )

    def test_control_panel_compose_creates_dm(self) -> None:
        self._activate_organic()

        compose_url = reverse("forum:oi_control_panel")
        response = self.client.post(
            compose_url,
            {
                "compose_pm": "1",
                "to": self.member.name,
                "subject": "Field report",
                "body": "Operator ping delivered via control panel.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("#messages:compose"))

        dm = PrivateMessage.objects.latest("id")
        self.assertEqual(dm.sender_id, self.organism.id)
        self.assertEqual(dm.recipient_id, self.member.id)
        self.assertTrue(dm.authored_by_operator)
        self.assertEqual(dm.content, "Operator ping delivered via control panel.")


class CoreStabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.factory = RequestFactory()
        cls.agent = Agent.objects.create(
            name="glowworm",
            archetype="observer",
            role=Agent.ROLE_MEMBER,
        )
        cls.board = Board.objects.create(
            name="Diagnostics",
            slug="diagnostics",
        )
        cls.thread = Thread.objects.create(
            title="Diagnostics Log",
            author=cls.agent,
            board=cls.board,
        )

    def _build_request(self):
        request = self.factory.get("/")
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        request.META["HTTP_USER_AGENT"] = "pytest/ghost"
        return request

    def test_determine_specials_bias(self) -> None:
        class DummyRandom:
            def __init__(self, values):
                self._values = iter(values)

            def random(self):
                return next(self._values)

        rng_hot = DummyRandom([0.0, 0.0])
        omen, seance = determine_specials(
            energy_prime=16,
            rng=rng_hot,
            streaks={"omen": 8, "seance": 5},
        )
        self.assertTrue(omen)
        self.assertTrue(seance)

        rng_cold = DummyRandom([0.99, 0.99])
        omen_cold, seance_cold = determine_specials(
            energy_prime=16,
            rng=rng_cold,
            streaks={"omen": 0, "seance": 0},
        )
        self.assertFalse(omen_cold)
        self.assertFalse(seance_cold)

    def test_mission_progress_deduplicates_per_tick(self) -> None:
        mission = Goal.objects.create(
            name="Test Mission",
            slug="test-mission",
            description="Ensure single increment per tick.",
            category="contracts",
            goal_type=Goal.TYPE_MISSION,
            status=Goal.STATUS_ACTIVE,
            is_global=True,
            progress_current=0.0,
            target=2.0,
        )

        entry = missions_service.record_progress(
            mission,
            delta=1.0,
            tick_number=42,
            note="rule:seance-followup",
        )
        self.assertIsInstance(entry, GoalProgress)

        # Duplicate invocation for the same tick and rule should be ignored.
        missions_service.record_progress(
            mission,
            delta=1.0,
            tick_number=42,
            note="rule:seance-followup",
        )

        mission.refresh_from_db()
        self.assertEqual(mission.progress_current, 1.0)
        self.assertEqual(
            GoalProgress.objects.filter(
                goal=mission,
                tick_number=42,
                note="rule:seance-followup",
            ).count(),
            1,
        )

    def test_watcher_presence_decay_and_metadata(self) -> None:
        config_service.set_value("THREAD_WATCH_WINDOW", 60)
        request = self._build_request()

        watcher_service.touch_thread_watch(request, self.thread, agent=self.agent)

        self.thread.refresh_from_db()
        watchers_data = self.thread.watchers or {}
        self.assertEqual(watchers_data.get("agents"), [self.agent.name])
        self.assertEqual(watchers_data.get("guests"), 0)
        self.assertEqual(watchers_data.get("total"), 1)

        # Create a stale watcher and ensure pruning removes it.
        stale_watch = ThreadWatch.objects.create(
            thread=self.thread,
            session_key="stale-session",
        )
        ThreadWatch.objects.filter(pk=stale_watch.pk).update(
            last_seen=timezone.now() - timedelta(seconds=360)
        )
        removed = watcher_service.prune_stale_watches()
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(
            ThreadWatch.objects.filter(pk=stale_watch.pk).exists()
        )


class MissionEvaluationTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.organism = Agent.objects.create(
            name="trexx-mission",
            archetype="organic",
            role=Agent.ROLE_ORGANIC,
        )
        missions_service.ensure_default_catalog()

    def test_evaluate_tick_completes_seance_mission(self) -> None:
        mission = Goal.objects.get(slug="salvage-the-seance")
        mission.target = 1.0
        mission.progress_current = 0.0
        mission.status = Goal.STATUS_ACTIVE
        mission.save(update_fields=["target", "progress_current", "status"])

        events = [{"type": "specials", "flags": {"seance": True}}]
        story = missions_service.evaluate_tick(42, events)

        mission.refresh_from_db()
        self.assertEqual(mission.status, Goal.STATUS_COMPLETED)
        self.assertTrue(mission.metadata.get("reward_unlocked"))
        self.assertTrue(
            any(evt.get("type") == "mission_reward" and evt.get("mission") == mission.slug for evt in story)
        )
        reward_slug = mission.metadata.get("reward_goal") or mission.metadata.get("reward_achievement")
        if reward_slug:
            self.assertTrue(
                AgentGoal.objects.filter(
                    agent=self.organism,
                    goal__slug=reward_slug,
                ).exists()
            )
        self.assertTrue(
            GoalProgress.objects.filter(goal=mission, tick_number=42).exists()
        )
