from __future__ import annotations

from django.test import SimpleTestCase

from forum.personas import persona_examples_for


class PersonaLibraryTests(SimpleTestCase):
    def test_known_handle_returns_examples(self) -> None:
        examples = persona_examples_for("t.admin")
        self.assertGreaterEqual(len(examples), 3)
        self.assertTrue(all(isinstance(line, str) and line for line in examples))

    def test_lookup_is_case_insensitive(self) -> None:
        self.assertEqual(
            persona_examples_for("Trexxak"),
            persona_examples_for("trexxak"),
        )

    def test_unknown_handle_returns_empty(self) -> None:
        self.assertEqual(persona_examples_for("ghost-unknown"), [])
