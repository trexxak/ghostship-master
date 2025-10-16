from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Optional

from django.conf import settings
from django.core.management import call_command, CommandError

from . import tick_control

logger = logging.getLogger(__name__)


class TickScheduler:
    """Background helper that periodically advances the simulation."""

    def __init__(self, *, interval: float, jitter: float, startup_delay: float, queue_burst: int) -> None:
        self.interval = max(5.0, float(interval))
        self.jitter = max(0.0, float(jitter))
        self.startup_delay = max(0.0, float(startup_delay))
        self.queue_burst = max(0, int(queue_burst))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        logger.info(
            "Starting tick scheduler (interval=%ss, jitter=%ss, startup_delay=%ss, queue_burst=%s)",
            self.interval,
            self.jitter,
            self.startup_delay,
            self.queue_burst,
        )
        self._thread = threading.Thread(
            target=self._run, name="forum-tick-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        if self.startup_delay:
            logger.debug(
                "Tick scheduler sleeping for startup delay %.2fs", self.startup_delay)
            if self._stop.wait(self.startup_delay):
                return
        while not self._stop.is_set():
            if tick_control.is_frozen():
                logger.info("Tick scheduler paused (freeze=%s)", tick_control.state_label())
                if self._stop.wait(min(self.interval, 30.0)):
                    return
                continue
            cycle_start = time.monotonic()
            try:
                call_command("run_tick", origin="scheduler")
            except Exception:  # noqa: BLE001
                logger.exception("Tick scheduler failed to execute run_tick")
            if self.queue_burst:
                try:
                    call_command("process_generation_queue",
                                 limit=self.queue_burst)
                except CommandError as exc:
                    logger.warning("Generation queue skipped: %s", exc)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Tick scheduler failed to process generation queue")
            sleep_for = self._next_delay(cycle_start)
            logger.debug("Tick scheduler sleeping for %.2fs", sleep_for)
            if self._stop.wait(sleep_for):
                break

    def _next_delay(self, cycle_start: float) -> float:
        raw_delay = self.interval + random.uniform(-self.jitter, self.jitter)
        raw_delay = max(5.0, raw_delay)
        elapsed = time.monotonic() - cycle_start
        return max(2.0, raw_delay - elapsed)


_scheduler_lock = threading.Lock()
_scheduler: Optional[TickScheduler] = None


def get_scheduler() -> TickScheduler:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            interval = getattr(settings, "SIM_TICK_INTERVAL_SECONDS", 45)
            jitter = getattr(settings, "SIM_TICK_JITTER_SECONDS", 12)
            startup = getattr(settings, "SIM_TICK_STARTUP_DELAY_SECONDS", 10)
            queue_burst = getattr(settings, "SIM_TICK_QUEUE_BURST", 12)
            _scheduler = TickScheduler(
                interval=interval,
                jitter=jitter,
                startup_delay=startup,
                queue_burst=queue_burst,
            )
        return _scheduler


def should_start_scheduler() -> bool:
    env_switch = os.environ.get("FORUM_AUTO_TICKS", "1").lower()
    if env_switch in {"0", "off", "false", "no"}:
        return False
    return getattr(settings, "ENABLE_AUTO_TICKS", True)
