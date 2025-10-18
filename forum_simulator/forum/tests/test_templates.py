from django.template.loader import get_template
from django.test import SimpleTestCase


class TemplateSmokeTests(SimpleTestCase):
    """Ensure key templates and their partials are available."""

    def test_primary_templates_exist(self) -> None:
        template_names = [
            "forum/base.html",
            "forum/board_list.html",
            "forum/board_detail.html",
            "forum/thread_detail.html",
            "forum/partials/post_card.html",
            "forum/partials/board_row.html",
            "forum/partials/thread_row.html",
            "forum/partials/progress_overlays.html",
        ]

        for name in template_names:
            with self.subTest(name=name):
                get_template(name)
