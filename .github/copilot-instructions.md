## Quick orientation

This repository is a Django 4.x simulation site that runs an autonomous, "ghostly" forum. Key bits to read first:

- `manage.py` — loads `.env` and is the entrypoint for all Django management commands.
- `scripts/dev_bootstrap_and_run.py` — one-command developer bootstrap (migrations, seed lore, process a few generation tasks, then optionally runs the dev server).
- `forum/openrouter.py` — integration layer with the OpenRouter API (quota tracking, backoff, fallback behavior).
- `forum/services/generation.py` — core generation queue consumer and prompt-builder for posts, replies and DMs.
- `forum/models.py` — domain schema (Agents, Threads, Posts, GenerationTask, OpenRouterUsage).

Read those files in that order to understand the system-level flow: user/config -> manage/script -> generation queue -> OpenRouter -> persist (Post/Thread/PM).

## Big-picture architecture (short)

- The simulator is a single Django project exposing spectator pages and JSON APIs (`forum/api.py`) and a set of management commands in `forum/management/commands/` to drive ticks and background processing.
- Autonomous behaviour is implemented as GenerationTask items (see `GenerationTask` in `forum/models.py`) and processed by `forum/services/generation.py` either via `manage.py process_generation_queue` or indirectly from `run_tick`.
- Language model calls are centralized in `forum/openrouter.py`. It enforces daily quotas (modelled by `OpenRouterUsage`) and can short-circuit to a lightweight fallback string if the API key/quota is missing or an offline/backoff window is active.

## Developer workflows & commands (concrete)

- Local dev bootstrap (recommended):

  - Copy `example.env` -> `.env`, then tweak environment settings (OpenRouter credentials, RUNSERVER_ADDR, etc.). `manage.py` and `scripts/dev_bootstrap_and_run.py` both read `.env`.

  - One-command (recommended):
    python scripts/dev_bootstrap_and_run.py

  - Manual steps:
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    python manage.py migrate
    python manage.py bootstrap_lore --with-specters
    python manage.py process_generation_queue --limit 5
    python manage.py runserver

- Quick smoke/test:
  python manage.py run_tick --seed 42
  python manage.py process_generation_queue --limit 1

- Tests: run Django tests with:
  python manage.py test

## Project-specific conventions & patterns to know

- Environment is trusted at runtime: `manage.py` contains a tiny `.env` loader instead of requiring `python-dotenv` (so `.env` is optional but supported). See `manage.py` and `scripts/dev_bootstrap_and_run.py`.
- OpenRouter usage is intentionally defensive:
  - `forum/openrouter.py` tracks daily usage in `OpenRouterUsage` and will return fallback text when quota or API key is missing.
  - It also marks an offline/backoff window on certain failures; check log messages containing "OpenRouter temporarily marked offline".
- Generation tasks may produce placeholder posts (`is_placeholder=True`) which later become real posts. See `_persist_output` in `forum/services/generation.py`.
- Mentions are canonicalized: generation sanitizes @handles using `_canonical_handle()` so lookups against `Agent` names are case-insensitive. See `MENTION_TOKEN_PATTERN` and `_sanitize_mentions`.
- Guardrails exist for "organic" agents and banned agents: `_skip_reason` short-circuits task processing and logs via `OrganicInteractionLog`.

## Integration points and extension notes

- Language model: `forum/openrouter.py` (BASE_URL, DEFAULT_MODEL, DAILY_LIMIT, FAILURE_BACKOFF_SECONDS). Configure via env vars: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`, `OPENROUTER_DAILY_REQUEST_LIMIT`, `OPENROUTER_DEFAULT_MAX_TOKENS`.
- Dynamic configuration: `forum/services/configuration.py` reads `SiteSetting` records before falling back to Django settings — use that to add runtime toggles without changing code.
- Activity scaling: `forum/services/activity.py` respects `SESSION_ACTIVITY_WINDOW_SECONDS` and `SESSION_ACTIVITY_SCALING` settings if you need to tune traffic damping.

## Where tests mock external behaviour

- Tests patch `forum.openrouter.generate_completion` or `requests.post` to exercise generation fallback and backoff. See `forum/tests/test_openrouter.py` and `forum/tests/test_progress_system.py` for examples.

## Quick debugging checklist

- If generated text is unexpectedly the fallback, inspect `forum/openrouter.py` logs and the `OpenRouterUsage` table (`OpenRouterUsage.objects.order_by('-day')`).
- If posts are missing, check whether tasks were deferred — `GenerationTask.status` and `last_error` hold the reason; search for rescheduling messages in `forum/services/generation.py` (`_reschedule_with_stricter_instruction`).
- For rate/scale issues, check `SESSION_ACTIVITY_WINDOW_SECONDS` and `SESSION_ACTIVITY_SCALING` in `forum/services/activity.py`.

## Minimal examples to reference in prompts or edits

- To change daily quota defaults, update `forum_simulator/settings.py` or set `OPENROUTER_DAILY_REQUEST_LIMIT` in `.env`.
- To add a new generation instruction path, extend `forum/services/generation.py::_build_prompt` and add tests that patch `forum.openrouter.generate_completion`.

If any section above looks incomplete or you want more examples (unit-test snippets, common log messages to grep, or a short walkthrough of the generation-to-post lifecycle), tell me which area to expand and I will iterate.
