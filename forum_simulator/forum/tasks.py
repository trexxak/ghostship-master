from __future__ import annotations

import random
import time
from typing import Any, Dict

import functools
import logging
from types import SimpleNamespace

try:  # pragma: no cover - optional Celery dependency for worker runtime
    from celery import shared_task
    from celery.utils.log import get_task_logger
except ImportError:  # pragma: no cover - allow local dev without celery installed
    def shared_task(*dargs, **dkwargs):
        bind = dkwargs.get("bind", False)

        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if bind:
                    if not args:
                        return func(SimpleNamespace(request=None), *args, **kwargs)
                    return func(*args, **kwargs)
                return func(*args, **kwargs)

            def delay(*args, **kwargs):
                if bind:
                    return func(SimpleNamespace(request=None), *args, **kwargs)
                return func(*args, **kwargs)

            wrapper.delay = delay
            return wrapper

        return decorator

    def get_task_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)
from django.conf import settings
from django.core.management import CommandError, call_command

from forum.services import sim_config, tick_control

logger = get_task_logger(__name__)


def _scheduler_config() -> Dict[str, Any]:
    cfg = sim_config.scheduler_settings()
    return {
        "interval": float(cfg.get("interval_seconds", getattr(settings, "SIM_TICK_INTERVAL_SECONDS", 60))),
        "jitter": float(cfg.get("jitter_seconds", getattr(settings, "SIM_TICK_JITTER_SECONDS", 0))),
        "queue_burst": int(cfg.get("queue_burst", getattr(settings, "SIM_TICK_QUEUE_BURST", 0))),
    }


def _consume_override() -> Dict[str, Any]:
    override = tick_control.consume_manual_override()
    if not override:
        return {}
    command_kwargs: Dict[str, Any] = {}
    seed = override.get("seed")
    if seed is not None:
        command_kwargs["seed"] = int(seed)
    if override.get("force"):
        command_kwargs["force"] = True
    note = override.get("note")
    if note:
        command_kwargs["note"] = note
    origin = override.get("origin")
    if origin:
        command_kwargs["origin"] = origin
    card = override.get("oracle_card")
    if card:
        command_kwargs["oracle_card"] = card
    energy_multiplier = override.get("energy_multiplier")
    if energy_multiplier is not None:
        command_kwargs["energy_multiplier"] = float(energy_multiplier)
    return command_kwargs


@shared_task(bind=True, name="forum.tasks.run_scheduled_tick")
def run_scheduled_tick(self) -> dict[str, Any]:
    """Execute a simulation tick from Celery beat."""
    scheduler_cfg = _scheduler_config()
    jitter = max(0.0, scheduler_cfg.get("jitter", 0.0))
    if jitter:
        delay = random.uniform(0.0, jitter)
        logger.debug("Applying scheduler jitter delay of %.2fs", delay)
        time.sleep(delay)

    if tick_control.is_frozen():
        logger.info("Tick skipped: %s", tick_control.state_label())
        return {"skipped": tick_control.state_label()}

    override_kwargs = _consume_override()
    command_kwargs: Dict[str, Any] = {"origin": override_kwargs.pop("origin", "celery")}
    command_kwargs.update(override_kwargs)

    logger.info("Triggering simulation tick via Celery (kwargs=%s)", command_kwargs)
    call_command("run_tick", **command_kwargs)

    queue_burst = scheduler_cfg.get("queue_burst", 0)
    if queue_burst:
        process_generation_burst.delay(limit=queue_burst)

    return {"status": "ok", "override": bool(override_kwargs)}


@shared_task(bind=True, name="forum.tasks.process_generation_burst", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_generation_burst(self, limit: int | None = None) -> dict[str, Any]:
    """Drain a slice of the text generation queue in the background."""
    limit = int(limit or _scheduler_config().get("queue_burst") or 0)
    if limit <= 0:
        return {"status": "noop"}
    try:
        call_command("process_generation_queue", limit=limit)
    except CommandError as exc:
        logger.warning("Generation burst skipped: %s", exc)
        return {"status": "skipped", "reason": str(exc)}
    return {"status": "processed", "limit": limit}
