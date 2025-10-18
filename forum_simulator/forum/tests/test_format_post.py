from __future__ import annotations

from django.test import TestCase

from forum.models import Agent
from forum.templatetags import forum_extras


class FormatPostMarkdownTests(TestCase):
    def setUp(self) -> None:
        forum_extras._AGENT_CACHE.clear()

    def test_basic_markdown_elements_render(self) -> None:
        html = forum_extras.format_post("Signal **boost** with _clarity_.\n- ping\n- pong")
        self.assertIn("<strong>boost</strong>", html)
        self.assertIn("<em>clarity</em>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<li>ping</li>", html)

    def test_code_blocks_and_inline_code(self) -> None:
        content = "```python\nprint('echo')\n```\nInline `@ghost` call"
        html = forum_extras.format_post(content)
        self.assertIn("<pre><code class=\"language-python\">print(&#x27;echo&#x27;)", html)
        self.assertIn("<code>@ghost</code>", html)
        self.assertNotIn("data-handle", html)

    def test_markdown_quotes_render_as_blockquote(self) -> None:
        html = forum_extras.format_post("> traced signal\n> persists")
        self.assertIn("<blockquote>", html)
        self.assertIn("traced signal", html)

    def test_known_mentions_linked(self) -> None:
        agent = Agent.objects.create(name="Echo", archetype="listener", role=Agent.ROLE_MEMBER)
        html = forum_extras.format_post("Paging @Echo for status")
        self.assertIn("class=\"mention ghost-handle role-member\"", html)
        self.assertIn(f"href=\"/agents/{agent.pk}/\"", html)

    def test_unknown_mentions_remain_plain_text(self) -> None:
        html = forum_extras.format_post("Shadowing @Unknown")
        self.assertIn("@Unknown", html)
        self.assertNotIn("data-handle", html)

    def test_html_is_sanitized(self) -> None:
        html = forum_extras.format_post("Injected <script>alert('x')</script> text")
        self.assertNotIn("<script>", html)
        self.assertIn("Injected", html)

    def test_single_newlines_become_line_breaks(self) -> None:
        html = forum_extras.format_post("first line\nsecond line")
        self.assertIn("<br>", html)

    def test_headings_and_ordered_lists_render(self) -> None:
        content = "# Mission Log\n1. Chart the nebula\n2. Signal the crew"
        html = forum_extras.format_post(content)
        self.assertIn("<h1>Mission Log</h1>", html)
        self.assertIn("<ol>", html)
        self.assertIn("<li>Chart the nebula</li>", html)

    def test_links_and_strikethrough_are_supported(self) -> None:
        content = "Reference [lore](https://example.com) and ~~expired~~ directives."
        html = forum_extras.format_post(content)
        self.assertIn('href="https://example.com"', html)
        self.assertIn("<del>expired</del>", html)

    def test_unsafe_links_are_not_rendered(self) -> None:
        content = "Do not click [this](javascript:alert('nope'))"
        html = forum_extras.format_post(content)
        self.assertNotIn("javascript:alert", html)
        self.assertNotIn("<a href", html)
