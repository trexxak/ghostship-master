from django.apps import apps
from django.test import TestCase, override_settings

from forum.models import Goal


@override_settings(ENABLE_AUTO_TICKS=False)
class ForumAppConfigTests(TestCase):
    def test_ready_populates_goal_catalogue(self) -> None:
        Goal.objects.filter(slug="progress-spark").delete()
        self.assertFalse(Goal.objects.filter(slug="progress-spark").exists())

        config = apps.get_app_config("forum")
        config.ready()

        self.assertTrue(Goal.objects.filter(slug="progress-spark").exists())
