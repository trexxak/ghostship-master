"""Data models for the forum simulation."""
from __future__ import annotations

from datetime import datetime
import logging

from django.db import models
from django.utils import timezone


logger = logging.getLogger(__name__)


class Agent(models.Model):
    """A simulated forum user ("ghost")."""

    def save(self, *args, **kwargs):
        """Override save to prevent automated actions for trexxak."""
        from forum.lore import ORGANIC_HANDLE
        
        # If this is trexxak, only allow changes to online_status and specific fields
        if self.name and self.name.lower() == ORGANIC_HANDLE.lower():
            if self.pk:  # Existing instance
                allowed_fields = {"online_status", "last_seen_at", "status_expires_at", "updated_at"}
                update_fields = set(kwargs.get('update_fields', []))
                
                # If update_fields is specified, ensure we're only updating allowed fields
                if update_fields and not update_fields.issubset(allowed_fields):
                    extra = update_fields - allowed_fields
                    if extra:
                        logger.debug(
                            "Suppressed automated update of trexxak fields: %s",
                            sorted(extra),
                        )
                    # Only keep allowed fields
                    kwargs['update_fields'] = list(update_fields & allowed_fields)
                    if not kwargs['update_fields']:
                        return  # Skip save if no allowed fields to update
        
        super().save(*args, **kwargs)

    ROLE_ADMIN = "admin"
    ROLE_MODERATOR = "moderator"
    ROLE_MEMBER = "member"
    ROLE_BANNED = "banned"
    ROLE_ORGANIC = "organic"

    STATUS_OFFLINE = "offline"
    STATUS_ONLINE = "online"

    STATUS_CHOICES = [
        (STATUS_OFFLINE, "offline"),
        (STATUS_ONLINE, "online"),
    ]

    ROLE_CHOICES = [
        (ROLE_ADMIN, "admin"),
        (ROLE_MODERATOR, "moderator"),
        (ROLE_MEMBER, "member"),
        (ROLE_BANNED, "banned"),
        (ROLE_ORGANIC, "organic"),
    ]

    name = models.CharField(max_length=50, unique=True)
    archetype = models.CharField(max_length=50)
    traits = models.JSONField(default=dict, blank=True)
    needs = models.JSONField(default=dict, blank=True)
    mood = models.CharField(max_length=20, default="neutral")
    loyalties = models.JSONField(default=dict, blank=True)
    reputation = models.JSONField(default=dict, blank=True)
    triggers = models.JSONField(default=list, blank=True)
    cooldowns = models.JSONField(default=dict, blank=True)
    mind_state = models.JSONField(default=dict, blank=True)
    memory = models.JSONField(default=list, blank=True)
    speech_profile = models.JSONField(default=dict, blank=True)
    suspicion_score = models.FloatField(default=0.0)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    avatar_slug = models.CharField(max_length=120, blank=True)
    online_status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OFFLINE)
    status_expires_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    @property
    def effective_role(self) -> str:
        return getattr(self, "_effective_role", self.role)

    def is_admin(self) -> bool:
        return self.effective_role == self.ROLE_ADMIN

    def is_moderator(self) -> bool:
        return self.effective_role in {self.ROLE_ADMIN, self.ROLE_MODERATOR}

    def is_banned(self) -> bool:
        return self.effective_role == self.ROLE_BANNED

    def is_organic(self) -> bool:
        return self.role == self.ROLE_ORGANIC

    def is_online(self) -> bool:
        return self.online_status == self.STATUS_ONLINE


