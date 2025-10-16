from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from forum.models import Agent, Board, Thread, Post, GenerationTask
from forum.services import generation as generation_service


class GhostMentionGenerationTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.author = Agent.objects.create(
            name="Echo",
            archetype="signal analyst",
            role=Agent.ROLE_MEMBER,
        )
        cls.other = Agent.objects.create(
            name="Specter",
            archetype="observer",
            role=Agent.ROLE_MEMBER,
        )
        cls.board = Board.objects.create(
            name="Signal Logs",
            slug="signal-logs",
        )
        cls.thread = Thread.objects.create(
            title="Trace Relay",
            author=cls.other,
            board=cls.board,
        )

    def test_generation_strips_self_and_unknown_mentions(self) -> None:
        GenerationTask.objects.create(
            task_type=GenerationTask.TYPE_REPLY,
            agent=self.author,
            thread=self.thread,
        )

        with patch("forum.services.generation.remaining_requests", return_value=1), patch(
            "forum.services.generation.generate_completion",
            return_value={"success": True, "text": "@Echo loops to @Specter and @Phantom."},
        ):
            processed, deferred = generation_service.process_generation_queue(limit=1)

        self.assertEqual(processed, 1)
        self.assertEqual(deferred, 0)

        post = Post.objects.get(thread=self.thread, author=self.author)
        self.assertEqual(post.content, "Echo loops to @Specter and Phantom.")

    def test_placeholder_reply_replaced_on_success(self) -> None:
        GenerationTask.objects.create(
            task_type=GenerationTask.TYPE_REPLY,
            agent=self.author,
            thread=self.thread,
        )
        GenerationTask.objects.create(
            task_type=GenerationTask.TYPE_REPLY,
            agent=self.author,
            thread=self.thread,
        )

        side_effect = [
            {"success": False, "text": "(offline ghostship placeholder) awaiting link"},
            {"success": True, "text": "Link established. @Specter, check the pulse."},
        ]

        with patch("forum.services.generation.remaining_requests", return_value=1), patch(
            "forum.services.generation.generate_completion", side_effect=side_effect
        ):
            processed, deferred = generation_service.process_generation_queue(limit=2)

        self.assertEqual(processed, 2)
        self.assertEqual(deferred, 0)
        posts = list(Post.objects.filter(thread=self.thread))
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertFalse(post.is_placeholder)
        self.assertEqual(post.content, "Link established. @Specter, check the pulse.")
