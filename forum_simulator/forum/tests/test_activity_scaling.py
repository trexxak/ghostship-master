from __future__ import annotations

from datetime import timedelta

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.utils import timezone

from forum.models import Agent, SessionActivity
from forum.services import activity as activity_service
from forum.simulation.allocators import Allocation


class SessionActivityServiceTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def _request(self, path: str = "/") -> object:
        request = self.factory.get(path)
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        request.oi_agent = None
        request.oi_active = False
        return request

    def test_touch_session_records_entry(self) -> None:
        agent = Agent.objects.create(name="trexxak", archetype="Organic Interface", role=Agent.ROLE_ORGANIC)
        request = self._request("/threads/1/")
        request.oi_agent = agent
        request.oi_active = True
        activity_service.touch_session(request)
        snapshot = activity_service.session_snapshot()
        self.assertEqual(snapshot.total, 1)
        self.assertEqual(snapshot.organic, 1)

    def test_prune_stale_sessions(self) -> None:
        now = timezone.now()
        old = SessionActivity.objects.create(session_key="old")
        fresh = SessionActivity.objects.create(session_key="fresh")
        SessionActivity.objects.filter(pk=old.pk).update(last_seen=now - timedelta(minutes=10))
        SessionActivity.objects.filter(pk=fresh.pk).update(last_seen=now)
        removed = activity_service.prune_stale_sessions(now=now, window_seconds=60)
        self.assertEqual(removed, 1)
        self.assertFalse(SessionActivity.objects.filter(pk=old.pk).exists())
        self.assertTrue(SessionActivity.objects.filter(pk=fresh.pk).exists())


class ActivityScalingTests(TestCase):
    def test_zero_sessions_dampens_allocation(self) -> None:
        allocation = Allocation(registrations=5, threads=4, replies=20, private_messages=6, moderation_events=3)
        snapshot = activity_service.SessionSnapshot(total=0, organic=0, window=180, tier="dormant", factor=0.1)
        scaled = activity_service.apply_activity_scaling(allocation, snapshot)
        self.assertLessEqual(scaled.replies, 2)
        self.assertLessEqual(scaled.threads, 1)

    def test_busy_tier_preserves_allocation(self) -> None:
        allocation = Allocation(registrations=5, threads=4, replies=20, private_messages=6, moderation_events=3)
        snapshot = activity_service.SessionSnapshot(total=5, organic=0, window=180, tier="busy", factor=1.0)
        scaled = activity_service.apply_activity_scaling(allocation, snapshot)
        self.assertEqual(scaled.replies, 20)
        self.assertIn("activity:busy", " ".join(scaled.notes))
