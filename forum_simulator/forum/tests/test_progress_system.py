from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.utils import timezone

from forum.context_processors import progress_notifications
from forum.models import Agent, AgentGoal, Goal, GoalEvaluation, Thread, Board, Post, TickLog
from forum.services import progress as progress_service


class ProgressCatalogTests(TestCase):
    def setUp(self) -> None:
        self.organism = Agent.objects.create(
            name="trexxak",
            archetype="organic operator",
            role=Agent.ROLE_ORGANIC,
        )

    def test_catalog_seed_populates_progression(self) -> None:
        progress_service.ensure_goal_catalog()
        progression = Goal.objects.filter(goal_type=Goal.TYPE_PROGRESS).order_by("priority")
        self.assertGreaterEqual(progression.count(), 9)
        priorities = [item.priority for item in progression]
        self.assertEqual(len(set(priorities)), len(priorities))
        self.assertTrue(Goal.objects.filter(slug="progress-spark").exists())

    def test_emoji_palette_includes_fifty_unique_symbols(self) -> None:
        palette = progress_service.emoji_palette()
        self.assertEqual(len(palette), 50)
        self.assertEqual(len(set(palette)), 50)


class ProgressNotificationTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.organism = Agent.objects.create(
            name="trexxak",
            archetype="organic operator",
            role=Agent.ROLE_ORGANIC,
        )
        progress_service.ensure_goal_catalog()

    def _request(self):
        request = self.factory.get("/")
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        return request

    def test_toast_triggers_for_progression_unlock(self) -> None:
        request = self._request()
        goal = Goal.objects.get(slug="progress-spark")
        AgentGoal.objects.create(
            agent=self.organism,
            goal=goal,
            progress=1.0,
            unlocked_at=timezone.now(),
            metadata={"trigger_session_key": request.session.session_key},
        )
        context = progress_notifications(request)
        self.assertEqual(len(context["progress_toasts"]), 1)
        self.assertEqual(context["progress_toasts"][0]["slug"], "progress-spark")

    def test_ticker_omits_current_session_unlocks(self) -> None:
        request = self._request()
        other_session_key = "other-session"
        goal = Goal.objects.get(slug="first-footfall")
        AgentGoal.objects.create(
            agent=self.organism,
            goal=goal,
            progress=1.0,
            unlocked_at=timezone.now(),
            metadata={"trigger_session_key": other_session_key},
        )
        context = progress_notifications(request)
        self.assertEqual(len(context["progress_ticker"]), 1)

    def test_metrics_delta_consumed_from_session(self) -> None:
        request = self._request()
        request.session["progress_metrics_delta"] = {"threads": 2, "replies": 1, "reports": 0}
        request.session.modified = True
        context = progress_notifications(request)
        self.assertEqual(context["progress_metrics_delta"], {"threads": 2, "replies": 1})
        self.assertNotIn("progress_metrics_delta", request.session)
        repeat = progress_notifications(request)
        self.assertEqual(repeat["progress_metrics_delta"], {})

    def test_manual_progress_event_consumed_from_session(self) -> None:
        request = self._request()
        request.session["progress_event_queue"] = [
            {
                "slug": "role-change-admin",
                "name": "t.admin made you Admin!",
                "emoji": "👑",
                "agent": "t.admin",
            }
        ]
        request.session.modified = True
        context = progress_notifications(request)
        self.assertTrue(context["progress_toasts"])
        self.assertEqual(context["progress_toasts"][0]["slug"], "role-change-admin")
        self.assertIn("role-change-admin", [item["slug"] for item in context["progress_ticker"]])
        self.assertIn("role-change-admin", [item["slug"] for item in context["progress_broadcasts"]])
        self.assertNotIn("progress_event_queue", request.session)

    def test_broadcasts_include_recent_unlocks(self) -> None:
        request = self._request()
        goal = Goal.objects.get(slug="progress-spark")
        AgentGoal.objects.create(
            agent=self.organism,
            goal=goal,
            progress=1.0,
            unlocked_at=timezone.now(),
            metadata={"trigger_session_key": request.session.session_key},
        )
        context = progress_notifications(request)
        self.assertEqual(len(context["progress_broadcasts"]), 1)
        self.assertEqual(context["progress_broadcasts"][0]["slug"], "progress-spark")
        follow_up = progress_notifications(request)
        self.assertEqual(follow_up["progress_broadcasts"], [])


class ProgressRefereeTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.organism = Agent.objects.create(
            name="trexxak",
            archetype="organic operator",
            role=Agent.ROLE_ORGANIC,
        )
        self.board = Board.objects.create(name="Ops", slug="ops")
        self.thread = Thread.objects.create(title="Ops Log", author=self.organism, board=self.board)
        self.post = Post.objects.create(
            thread=self.thread,
            author=self.organism,
            content="First contact!",
            authored_by_operator=True,
        )
        progress_service.ensure_goal_catalog()

    def _seed_ticks(self, upto: int = 5) -> None:
        base_time = timezone.now()
        for idx in range(1, upto + 1):
            TickLog.objects.create(
                tick_number=idx,
                timestamp=base_time - timedelta(minutes=upto - idx),
                events=[{"type": "post", "post_id": self.post.id}],
            )

    @patch("forum.services.progress.openrouter.generate_completion")
    def test_evaluate_tick_batch_unlocks_achievement(self, mock_completion) -> None:
        self._seed_ticks()
        payload = {
            "unlocked": [
                {
                    "slug": "progress-spark",
                    "post_id": self.post.id,
                    "confidence": 0.92,
                    "rationale": "First organic post detected.",
                }
            ]
        }
        mock_completion.return_value = {"success": True, "text": json.dumps(payload)}
        evaluation, fresh = progress_service.evaluate_tick_batch(
            batch_ticks=[1, 2, 3, 4, 5],
            actor=self.organism,
        )
        self.assertTrue(fresh)
        self.assertEqual(evaluation.status, GoalEvaluation.STATUS_COMPLETED)
        self.assertTrue(
            AgentGoal.objects.filter(agent=self.organism, goal__slug="progress-spark").exists()
        )
