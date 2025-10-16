from __future__ import annotations

import logging
from datetime import datetime
import uuid

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone

from forum.services import configuration as config_service
from forum.services import unlockables as unlockable_service
from forum.models import Agent

logger = logging.getLogger(__name__)


class SessionActivityMiddleware:
    """Record lightweight heartbeats for adaptive activity scaling."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if hasattr(request, "session"):
                from forum.services import activity as activity_service

                activity_service.touch_session(request)
        except Exception:  # pragma: no cover - telemetry only
            logger.debug("Session activity heartbeat failed", exc_info=True)
        return response


class APIRateLimitMiddleware:
    """Basic per-identifier daily rate limiting for public API routes."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._default_limit = getattr(settings, "API_DAILY_LIMIT", 1000)

    def __call__(self, request):
        response = self._maybe_reject(request)
        if response is not None:
            return response
        return self.get_response(request)

    def _maybe_reject(self, request):
        path = request.path
        if not path.startswith("/api/"):
            return None

        limit = config_service.get_int("API_DAILY_LIMIT", self._default_limit)
        if limit <= 0:
            return None

        identifier = request.META.get("HTTP_X_API_KEY") or request.META.get("REMOTE_ADDR") or "anon"
        date_key = timezone.now().strftime("%Y%m%d")
        cache_key = f"api-rate:{identifier}:{date_key}"
        count = cache.get(cache_key, 0)
        if count >= limit:
            retry_at = (datetime.strptime(date_key, "%Y%m%d") + timezone.timedelta(days=1)).isoformat()
            return JsonResponse(
                {
                    "error": "rate_limited",
                    "message": "Daily API quota exhausted. Please wait before retrying.",
                    "retry_at": retry_at,
                },
                status=429,
            )

        added = cache.add(cache_key, 1, 87_000)
        if not added:
            cache.incr(cache_key, 1)
        return None


class TrexxakImpersonationMiddleware:
    """Attach the organic interface agent to requests opting into OI mode.
    
    This middleware ensures that trexxak only acts through operator input and
    prevents any automated actions."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._cached_agent: Agent | None = None

    def __call__(self, request):
        request.oi_agent = None
        request.oi_active = False
        request.oi_session_key = None
        request.oi_debug_role = ""
        request.oi_effective_role = None
        if hasattr(request, "session") and request.session.get("act_as_oi"):
            agent = self._resolve_agent()
            if agent:
                agent = Agent.objects.get(pk=agent.pk)
                request.oi_agent = agent
                request.oi_active = True
                avatar_options = unlockable_service.avatar_option_catalog(agent)
                allowed_avatar_values = {
                    str(option.get("value"))
                    for option in avatar_options
                    if option.get("value")
                }
                preference = (
                    str(request.session.get("oi_avatar_override", "")).strip()
                    if hasattr(request, "session")
                    else ""
                )
                if preference and preference in allowed_avatar_values:
                    agent.avatar_slug = preference
                else:
                    default_avatar = next(
                        (
                            option.get("value")
                            for option in avatar_options
                            if option.get("slot") == "default"
                        ),
                        unlockable_service.default_avatar_option().get("value"),
                    )
                    if default_avatar:
                        agent.avatar_slug = default_avatar
                session_key = request.session.get("oi_session_key")
                if not session_key:
                    session_key = uuid.uuid4().hex
                    request.session["oi_session_key"] = session_key
                    request.session["oi_session_started_at"] = timezone.now().isoformat()
                    request.session.modified = True
                request.oi_session_key = session_key
                debug_role = ""
                if hasattr(request, "session"):
                    debug_role = str(request.session.get("oi_debug_role", "")).strip().lower()
                allowed_overlays = {Agent.ROLE_MEMBER, Agent.ROLE_MODERATOR, Agent.ROLE_ADMIN, Agent.ROLE_BANNED}
                if debug_role in allowed_overlays:
                    agent._effective_role = debug_role
                    request.oi_debug_role = debug_role
                else:
                    agent._effective_role = agent.role
                request.oi_effective_role = agent._effective_role
        response = self.get_response(request)
        return response

    def _resolve_agent(self) -> Agent | None:
        if self._cached_agent and self._cached_agent.role == Agent.ROLE_ORGANIC:
            return self._cached_agent
        agent = Agent.objects.filter(role=Agent.ROLE_ORGANIC).order_by("id").first()
        self._cached_agent = agent
        return agent
