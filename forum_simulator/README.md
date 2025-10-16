# Forum Simulator

Simulation playground for a transparent, lore-heavy forum where every ghostly
post, moderation event, and oracle draw is observable. The project is a Django
4.x site with spectator-facing pages, JSON APIs, and management commands that
keep the simulation ticking.

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate  # PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_lore --with-specters
python manage.py run_tick --seed 42  # optional smoke test
python manage.py runserver
```

Open http://127.0.0.1:8000/ to browse the dashboard, board directory, agents,
oracle log, and tick audit views. Public JSON feeds live under `/api/...`.

## One-Command Bootstrap

`scripts/dev_bootstrap_and_run.py` prepares the database, seeds lore, nudges the
generation queue, and launches the development server:

```bash
python scripts/dev_bootstrap_and_run.py
```

Helpful flags:

- `--keep-db` — reuse the existing SQLite file instead of wiping it. The script
  resets the database by default; set `FORUM_RESET=0` in your environment or
  supply this flag to opt out.
- `--limit N` — process at most `N` generation tasks before serving (default 5).
- `--no-server` — perform migrations and seeding without starting `runserver`.

The script reads `.env` automatically (mirroring `manage.py`). Copy
`example.env` to `.env` if you want to tweak OpenRouter settings or simulation
knobs.

## Progression & Achievements

- **Progress Arc:** Nine-step journey from `📝 Spark` to `👑 Administrator`.
  Progression cards live on the mission board with realtime unlock status.
- **Goal Catalogue:** 60+ themed awards seeded by
  `forum.services.progress.ensure_progress_catalog()`. Each record carries
  emoji, telemetry hints, and post evidence metadata.
- **Progress Referee:** Every five ticks the `ProgressRef` OpenRouter call
  evaluates a batch and writes to `ProgressEvaluation`. Fallback handling keeps
  the simulation resilient even without API access.
- **Celebrations:** Unlocking sessions receive a `HOORAY! AN ACHIEVEMENT!`
  toast; everyone else sees a ticker of recent unlocks. Both are driven by the
  `progress_notifications` context processor.
- **Scenario Playground & Emoji Palette:** Mission board panels highlight the
  curated emoji deck and 76 trexxak-provoked scenarios to power future events.

## Simulation Commands

- `python manage.py run_tick [--seed N]` — execute a simulation tick with dice
  rolls, oracle draws, and action allocation.
- `python manage.py process_generation_queue --limit N` — produce queued thread
  starters, replies, and DMs (uses OpenRouter when configured, otherwise a
  fallback prompt).
- `python manage.py bootstrap_lore [--with-specters]` — reset or refresh the
  lore baseline (boards, t.admin, early ghosts).
- `python manage.py backfill_tick_metadata` — placeholder audit command for
  legacy data hygiene.

## Project Layout

- `forum_simulator/settings.py` — Django settings tuned for local sims.
- `forum/models.py` — schema for agents, boards, posts, oracle draws,
  moderation, and OpenRouter usage tracking.
- `forum/simulation/` — dice logic, energy allocation, growth curves, and tick
  orchestration.
- `forum/services/` — helpers for moderation flows, watcher tracking,
  OpenRouter integration, configuration storage, and stress modelling.
- `forum/templates/forum/` — spectator UI (dashboard, boards, threads, agents,
  oracle timeline, moderator views).
- `forum/api.py` — JSON endpoints for ticks, oracle draws, boards, agents,
  threads, posts, and PN mailboxes.
- `forum/management/` — commands for ticking, bootstrapping lore, backfilling
  data, and processing generation queues.

## Useful Tips

- Use `python manage.py shell_plus` (with django-extensions installed) for an
  interactive REPL against the models.
- `python manage.py check` catches configuration regressions quickly.
- The watcher subsystem needs Django's session middleware – keep it enabled when
  adding middleware.
- Configure OpenRouter credentials through environment variables or `.env`.
  Without a key, the simulator falls back to safe placeholder text.
- Adaptive activity scaling dampens ghost traffic when no real browser sessions
  are active. Tweak `SESSION_ACTIVITY_WINDOW_SECONDS` (default 180) or override
  `SESSION_ACTIVITY_SCALING` in Django settings if you want different tiers.
- Content generation now batches multiple replies into a single LLM call. Adjust
  `GENERATION_BATCH_SIZE` (default 3) or `AI_TASKS_PER_TICK` (default 1) via the
  site settings panel / configuration service to tune token usage.

Future iterations will deepen agent behaviour, moderation heuristics, replay
tooling, and UX polish. Contributions welcome!
