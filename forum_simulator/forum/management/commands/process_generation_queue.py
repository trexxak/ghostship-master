from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from forum.services.generation import process_generation_queue


class Command(BaseCommand):
    help = "Execute pending GenerationTask items against OpenRouter (or fallback generation)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10,
                            help="Maximum tasks to process this run (default: 10).")

    def handle(self, *args, **options):
        limit_raw = options.get('limit')
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            raise CommandError('Limit must be a positive integer.') from None
        if limit <= 0:
            raise CommandError('Limit must be positive.')
        processed, deferred = process_generation_queue(limit=limit)
        self.stdout.write(self.style.SUCCESS(
            f"Processed {processed} tasks; {deferred} deferred/remaining."
        ))
