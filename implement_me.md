# Urgent Roadmap Follow-Ups

The latest fitness check surfaced several roadmap items that remain unimplemented yet continue to block a "feature ready" milestone. Future agents updating `AGENTS.md` or working on the next pass should prioritise shipping the following capabilities before tackling new surface polish:

1. **Automated Tick Scheduling**
   - Integrate Celery Beat (or an equivalent scheduler) so simulation ticks run automatically without manual intervention.
   - Pair the scheduler with override-friendly manual controls to respect the existing operations plan outlined in `forum_simulator/todo.md`.

2. **Agent State Progression**
   - Implement the per-tick need drift, mood updates, suspicion/reputation adjustments, and action scoring heuristics promised in the agent backlog.
   - Enrich the agent factory with archetype templates so new agents spawn with meaningful starting traits.

3. **Transparency & Replay Tooling**
   - Build the oracle canvas and replay scrubber so operators can audit runs and explain narrative outcomes.
   - Backfill tick numbers, seeds, and detailed decision traces to support reliable replays.

4. **Simulation Configuration Surface**
   - Load core tuning knobs (cooldowns, needs drift, oracle decks) from TOML/JSON so environments can be reconfigured without code edits.
   - Persist and expose PRNG seeds to guarantee reproducible sessions across deployments.

When amending `AGENTS.md`, call out these obligations explicitly so the next maintainer understands that the "huge bugfix pass" is incomplete without them.
