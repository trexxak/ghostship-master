from __future__ import annotations

from django.core.management.base import BaseCommand

from forum.services import tick_control


class Command(BaseCommand):
    help = "Queue a manual tick override for the scheduled runner."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--seed", type=int, default=None, help="Optional RNG seed to replay the tick.")
        parser.add_argument("--oracle-card", dest="oracle_card", default=None, help="Force a specific oracle card slug.")
        parser.add_argument(
            "--energy-multiplier",
            type=float,
            default=None,
            help="Optional multiplier applied to the tick's modulated energy.",
        )
        parser.add_argument("--force", action="store_true", help="Override the freeze toggle for the queued tick.")
        parser.add_argument("--note", default="", help="Operator note recorded alongside the override.")
        parser.add_argument(
            "--origin",
            default="manual-override",
            help="Label recorded as the tick origin when the scheduler executes.",
        )

    def handle(self, *args, **options):
        payload = tick_control.queue_manual_override(
            seed=options.get("seed"),
            oracle_card=options.get("oracle_card"),
            energy_multiplier=options.get("energy_multiplier"),
            force=options.get("force"),
            note=options.get("note"),
            origin=options.get("origin"),
        )
        self.stdout.write(self.style.SUCCESS(f"Queued tick override: {payload}"))