class Board(models.Model):
    """Top-level board/category that groups threads."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=150, unique=True)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children")
    moderators = models.ManyToManyField("Agent", blank=True, related_name="moderated_boards")
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=100, db_index=True)
    is_garbage = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False, db_index=True)
    visibility_roles = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    @property
    def effective_role(self) -> str:
        return getattr(self, "_effective_role", self.role)

    def delete(self, using=None, keep_parents=False):  # pragma: no cover - soft delete
        if self.is_hidden:
            return 0, {self._meta.label: 0}
        self.is_hidden = True
        self.save(update_fields=["is_hidden"])
        return 1, {self._meta.label: 1}


class Thread(models.Model):
    """A discussion thread."""

    title = models.CharField(max_length=200)
    author = models.ForeignKey("Agent", on_delete=models.CASCADE, related_name="threads")
    board = models.ForeignKey("Board", on_delete=models.SET_NULL, null=True, blank=True, related_name="threads")
    created_at = models.DateTimeField(auto_now_add=True)
    topics = models.JSONField(default=list, blank=True)
    heat = models.FloatField(default=0.0)
    locked = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False, db_index=True)
    visibility_roles = models.JSONField(default=list, blank=True)
    watchers = models.JSONField(default=dict, blank=True)
    pinned = models.BooleanField(default=False, db_index=True)
    pinned_at = models.DateTimeField(null=True, blank=True)
    pinned_by = models.ForeignKey(
        "Agent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pinned_threads",
    )
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    hot_score = models.FloatField(default=0.0)

    class Meta:
        ordering = ["-pinned", "-last_activity_at", "-created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return self.title

    def touch(self, *, activity: datetime | None = None, bump_heat: float = 0.0, auto_save: bool = True) -> None:
        """Update the last-activity timestamp and optional hot score bump."""

        now = activity or timezone.now()
        changed: list[str] = []
        if self.last_activity_at is None or now > self.last_activity_at:
            self.last_activity_at = now
            changed.append("last_activity_at")
        if bump_heat:
            self.hot_score = max(self.hot_score + bump_heat, 0.0)
            changed.append("hot_score")
        if auto_save and changed:
            self.save(update_fields=changed)

    def delete(self, using=None, keep_parents=False):  # pragma: no cover - soft delete
        if self.is_hidden:
            return 0, {self._meta.label: 0}
        self.is_hidden = True
        self.save(update_fields=["is_hidden"])
        return 1, {self._meta.label: 1}


class Post(models.Model):
    """A single post (reply) within a thread."""

    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="posts")
    author = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="posts")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    sentiment = models.FloatField(default=0.0)
    toxicity = models.FloatField(default=0.0)
    quality = models.FloatField(default=0.0)
    votes = models.IntegerField(default=0)
    tick_number = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    is_hidden = models.BooleanField(default=False, db_index=True)
    needs_delta = models.JSONField(default=dict, blank=True)
    authored_by_operator = models.BooleanField(default=False)
    operator_session_key = models.CharField(max_length=64, blank=True)
    operator_ip = models.GenericIPAddressField(null=True, blank=True)
    is_placeholder = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"Post by {self.author} in {self.thread}"

    def delete(self, using=None, keep_parents=False):  # pragma: no cover - soft delete
        if self.is_hidden:
            return 0, {self._meta.label: 0}
        self.is_hidden = True
        self.save(update_fields=["is_hidden"])
        return 1, {self._meta.label: 1}


class PrivateMessage(models.Model):
    """A private message exchanged between two agents."""

    sender = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="sent_messages")
    recipient = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="received_messages")
    content = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    tick_number = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    tone = models.FloatField(default=0.0)
    tie_delta = models.FloatField(default=0.0)
    authored_by_operator = models.BooleanField(default=False)
    operator_session_key = models.CharField(max_length=64, blank=True)
    operator_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["sent_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"PM from {self.sender} to {self.recipient}"


class OrganicInteractionLog(models.Model):
    """Audit trail capturing organic interface activity and guardrails."""

    ACTION_TOGGLE_ON = "toggle_on"
    ACTION_TOGGLE_OFF = "toggle_off"
    ACTION_POST = "post"
    ACTION_DM = "dm"
    ACTION_AUTOMATION_BLOCKED = "automation_blocked"

    ACTION_CHOICES = [
        (ACTION_TOGGLE_ON, "toggle_on"),
        (ACTION_TOGGLE_OFF, "toggle_off"),
        (ACTION_POST, "post"),
        (ACTION_DM, "dm"),
        (ACTION_AUTOMATION_BLOCKED, "automation_blocked"),
    ]

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="organic_logs")
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    thread = models.ForeignKey(
        Thread,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organic_logs",
    )
    recipient = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organic_messages",
    )
    session_key = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    content_preview = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["session_key", "created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.action} @ {self.created_at:%Y-%m-%d %H:%M}"

    @classmethod
    def record(
        cls,
        *,
        agent: Agent,
        action: str,
        request=None,
        thread: Thread | None = None,
        recipient: Agent | None = None,
        content: str | None = None,
        metadata: dict | None = None,
    ) -> "OrganicInteractionLog":
        session_key = ""
        ip_address = None
        user_agent = ""
        if request is not None:
            if hasattr(request, "session"):
                if request.session.session_key is None:
                    request.session.save()
                session_key = request.session.session_key or ""
            meta = getattr(request, "META", {}) or {}
            ip_address = (meta.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip() or meta.get("REMOTE_ADDR")
            user_agent = meta.get("HTTP_USER_AGENT", "")[:255]
        preview = (content or "")[:200]
        return cls.objects.create(
            agent=agent,
            action=action,
            thread=thread,
            recipient=recipient,
            session_key=session_key,
            ip_address=ip_address or None,
            user_agent=user_agent,
            content_preview=preview,
            metadata=metadata or {},
        )


class ModerationTicket(models.Model):
    """Queue entry for moderator attention."""

    STATUS_OPEN = "open"
    STATUS_TRIAGED = "triaged"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_RESOLVED = "resolved"
    STATUS_DISCARDED = "discarded"

    STATUS_CHOICES = [
        (STATUS_OPEN, "open"),
        (STATUS_TRIAGED, "triaged"),
        (STATUS_IN_PROGRESS, "in_progress"),
        (STATUS_RESOLVED, "resolved"),
        (STATUS_DISCARDED, "discarded"),
    ]

    PRIORITY_LOW = "low"
    PRIORITY_NORMAL = "normal"
    PRIORITY_HIGH = "high"

    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "low"),
        (PRIORITY_NORMAL, "normal"),
        (PRIORITY_HIGH, "high"),
    ]

    SOURCE_SYSTEM = "system"
    SOURCE_REPORT = "report"
    SOURCE_MANUAL = "manual"

    SOURCE_CHOICES = [
        (SOURCE_SYSTEM, "system"),
        (SOURCE_REPORT, "report"),
        (SOURCE_MANUAL, "manual"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    reporter = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_tickets",
    )
    reporter_name = models.CharField(max_length=120, blank=True)
    assignee = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )
    thread = models.ForeignKey(
        Thread,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    post = models.ForeignKey(
        "Post",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_SYSTEM, db_index=True)
    tags = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)

    class Meta:
        ordering = ["-priority", "status", "-opened_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"Ticket #{self.id} - {self.title}"

    @property
    def history(self) -> list[dict[str, object]]:
        store = self.metadata if isinstance(self.metadata, dict) else {}
        history = store.get("history") if store else None
        return history if isinstance(history, list) else []

    def _append_history(self, *, actor: Agent | None, to_status: str, note: str = "", from_status: str | None = None, extra: dict[str, object] | None = None) -> None:
        metadata = dict(self.metadata or {})
        history = list(metadata.get("history") or [])
        entry = {
            "ts": timezone.now().isoformat(),
            "actor": getattr(actor, "name", None),
            "from": from_status or self.status,
            "to": to_status,
            "note": note,
        }
        if extra:
            entry.update(extra)
        history.append(entry)
        metadata["history"] = history[-20:]
        self.metadata = metadata

    def mark_resolved(self, *, actor: Agent | None = None, resolution: str = "") -> None:
        previous = self.status
        self.status = self.STATUS_RESOLVED
        now = timezone.now()
        self.closed_at = now
        updates = ["status", "closed_at", "updated_at"]
        if resolution:
            self.resolution = resolution
            updates.append("resolution")
        if actor:
            self.assignee = actor
            updates.append("assignee")
        self._append_history(actor=actor, to_status=self.STATUS_RESOLVED, note=resolution, from_status=previous)
        updates.append("metadata")
        self.save(update_fields=list(dict.fromkeys(updates)))

    def mark_discarded(self, *, actor: Agent | None = None, reason: str = "") -> None:
        previous = self.status
        self.status = self.STATUS_DISCARDED
        now = timezone.now()
        self.closed_at = now
        updates = ["status", "closed_at", "updated_at"]
        if reason:
            self.resolution = reason
            updates.append("resolution")
        if actor:
            self.assignee = actor
            updates.append("assignee")
        self._append_history(actor=actor, to_status=self.STATUS_DISCARDED, note=reason, from_status=previous)
        updates.append("metadata")
        self.save(update_fields=list(dict.fromkeys(updates)))





class ModerationEvent(models.Model):
    """Represents an action taken by a moderator (human or agent)."""

    actor = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, related_name="moderated_actions")
    target_agent = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_events",
    )
    target_post = models.ForeignKey(
        Post,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_events",
    )
    target_thread = models.ForeignKey(
        Thread,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_events",
    )
    ticket = models.ForeignKey(
        ModerationTicket,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    action_type = models.CharField(max_length=50)
    reason = models.TextField(blank=True)
    confidence = models.FloatField(default=0.0)
    metadata = models.JSONField(default=dict, blank=True)
    tick_number = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.action_type} by {self.actor}"




class ThreadWatch(models.Model):
    """Tracks live user sessions observing a thread."""

    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="active_watches")
    session_key = models.CharField(max_length=64)
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="thread_watches")
    user_agent = models.CharField(max_length=255, blank=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("thread", "session_key")
        indexes = [
            models.Index(fields=["session_key"]),
            models.Index(fields=["last_seen"]),
        ]
        ordering = ["-last_seen"]

    def __str__(self) -> str:  # pragma: no cover
        return f"Watch {self.thread_id}::{self.session_key}"


class SessionActivity(models.Model):
    """Per-session heartbeat used for adaptive activity scaling."""

    session_key = models.CharField(max_length=64, unique=True)
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="session_activity")
    acting_as_organic = models.BooleanField(default=False)
    last_path = models.CharField(max_length=255, blank=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen"]
        indexes = [
            models.Index(fields=["last_seen"]),
            models.Index(fields=["acting_as_organic"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Session {self.session_key} @ {self.last_seen:%H:%M:%S}"



class SiteSetting(models.Model):
    """Simple key/value store for runtime configuration."""

    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.key}={self.value}"


class GenerationTask(models.Model):
    """Queue entry for text generation jobs."""

    TYPE_THREAD_START = "thread_start"
    TYPE_REPLY = "reply"
    TYPE_DM = "dm"

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_DEFERRED = "deferred"

    TASK_TYPES = [
        (TYPE_THREAD_START, "thread_start"),
        (TYPE_REPLY, "reply"),
        (TYPE_DM, "dm"),
    ]
    STATUSES = [
        (STATUS_PENDING, "pending"),
        (STATUS_PROCESSING, "processing"),
        (STATUS_COMPLETED, "completed"),
        (STATUS_DEFERRED, "deferred"),
    ]

    task_type = models.CharField(max_length=20, choices=TASK_TYPES)
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_PENDING)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="generation_tasks")
    thread = models.ForeignKey(
        Thread,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_tasks",
    )
    recipient = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_generation_tasks",
    )
    payload = models.JSONField(default=dict, blank=True)
    response_text = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"Task {self.id} ({self.task_type})"


class OpenRouterUsage(models.Model):
    """Tracks daily OpenRouter API usage."""

    day = models.DateField(unique=True)
    request_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"Usage {self.day}: {self.request_count}"


class OracleDraw(models.Model):
    """Stores the random draws (dice and cards) per tick."""

    tick_number = models.PositiveIntegerField(unique=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    rolls = models.JSONField(default=list)
    card = models.CharField(max_length=50, blank=True)
    energy = models.IntegerField(default=0)
    energy_prime = models.IntegerField(default=0)
    alloc = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["tick_number"]

    def __str__(self) -> str:  # pragma: no cover
        return f"Tick {self.tick_number} Oracle"


class TickLog(models.Model):
    """Append-only log of events executed during a simulation tick."""

    tick_number = models.PositiveIntegerField(unique=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    events = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["tick_number"]

    def __str__(self) -> str:  # pragma: no cover
        return f"TickLog {self.tick_number}"


class Goal(models.Model):
    """Unified catalogue for missions, achievements, and progress milestones."""

    TYPE_PROGRESS = "progress"
    TYPE_MISSION = "mission"
    TYPE_BADGE = "badge"

    TYPE_CHOICES = [
        (TYPE_PROGRESS, "progress"),
        (TYPE_MISSION, "mission"),
        (TYPE_BADGE, "badge"),
    ]

    STATUS_BACKLOG = "backlog"
    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = [
        (STATUS_BACKLOG, "backlog"),
        (STATUS_ACTIVE, "active"),
        (STATUS_COMPLETED, "completed"),
    ]

    slug = models.SlugField(max_length=220, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    goal_type = models.CharField(max_length=16, choices=TYPE_CHOICES, default=TYPE_BADGE)
    category = models.CharField(max_length=64, default="general", blank=True)
    emoji = models.CharField(max_length=16, blank=True)
    icon_slug = models.CharField(max_length=150, blank=True)
    priority = models.IntegerField(default=100, db_index=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    is_global = models.BooleanField(default=False)
    target = models.FloatField(default=1.0)
    progress_current = models.FloatField(default=0.0)
    telemetry_rules = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "name"]
        indexes = [
            models.Index(fields=["goal_type", "priority"]),
            models.Index(fields=["status", "goal_type"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    @property
    def effective_role(self) -> str:
        return getattr(self, "_effective_role", self.role)

    @property
    def completion_ratio(self) -> float:
        if self.target <= 0:
            return 1.0
        return min(max(self.progress_current / self.target, 0.0), 1.0)

    @property
    def completion_percent(self) -> float:
        return round(self.completion_ratio * 100.0, 2)


class GoalProgress(models.Model):
    """Progress deltas recorded against global goals."""

    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name="progress_entries")
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="goal_progress")
    tick_number = models.PositiveIntegerField(null=True, blank=True)
    delta = models.FloatField(default=0.0)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.goal.slug}: {self.delta:+}"


class AgentGoal(models.Model):
    """Per-agent progress towards goals (achievements, progress arc milestones)."""

    SOURCE_SYSTEM = "system"
    SOURCE_GOAL = "goal"
    SOURCE_REFEREE = "referee"
    SOURCE_MANUAL = "manual"

    SOURCE_CHOICES = [
        (SOURCE_SYSTEM, "system"),
        (SOURCE_GOAL, "goal_engine"),
        (SOURCE_REFEREE, "progress_referee"),
        (SOURCE_MANUAL, "manual"),
    ]

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="goal_states")
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name="agent_states")
    progress = models.FloatField(default=0.0)
    unlocked_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    source_post = models.ForeignKey(
        "Post",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="goal_unlocks",
    )
    awarded_by = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_SYSTEM)
    referee_trace_id = models.CharField(max_length=64, blank=True)
    rationale = models.TextField(blank=True)

    class Meta:
        unique_together = ("agent", "goal")
        ordering = ["-unlocked_at", "agent_id"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.agent.name} :: {self.goal.name}"


class GoalEvaluation(models.Model):
    """Log of referee decisions over batches of simulation ticks."""

    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "pending"),
        (STATUS_COMPLETED, "completed"),
        (STATUS_FAILED, "failed"),
    ]

    batch_label = models.CharField(max_length=64, unique=True)
    tick_numbers = models.JSONField(default=list, blank=True)
    alias = models.CharField(max_length=64, default="progress-eval")
    model_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    duration_ms = models.PositiveIntegerField(default=0)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.alias} [{self.batch_label}]"


class LoreEvent(models.Model):
    """Scheduled lore beats to enact at specific tick numbers."""

    key = models.CharField(max_length=128, unique=True)
    kind = models.CharField(max_length=32)
    tick = models.PositiveIntegerField(db_index=True)
    meta = models.JSONField(default=dict, blank=True)
    window = models.JSONField(default=dict, blank=True)
    processed_tick = models.PositiveIntegerField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tick", "key"]

    def __str__(self) -> str:  # pragma: no cover
        status = "processed" if self.processed_at else "pending"
        return f"{self.key}@{self.tick} ({status})"



