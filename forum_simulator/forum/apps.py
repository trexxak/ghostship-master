from __future__ import annotations

import os
import sys
import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ForumConfig(AppConfig):
    """Configuration for the forum app."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'forum'

    def ready(self) -> None:  # pragma: no cover - startup wiring
        from django.conf import settings  # noqa: WPS433 - runtime import to avoid config issues

        if not getattr(settings, "ENABLE_AUTO_TICKS", True):
            return
        if os.environ.get("FORUM_AUTO_TICKS", "1").lower() in {"0", "off", "false", "no"}:
            return
        if os.environ.get("RUN_MAIN") != "true":
            return
        command = sys.argv[1] if len(sys.argv) > 1 else ""
        if command not in {"runserver", "runserver_plus"}:
            return

        try:
            from .services.tick_scheduler import get_scheduler

            scheduler = get_scheduler()
            scheduler.start()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to start tick scheduler")
