from __future__ import annotations

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from forum.models import Agent, Board, Post, Thread, ThreadWatch
from forum.services import watchers as watcher_service
from forum.templatetags import forum_extras


class GuestModeBehaviourTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.factory = RequestFactory()
        cls.board = Board.objects.create(name="Signals", slug="signals")
        cls.admin = Agent.objects.create(
            name="t.admin",
            archetype="Custodian",
            role=Agent.ROLE_ADMIN,
        )
        cls.trexxak = Agent.objects.create(
            name="trexxak",
            archetype="Organic Interface",
            role=Agent.ROLE_ORGANIC,
        )
        cls.thread = Thread.objects.create(
            title="How to pilot the Organic Interface",
            author=cls.admin,
            board=cls.board,
        )
        cls.post = Post.objects.create(
            thread=cls.thread,
            author=cls.admin,
            content="Diagnostics snapshot.",
        )

    def setUp(self) -> None:
        self.client: Client = Client()

    # Helpers -----------------------------------------------------------------

    def _build_request(self):
        request = self.factory.get("/")
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        return request

    def _touch_watch(self, *, agent: Agent | None = None) -> None:
        request = self._build_request()
        watcher_service.touch_thread_watch(request, self.thread, agent=agent)

    # Tests -------------------------------------------------------------------

    def test_report_access_requires_organic_mode(self) -> None:
        url = reverse("forum:report_post", args=[self.post.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("forum:thread_detail", args=[self.thread.pk]), response.url)

    def test_report_access_allowed_for_organic_mode(self) -> None:
        session = self.client.session
        session["act_as_oi"] = True
        session.save()
        url = reverse("forum:report_post", args=[self.post.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What happened?")

    def test_watchers_line_includes_roles_and_guests(self) -> None:
        self._touch_watch(agent=self.admin)
        self._touch_watch(agent=self.trexxak)
        self._touch_watch(agent=None)
        self.thread.refresh_from_db()
        line = forum_extras.watchers_line(self.thread)
        self.assertIn("t.admin", line)
        self.assertIn("trexxak", line)
        self.assertIn("OI", line)
        self.assertIn("1 guest", line)
        self.assertIn("are watching", line)

    def test_nav_pruned_when_piloting(self) -> None:
        response = self.client.get(reverse("forum:board_list"))
        self.assertContains(response, "Dashboard")
        self.assertNotContains(response, "User-Control Panel")

        session = self.client.session
        session["act_as_oi"] = True
        session.save()
        response = self.client.get(reverse("forum:board_list"))
        self.assertContains(response, "User-Control Panel")
        self.assertNotContains(response, "Oracle Draws")

    def test_agent_detail_sanitised_for_organic(self) -> None:
        session = self.client.session
        session["act_as_oi"] = True
        session.save()
        response = self.client.get(reverse("forum:agent_detail", args=[self.admin.pk]))
        self.assertContains(response, "Detailed dossier hidden")
        self.assertContains(response, "withheld while in trexxak mode")
        self.assertNotContains(response, "Traits")
        self.assertNotContains(response, "Sent Messages")

    def test_presence_map_layout(self) -> None:
        ThreadWatch.objects.create(
            thread=self.thread,
            agent=self.admin,
            session_key="agent-session",
            last_seen=timezone.now(),
        )
        ThreadWatch.objects.create(
            thread=self.thread,
            session_key="guest-session",
            last_seen=timezone.now(),
        )
        response = self.client.get(reverse("forum:who"))
        self.assertContains(response, "presence-row")
        self.assertContains(response, "reading")
        self.assertContains(response, self.thread.title)
        self.assertContains(response, "guest")
