from __future__ import annotations

import random

from django.test import TestCase

from forum.models import Agent, Thread, Board
from forum.services import generation as generation_service


class ReplyLengthSamplingTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.agent = Agent.objects.create(
            name="sample-ghost",
            archetype="Hothead",
            speech_profile={
                "min_words": 18,
                "max_words": 34,
                "mean_words": 24,
                "sentence_range": [1, 3],
                "burst_chance": 0.0,
                "burst_range": [6, 10],
            },
        )

    def test_sample_within_declared_bounds(self) -> None:
        rng = random.Random(42)
        hint = generation_service._sample_post_length(self.agent, rng=rng)
        self.assertGreaterEqual(hint["words"], 18)
        self.assertLessEqual(hint["words"], 34)
        self.assertGreaterEqual(hint["sentences"], 1)
        self.assertLessEqual(hint["sentences"], 3)
        self.assertFalse(hint["burst"])

    def test_forced_burst_respects_short_range(self) -> None:
        self.agent.speech_profile = {
            "min_words": 18,
            "max_words": 40,
            "mean_words": 24,
            "sentence_range": [1, 3],
            "burst_chance": 1.0,
            "burst_range": [4, 8],
        }
        rng = random.Random(7)
        hint = generation_service._sample_post_length(self.agent, rng=rng)
        self.assertTrue(hint["burst"])
        self.assertGreaterEqual(hint["words"], 4)
        self.assertLessEqual(hint["words"], 8)

    def test_defaults_apply_when_profile_missing(self) -> None:
        agent = Agent.objects.create(name="default-ghost", archetype="Watcher")
        rng = random.Random(11)
        hint = generation_service._sample_post_length(agent, rng=rng)
        self.assertGreaterEqual(hint["words"], 6)
        self.assertTrue(1 <= hint["sentences"] <= 3)


class LengthInstructionIntegrationTests(TestCase):
    def test_length_instruction_in_prompt(self) -> None:
        agent = Agent.objects.create(
            name="glimmer",
            archetype="Hothead",
            speech_profile={
                "min_words": 18,
                "max_words": 30,
                "mean_words": 22,
                "sentence_range": [1, 2],
                "burst_chance": 0.0,
                "burst_range": [6, 10],
            },
        )
        board = Board.objects.create(name="Test", slug="test")
        thread = Thread.objects.create(title="Sample Thread", author=agent, board=board)
        task = generation_service.enqueue_generation_task(
            task_type=generation_service.GenerationTask.TYPE_REPLY,
            agent=agent,
            thread=thread,
        )
        prompt = generation_service._build_prompt(task)
        self.assertIn("Aim for roughly", prompt)
