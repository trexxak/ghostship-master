"""Small helpers for resilient model updates used by services.

Provide a safe_save(obj, fields) that filters update_fields to concrete model
fields and falls back to a full save when necessary. This prevents
ValueError from bubbling up when callers include non-concrete fields such as
`updated_at` or accidentally include m2m fields.
"""
from __future__ import annotations

from typing import Iterable
import logging

logger = logging.getLogger(__name__)


def safe_save(obj, fields: Iterable[str] | None = None) -> None:
    """Save a model object safely.

    Filters the requested update_fields to concrete model fields and falls back
    to a full save. On unexpected exceptions we log a warning and swallow the
    exception to avoid crashing background jobs.
    """
    if fields is None:
        try:
            obj.save()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("safe_save full save failed for %r: %s",
                           obj, exc, exc_info=True)
        return

    desired = list(dict.fromkeys(list(fields)))
    concrete = {f.name for f in obj._meta.fields}
    valid = [f for f in desired if f in concrete]
    try:
        if valid:
            obj.save(update_fields=valid)
        else:
            obj.save()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            "safe_save failed for %r with fields %s (valid: %s): %s",
            obj,
            desired,
            valid,
            exc,
            exc_info=True,
        )
        try:
            obj.save()
        except Exception as exc2:  # pragma: no cover - last resort
            logger.error(
                "safe_save final fallback save failed for %r: %s", obj, exc2, exc_info=True)
