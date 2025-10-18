from __future__ import annotations

import random

from django.test import TestCase

from forum.models import Agent, Thread, Board, Post
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

    def test_prompt_includes_persona_examples(self) -> None:
        agent = Agent.objects.create(name="PalmVigil", archetype="Sentinel")
        board = Board.objects.create(name="Ops", slug="ops")
        thread = Thread.objects.create(title="Checklist", author=agent, board=board)
        task = generation_service.enqueue_generation_task(
            task_type=generation_service.GenerationTask.TYPE_REPLY,
            agent=agent,
            thread=thread,
        )
        prompt = generation_service._build_prompt(task)
        self.assertIn("Persona snapshots", prompt)
        self.assertIn("log(tick 1412)", prompt)


class PromptContextTimelineTests(TestCase):
    def setUp(self) -> None:
        self.board = Board.objects.create(name="History", slug="history")
        self.agent = Agent.objects.create(name="scribe", archetype="Seeker")
        self.witness = Agent.objects.create(name="witness", archetype="Archivist")
        self.analyst = Agent.objects.create(name="analyst", archetype="Scholar")
        self.scout = Agent.objects.create(name="scout", archetype="Scout")

    def _create_task(self, thread: Thread) -> generation_service.GenerationTask:
        return generation_service.enqueue_generation_task(
            task_type=generation_service.GenerationTask.TYPE_REPLY,
            agent=self.agent,
            thread=thread,
        )

    def test_prompt_contains_opener_and_timeline(self) -> None:
        thread = Thread.objects.create(title="Cold Case", author=self.witness, board=self.board)
        Post.objects.create(thread=thread, author=self.witness, content="Opener lays out the missing hiker timeline.")
        Post.objects.create(thread=thread, author=self.analyst, content="Analyst cross-references campsite logs from 1998.")
        Post.objects.create(thread=thread, author=self.scout, content="Scout relays drone footage from the ridge walk.")
        Post.objects.create(thread=thread, author=self.witness, content="Witness files a missing equipment report.")
        Post.objects.create(thread=thread, author=self.analyst, content="Analyst compiles a map of last-known sightings.")
        Post.objects.create(thread=thread, author=self.scout, content="Scout uploads fresh trail camera footage.")

        task = self._create_task(thread)
        prompt = generation_service._build_prompt(task)

        self.assertIn("Thread opener:", prompt)
        self.assertIn("[witness] Opener lays out the missing hiker timeline.", prompt)
        self.assertIn("Earlier thread highlights:", prompt)
        self.assertIn("Analyst cross-references campsite logs", prompt)
        self.assertIn("Mentionable ghosts and receipts:", prompt)
        self.assertIn("- @analyst: Analyst compiles a map of last-known sightings.", prompt)
        self.assertIn(
            "Only mention ghosts listed above and anchor any tag to the cited detail; do not invent handles or tag yourself unless directly summoned.",
            prompt,
        )

    def test_prompt_flags_double_post_risk(self) -> None:
        thread = Thread.objects.create(title="Strange Signals", author=self.agent, board=self.board)
        Post.objects.create(thread=thread, author=self.agent, content="Initial ping from the relay array.")
        Post.objects.create(thread=thread, author=self.agent, content="Follow-up with the spectral analysis attachments.")

        task = self._create_task(thread)
        prompt = generation_service._build_prompt(task)

        self.assertIn("You authored the most recent comment", prompt)
