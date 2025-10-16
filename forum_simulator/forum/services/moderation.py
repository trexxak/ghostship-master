from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from forum.models import Agent, Board, ModerationEvent, ModerationTicket, Post, Thread
from forum.services import stress


def _assert_can_moderate(actor: Agent) -> None:
    if not actor.is_moderator():
        raise PermissionError("Actor lacks moderation privileges")


def _maybe_to_garbage(thread: Thread) -> Board | None:
    if thread.board and getattr(thread.board, "is_garbage", False):
        return thread.board
    return Board.objects.filter(is_garbage=True).order_by("position").first()


def _should_auto_archive(reason: str, thread: Thread) -> bool:
    if thread.board and getattr(thread.board, "is_garbage", False):
        return False
    reason_lower = (reason or "").lower()
    if any(keyword in reason_lower for keyword in ("troll", "garbage", "archive", "resolved", "cleanup")):
        return True
    if thread.heat is not None and thread.heat < 0:
        return True
    return False


def _actor_role(actor: Agent | None) -> str | None:
    return getattr(actor, "role", None) if actor else None


def _event_metadata(actor: Agent | None, ticket: ModerationTicket | None = None, **extra: object) -> dict[str, object]:
    metadata: dict[str, object] = {"actor_role": _actor_role(actor)}
    if ticket is not None:
        metadata.update(
            {
                "ticket_id": ticket.id,
                "ticket_status": ticket.status,
                "ticket_priority": ticket.priority,
                "ticket_source": ticket.source,
            }
        )
    metadata.update(extra)
    return metadata


def _ticket_status_history(ticket: ModerationTicket, *, actor: Agent | None, to_status: str, note: str = "", previous: str | None = None, extra: dict[str, object] | None = None) -> None:
    ticket._append_history(actor=actor, to_status=to_status, note=note, from_status=previous, extra=extra)


@transaction.atomic
def update_ticket_status(
    actor: Agent | None,
    ticket: ModerationTicket,
    *,
    status: str,
    reason: str = "",
) -> ModerationEvent:
    previous = ticket.status
    ticket.status = status
    updates = ["status", "updated_at"]
    now = timezone.now()

    if status in {ModerationTicket.STATUS_RESOLVED, ModerationTicket.STATUS_DISCARDED}:
        ticket.closed_at = now
        updates.append("closed_at")
        if reason:
            ticket.resolution = reason
            updates.append("resolution")
        if actor:
            ticket.assignee = actor
            updates.append("assignee")
    elif status == ModerationTicket.STATUS_IN_PROGRESS and actor:
        ticket.assignee = actor
        updates.append("assignee")

    _ticket_status_history(ticket, actor=actor, to_status=status, note=reason, previous=previous)
    updates.append("metadata")
    ticket.save(update_fields=list(dict.fromkeys(updates)))

    event = ModerationEvent.objects.create(
        actor=actor,
        ticket=ticket,
        action_type=f"ticket-status:{status}",
        reason=reason,
        confidence=1.0,
        metadata=_event_metadata(actor, ticket, previous_status=previous, note=reason),
    )

    if ticket.source == ModerationTicket.SOURCE_REPORT:
        if status == ModerationTicket.STATUS_RESOLVED:
            stress.record_report_feedback(ticket, actor=actor, resolved=True, note=reason)
        elif status == ModerationTicket.STATUS_DISCARDED:
            stress.record_report_feedback(ticket, actor=actor, resolved=False, note=reason)
        elif status in {ModerationTicket.STATUS_TRIAGED, ModerationTicket.STATUS_IN_PROGRESS}:
            stress.adjust_frustration(ticket.reporter, -0.05)
            stress.adjust_admin_stress(-0.01)

    stress.backlog_pressure()
    return event


@transaction.atomic
def assign_ticket(
    actor: Agent | None,
    ticket: ModerationTicket,
    *,
    assignee: Agent | None,
    note: str = "",
) -> ModerationEvent:
    previous = ticket.assignee.name if ticket.assignee else None
    ticket.assignee = assignee
    _ticket_status_history(
        ticket,
        actor=actor,
        to_status=ticket.status,
        note=note,
        extra={"assign_to": getattr(assignee, "name", None), "previous_assignee": previous},
    )
    ticket.save(update_fields=["assignee", "metadata", "updated_at"])

    event = ModerationEvent.objects.create(
        actor=actor,
        ticket=ticket,
        action_type="ticket-assign",
        reason=note,
        confidence=1.0,
        metadata=_event_metadata(
            actor,
            ticket,
            previous_assignee=previous,
            new_assignee=getattr(assignee, "name", None),
            note=note,
        ),
    )

    stress.adjust_admin_stress(-0.01)
    return event


