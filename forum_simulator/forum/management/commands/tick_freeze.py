from __future__ import annotations

from django.core.management.base import BaseCommand

from forum.services import tick_control


class Command(BaseCommand):
    help = "Freeze or resume automatic tick accumulation while containers keep running."

    def add_arguments(self, parser) -> None:  # pragma: no cover - CLI wiring
        group = parser.add_mutually_exclusive_group(required=False)
        group.add_argument("--on", action="store_true", help="Freeze ticks until released.")
        group.add_argument("--off", action="store_true", help="Release a freeze.")
        group.add_argument("--toggle", action="store_true", help="Flip the current state.")
        parser.add_argument("--reason", help="Optional operator note recorded with the state change.")
        parser.add_argument("--actor", help="Identifier recorded with the freeze event.")
        parser.add_argument("--status", action="store_true", help="Only display current freeze state.")

    def handle(self, *args, **options) -> None:
        actor = options.get("actor")
        reason = options.get("reason")
        if options.get("status"):
            state = tick_control.describe_state()
            label = tick_control.state_label()
            self.stdout.write(self.style.SUCCESS(f"Freeze state: {label}"))
            if state.get("toggled_at"):
                self.stdout.write(f"  toggled_at: {state['toggled_at']}")
            return
        if options.get("on"):
            tick_control.freeze(actor=actor, reason=reason)
            self.stdout.write(self.style.WARNING(f"Ticks frozen ({tick_control.state_label()})"))
            return
        if options.get("off"):
            state = tick_control.unfreeze(actor=actor, note=reason)
            self.stdout.write(self.style.SUCCESS(f"Ticks unfrozen by {state.get('actor') or 'unknown'}"))
            return
        state = tick_control.toggle(actor=actor, reason=reason)
        color = self.style.WARNING if state.get("frozen") else self.style.SUCCESS
        self.stdout.write(color(f"Toggled freeze -> {tick_control.state_label()}"))
