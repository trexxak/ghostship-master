from __future__ import annotations

import os
from types import SimpleNamespace

try:  # pragma: no cover - Celery is optional for local/test runs
    from celery import Celery
    from celery.schedules import schedule
except ImportError:  # pragma: no cover
    Celery = None
    schedule = None

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forum_simulator.settings")


if Celery is None:  # pragma: no cover - fallback when celery isn't installed
    class _StubCelery:
        def __init__(self) -> None:
            self.conf = SimpleNamespace(beat_schedule={})

        def config_from_object(self, *args, **kwargs) -> None:  # noqa: D401 - stub
            """No-op stub."""

        def autodiscover_tasks(self) -> None:  # noqa: D401 - stub
            """No-op stub."""

    app = _StubCelery()
else:
    app = Celery("forum_simulator")
    app.config_from_object("django.conf:settings", namespace="CELERY")
    app.autodiscover_tasks()


def _build_beat_schedule() -> dict[str, dict[str, object]]:
    """Construct the Celery beat schedule using the dynamic sim config."""

    if Celery is None or schedule is None:
        return {}

    from django.conf import settings
    from forum.services import sim_config

    scheduler_cfg = sim_config.scheduler_settings()
    interval = float(scheduler_cfg.get("interval_seconds", getattr(settings, "SIM_TICK_INTERVAL_SECONDS", 60)))
    interval = max(10.0, interval)
    routes = getattr(settings, "CELERY_TASK_ROUTES", {}) or {}
    queue_name = routes.get("forum.tasks.run_scheduled_tick", {}).get("queue", "ticks")
    return {
        "simulation.tick": {
            "task": "forum.tasks.run_scheduled_tick",
            "schedule": schedule(interval),
            "options": {"queue": queue_name},
        }
    }


app.conf.beat_schedule = _build_beat_schedule()

__all__ = ("app",)
