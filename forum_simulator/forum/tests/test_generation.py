from __future__ import annotations

from unittest import mock

from django.test import TestCase

from forum.models import Agent, GenerationTask, PrivateMessage
from forum.services import generation


class GenerationDMTests(TestCase):
    @mock.patch("forum.services.generation.remaining_requests", return_value=1)
    @mock.patch(
        "forum.services.generation.generate_completion",
        return_value={"success": True, "text": "ghost ping"},
    )
    def test_dm_tasks_persist_private_messages(self, completion_mock, remaining_requests_mock) -> None:
        sender = Agent.objects.create(
            name="Beacon",
            archetype="Helper",
            traits={},
            needs={},
            cooldowns={},
        )
        recipient = Agent.objects.create(
            name="Aurora",
            archetype="Scout",
            traits={},
            needs={},
            cooldowns={},
        )
        GenerationTask.objects.create(
            task_type=GenerationTask.TYPE_DM,
            agent=sender,
            recipient=recipient,
            payload={"tick_number": 3, "instruction": "say hi"},
        )

        processed, deferred = generation.process_generation_queue(limit=1)

        self.assertEqual(processed, 1)
        self.assertEqual(deferred, 0)

        inbox = PrivateMessage.objects.filter(sender=sender, recipient=recipient)
        self.assertEqual(inbox.count(), 1)
        message = inbox.first()
        assert message is not None
        self.assertEqual(message.content, "ghost ping")
        self.assertEqual(message.tick_number, 3)
        self.assertFalse(message.authored_by_operator)

        completion_mock.assert_called_once()
        remaining_requests_mock.assert_called()
