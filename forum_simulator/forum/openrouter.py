from __future__ import annotations

import logging
import time
import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from functools import lru_cache

import requests
from django.conf import settings
from django.utils import timezone
from django.core.cache import cache

from forum.models import OpenRouterUsage
from forum.services._safe import safe_save

logger = logging.getLogger(__name__)

DEFAULT_MODEL = getattr(settings, "OPENROUTER_MODEL", "gpt-4o-mini")
BASE_URL = getattr(settings, "OPENROUTER_BASE_URL",
                   "https://openrouter.ai/api/v1")
DEFAULT_MAX_TOKENS = getattr(settings, "OPENROUTER_DEFAULT_MAX_TOKENS", 220)
DAILY_LIMIT = getattr(settings, "OPENROUTER_DAILY_REQUEST_LIMIT", 1000)
RETRY_STATUS = {429, 500, 502, 503, 504}
API_KEY = getattr(settings, "OPENROUTER_API_KEY", "").strip()
HEADERS = {"Content-Type": "application/json"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"
if getattr(settings, "OPENROUTER_TITLE", None):
    HEADERS["X-Title"] = settings.OPENROUTER_TITLE
if getattr(settings, "OPENROUTER_REFERRER", None):
    HEADERS["HTTP-Referer"] = settings.OPENROUTER_REFERRER
try:
    FAILURE_BACKOFF_SECONDS = int(
        getattr(settings, "OPENROUTER_FAILURE_BACKOFF_SECONDS", 300))
except (TypeError, ValueError):
    FAILURE_BACKOFF_SECONDS = 300
_offline_until: datetime | None = None


def _usage_for_today() -> OpenRouterUsage:
    today = timezone.now().date()
    usage, _ = OpenRouterUsage.objects.get_or_create(
        day=today, defaults={"request_count": 0})
    return usage


def remaining_requests() -> int:
    if not API_KEY:
        return 0
    usage = _usage_for_today()
    return max(DAILY_LIMIT - usage.request_count, 0)


def _increment_usage(requests: int = 1) -> None:
    if not API_KEY:
        return
    usage = _usage_for_today()
    usage.request_count += requests
    # Use safe_save to avoid ValueError if callers include non-concrete
    # fields like `updated_at` in update_fields elsewhere.
    safe_save(usage, ["request_count", "updated_at"])


def _mark_offline(reason: str) -> None:
    global _offline_until
    _offline_until = timezone.now() + timedelta(seconds=max(5, FAILURE_BACKOFF_SECONDS))
    logger.warning(
        "OpenRouter temporarily marked offline (%s); will retry after %ss.",
        reason,
        FAILURE_BACKOFF_SECONDS,
    )


def _should_short_circuit() -> bool:
    global _offline_until
    if _offline_until is None:
        return False
    now = timezone.now()
    if now >= _offline_until:
        _offline_until = None
        return False
    return True


def generate_completion(
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0.7,
    stop: Optional[list[str]] = None,
    model: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call OpenRouter chat completions API, obeying daily quota.

    Returns a dict with keys: ``success``, ``text``, and ``response`` (raw JSON)
    or error information. Falls back to a lightweight heuristic string when an
    API key is missing or the daily quota is exhausted.
    """

    if remaining_requests() <= 0 or not HEADERS.get("Authorization"):
        logger.warning(
            "OpenRouter quota exhausted or API key missing; using fallback text.")
        return {"success": False, "text": _fallback_text(prompt), "response": None}

    if _should_short_circuit():
        logger.debug("OpenRouter offline window active; using fallback text.")
        return {"success": False, "text": _fallback_text(prompt), "response": None}

    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": "You are an expressive forum participant."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens or DEFAULT_MAX_TOKENS,
        "temperature": temperature,
    }
    if stop:
        payload["stop"] = stop
    if metadata:
        payload["extra"] = metadata

    url = f"{BASE_URL}/chat/completions"
    attempts = 0
    while attempts < 3:
        attempts += 1
        try:
            response = requests.post(
                url, headers=HEADERS, json=payload, timeout=30)
            if response.status_code in RETRY_STATUS:
                delay = 1.5 * attempts
                logger.warning(
                    "OpenRouter returned %s; retrying in %.1fs", response.status_code, delay)
                time.sleep(delay)
                continue
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices")
            if not choices or not isinstance(choices, list):
                logger.error("OpenRouter response missing 'choices': %s", data)
                _mark_offline("missing_choices")
                return {
                    "success": False,
                    "text": _fallback_text(prompt),
                    "response": data,
                    "error": data.get("error") or "missing_choices",
                }
            message = (choices[0] or {}).get("message", {})
            content = message.get("content")
            if not isinstance(content, str):
                logger.error(
                    "OpenRouter response missing message content: %s", data)
                _mark_offline("missing_message_content")
                return {
                    "success": False,
                    "text": _fallback_text(prompt),
                    "response": data,
                    "error": data.get("error") or "missing_message_content",
                }
            text = content.strip()
            _increment_usage()
            return {"success": True, "text": text, "response": data}
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None),
                             "status_code", None)
            if status in RETRY_STATUS:
                logger.warning(
                    "OpenRouter request failed with %s; retrying", status)
                time.sleep(1.5 * attempts)
                continue
            if status in {401, 403}:
                logger.error(
                    "OpenRouter authorization failed (status %s); using fallback output.", status)
                _mark_offline(f"auth_{status}")
            elif status == 404:
                logger.warning(
                    "OpenRouter endpoint returned 404; switching to fallback mode.")
                _mark_offline("endpoint_404")
            elif status:
                logger.error(
                    "OpenRouter request failed with status %s; using fallback.", status)
                _mark_offline(f"status_{status}")
            else:
                logger.error(
                    "OpenRouter network error: %s; using fallback.", exc)
                _mark_offline("network_error")
            break

    return {"success": False, "text": _fallback_text(prompt), "response": None}


def _fallback_text(prompt: str) -> str:
    snippet = prompt.strip().split("\n")[-1][:200]
    return (
        "(offline ghostship placeholder) "
        + snippet.replace("You are", "I'm")
    )
