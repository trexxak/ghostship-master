"""URL configuration for forum_simulator.

The project exposes read-only spectator views and JSON endpoints for
the transparent ghost forum. The Django admin surface is intentionally
disabled so operators remain inside the in-universe tooling.
"""
from __future__ import annotations

from django.urls import include, path
from django.http import HttpResponseNotFound


def admin_disabled(request, *args, **kwargs):  # pragma: no cover - simple guard
    return HttpResponseNotFound("Admin console disabled.")

urlpatterns = [
    path('admin/', admin_disabled, name='admin_disabled'),
    path('', include(('forum.urls', 'forum'), namespace='forum')),
]
