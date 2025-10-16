from __future__ import annotations

from django.core.management.base import BaseCommand

from forum.models import Post, PrivateMessage, ModerationEvent, Thread


class Command(BaseCommand):
    help = "Inspect or backfill tick metadata and board assignments (placeholder)."

    def add_arguments(self, parser):  # pragma: no cover - CLI plumbing
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Attempt to write backfill changes (not yet implemented).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        missing_posts = Post.objects.filter(tick_number__isnull=True).count()
        missing_messages = PrivateMessage.objects.filter(tick_number__isnull=True).count()
        missing_mods = ModerationEvent.objects.filter(tick_number__isnull=True).count()
        unassigned_threads = Thread.objects.filter(board__isnull=True).count()

        self.stdout.write("Backfill audit")
        self.stdout.write(f"  posts without tick_number: {missing_posts}")
        self.stdout.write(f"  private messages without tick_number: {missing_messages}")
        self.stdout.write(f"  moderation events without tick_number: {missing_mods}")
        self.stdout.write(f"  threads without board: {unassigned_threads}")

        if apply_changes:
            self.stdout.write(self.style.WARNING("Backfill apply mode is not implemented yet. Implement the heuristics before running with --apply."))
        else:
            self.stdout.write("Run again with --apply once the backfill strategy is implemented.")
