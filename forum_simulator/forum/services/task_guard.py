from __future__ import annotations

import logging
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from forum.models import Agent, GenerationTask
from forum.lore import ORGANIC_HANDLE

logger = logging.getLogger(__name__)

def enqueue_generation_task(
    kind: str,
    agent: Optional[Agent] = None,
    context: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> Optional[GenerationTask]:
    """
    Create a new generation task with the given parameters.
    
    Args:
        kind: The type of task to generate
        agent: The agent to generate content for
        context: Additional context for the generation
        **kwargs: Additional task parameters
    
    Returns:
        The created task, or None if creation failed
    """
    # Prevent automated trexxak actions
    if agent and agent.name.lower() == ORGANIC_HANDLE.lower():
        logger.warning(
            "Prevented automated action for trexxak: %s", 
            {"kind": kind, "context": context}
        )
        return None

    with transaction.atomic():
        task = GenerationTask.objects.create(
            kind=kind,
            agent=agent,
            context=context or {},
            **kwargs
        )
        return task