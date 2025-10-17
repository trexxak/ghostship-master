"""Project bootstrap for the forum simulator."""

from __future__ import annotations

try:  # pragma: no cover - Celery optional for tests/local dev
    from .celery import app as celery_app
except Exception:  # pragma: no cover - expose a stub when Celery missing
    celery_app = None

__all__ = ["celery_app"]
