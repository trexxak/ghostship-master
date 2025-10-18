from __future__ import annotations

import logging
import os
import sys

from django.apps import AppConfig
from django.db.utils import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)


class ForumConfig(AppConfig):
    """Configuration for the forum app."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'forum'

    def ready(self) -> None:  # pragma: no cover - startup wiring
        from django.conf import settings  # noqa: WPS433 - runtime import to avoid config issues

        # Ensure the goal catalogue is available so achievements, avatar unlocks,
        # and mission rewards can resolve their referenced Goal records.
        try:
            from .services import goals as goal_service  # noqa: WPS433 - lazy import for app loading

            goal_service.ensure_goal_catalog()
        except (OperationalError, ProgrammingError):
            logger.info("Goal catalogue not ready; skipping bootstrap until after migrations run.")
        except Exception:  # noqa: BLE001 - log unexpected bootstrap errors and continue startup
            logger.exception("Failed to refresh goal catalogue during app initialisation")

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
