from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from forum.models import Agent, Board, Thread, Post, PrivateMessage, ModerationEvent, Goal, AgentGoal
from forum.services import notifications as notifications_service


class NotificationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.organism = Agent.objects.create(
            name="trexxak",
            archetype="organic interface",
            role=Agent.ROLE_ORGANIC,
        )
        cls.actor = Agent.objects.create(
            name="specter",
            archetype="observer",
            role=Agent.ROLE_MEMBER,
        )
        cls.board = Board.objects.create(
            name="Operations",
            slug="operations",
        )
        cls.thread = Thread.objects.create(
            title="Ops Log",
            author=cls.actor,
            board=cls.board,
        )

    def test_collect_returns_mentions_messages_and_role_events(self) -> None:
        post_time = timezone.now() - timedelta(minutes=2)
        post = Post.objects.create(
            thread=self.thread,
            author=self.actor,
            content="Routing @trexxak to the operations console ASAP.",
        )
        Post.objects.filter(pk=post.pk).update(created_at=post_time)
        post.refresh_from_db()
        dm = PrivateMessage.objects.create(
            sender=self.actor,
            recipient=self.organism,
            content="Ping — check the console when you can.",
        )
        event = ModerationEvent.objects.create(
            actor=self.actor,
            target_agent=self.organism,
            action_type="set-role:moderator",
            metadata={"new_role": "moderator", "reason": "Manual override"},
        )
        goal = Goal.objects.create(
            name="Quick Calibration",
            slug="quick-calibration",
            description="Run a calibration check.",
            goal_type=Goal.TYPE_PROGRESS,
            category="progress",
            status=Goal.STATUS_ACTIVE,
            target=1.0,
        )
        award = AgentGoal.objects.create(
            agent=self.organism,
            goal=goal,
            progress=1.0,
            unlocked_at=timezone.now() - timedelta(minutes=1),
        )

        window_start = timezone.now() - timedelta(hours=1)
        payload = notifications_service.collect(self.organism, since=window_start)
        identifiers = {item["id"] for item in payload}

        self.assertIn(f"mention:{post.pk}", identifiers)
        self.assertIn(f"pm:{dm.pk}", identifiers)
        self.assertIn(f"role:{event.pk}", identifiers)
        self.assertIn(f"achievement:{award.pk}", identifiers)

        created_values = [item["created"] for item in payload]
        self.assertTrue(all(isinstance(value, str) and "T" in value for value in created_values))