@transaction.atomic
def lock_thread(
    actor: Agent,
    thread: Thread,
    *,
    reason: str = "",
    ticket: ModerationTicket | None = None,
    send_to_garbage: bool = False,
) -> ModerationEvent:
    _assert_can_moderate(actor)
    if thread.locked:
        return ModerationEvent.objects.create(
            actor=actor,
            target_thread=thread,
            ticket=ticket,
            action_type="lock-thread",
            reason=reason or "Thread already locked",
            confidence=1.0,
            metadata=_event_metadata(actor, ticket, locked=True),
        )

    thread.locked = True
    thread.touch()
    thread.save(update_fields=["locked", "last_activity_at"])

    archive_on_lock = send_to_garbage or _should_auto_archive(reason, thread)
    if archive_on_lock:
        garbage_board = _maybe_to_garbage(thread)
        if garbage_board and thread.board_id != garbage_board.id:
            previous_board = thread.board
            thread.board = garbage_board
            thread.save(update_fields=["board"])
            return ModerationEvent.objects.create(
                actor=actor,
                target_thread=thread,
                ticket=ticket,
                action_type="lock-thread",
                reason=reason,
                confidence=1.0,
                metadata=_event_metadata(
                    actor,
                    ticket,
                    moved_from=getattr(previous_board, "slug", None),
                    moved_to=garbage_board.slug,
                    auto_archive=True,
                ),
            )

    return ModerationEvent.objects.create(
        actor=actor,
        target_thread=thread,
        ticket=ticket,
        action_type="lock-thread",
        reason=reason,
        confidence=1.0,
        metadata=_event_metadata(actor, ticket, auto_archive=archive_on_lock),
    )


@transaction.atomic
def unlock_thread(
    actor: Agent,
    thread: Thread,
    *,
    reason: str = "",
    ticket: ModerationTicket | None = None,
) -> ModerationEvent:
    _assert_can_moderate(actor)
    thread.locked = False
    thread.touch()
    thread.save(update_fields=["locked", "last_activity_at"])
    return ModerationEvent.objects.create(
        actor=actor,
        action_type="unlock-thread",
        target_thread=thread,
        ticket=ticket,
        reason=reason,
        confidence=1.0,
        metadata=_event_metadata(actor, ticket),
    )


@transaction.atomic
def delete_post(
    actor: Agent,
    post: Post,
    *,
    reason: str = "",
    ticket: ModerationTicket | None = None,
) -> ModerationEvent:
    _assert_can_moderate(actor)
    thread = post.thread
    post.delete()
    thread.heat = max(0.0, (thread.heat or 0.0) - 1.0)
    thread.touch()
    thread.save(update_fields=["heat", "last_activity_at"])
    return ModerationEvent.objects.create(
        actor=actor,
        target_post=None,
        target_agent=post.author,
        target_thread=thread,
        ticket=ticket,
        action_type="delete-post",
        reason=reason,
        confidence=0.9,
        metadata=_event_metadata(actor, ticket, post_author=post.author.name if post.author_id else None),
    )


@transaction.atomic
def move_thread(
    actor: Agent,
    thread: Thread,
    *,
    destination: Board,
    reason: str = "",
    ticket: ModerationTicket | None = None,
) -> ModerationEvent:
    _assert_can_moderate(actor)
    previous_board = thread.board
    thread.board = destination
    thread.touch()
    thread.save(update_fields=["board", "last_activity_at"])
    return ModerationEvent.objects.create(
        actor=actor,
        target_thread=thread,
        ticket=ticket,
        action_type="move-thread",
        reason=reason,
        confidence=0.9,
        metadata=_event_metadata(
            actor,
            ticket,
            moved_from=getattr(previous_board, "slug", None),
            moved_to=destination.slug,
        ),
    )


@transaction.atomic
def pin_thread(
    actor: Agent,
    thread: Thread,
    *,
    reason: str = "",
    ticket: ModerationTicket | None = None,
) -> ModerationEvent:
    _assert_can_moderate(actor)
    thread.pinned = True
    thread.pinned_at = timezone.now()
    thread.pinned_by = actor
    thread.touch(bump_heat=1.0, auto_save=False)
    thread.save(update_fields=["pinned", "pinned_at", "pinned_by", "last_activity_at", "hot_score"])
    return ModerationEvent.objects.create(
        actor=actor,
        target_thread=thread,
        ticket=ticket,
        action_type="pin-thread",
        reason=reason,
        confidence=1.0,
        metadata=_event_metadata(actor, ticket),
    )


@transaction.atomic
def unpin_thread(
    actor: Agent,
    thread: Thread,
    *,
    reason: str = "",
    ticket: ModerationTicket | None = None,
) -> ModerationEvent:
    _assert_can_moderate(actor)
    thread.pinned = False
    thread.pinned_by = None
    thread.touch(bump_heat=0.0, auto_save=False)
    thread.save(update_fields=["pinned", "pinned_by", "last_activity_at"])
    return ModerationEvent.objects.create(
        actor=actor,
        target_thread=thread,
        ticket=ticket,
        action_type="unpin-thread",
        reason=reason,
        confidence=1.0,
        metadata=_event_metadata(actor, ticket),
    )


@transaction.atomic
def set_agent_role(
    actor: Agent,
    target: Agent,
    *,
    role: str,
    reason: str = "",
    ticket: ModerationTicket | None = None,
) -> ModerationEvent:
    if not actor.is_admin():
        raise PermissionError("Only admins can set roles")
    if role not in {Agent.ROLE_ADMIN, Agent.ROLE_MODERATOR, Agent.ROLE_MEMBER, Agent.ROLE_BANNED, Agent.ROLE_ORGANIC}:
        raise ValueError("Invalid role")
    previous = target.role
    target.role = role
    target.save(update_fields=["role", "updated_at"])
    return ModerationEvent.objects.create(
        actor=actor,
        target_agent=target,
        ticket=ticket,
        action_type=f"set-role:{role}",
        reason=reason,
        confidence=1.0,
        metadata=_event_metadata(actor, ticket, previous_role=previous, new_role=role),
    )
