# Ghostship Active Roadmap

This document consolidates the outstanding feature and infrastructure work across the simulation. It replaces the older `forum_simulator/todo.md` and `implement_me.md` lists—refer to this file for the canonical backlog.

## Highest-Priority Initiatives
- **Automated tick scheduling and manual overrides:** Integrate Celery Beat (or an equivalent scheduler) so ticks run on a cadence, while retaining override-friendly manual controls for operations staff.
- **Agent state progression:** Deliver per-tick need drift, mood updates, suspicion/reputation adjustments, and action scoring heuristics backed by richer archetype templates for spawning agents.
- **Transparency & replay tooling:** Build the oracle canvas and replay scrubber, backfill tick numbers and seeds, and capture detailed decision traces to support deterministic replays and post-mortems.
- **Simulation configuration & reproducibility:** Load key tuning knobs (cooldowns, needs drift, oracle decks) from external config, allow runtime deck swaps and transparency toggles, and persist/expose PRNG seeds for reproducible sessions.

## Governance & Community Experience
- Automate `t.admin` promotions so reliable agents graduate into moderators on a predictable cadence.
- Apply role-based styling (purple admin, green moderator, grey banned) and refresh bulletin layouts to improve readability and trust signals.
- Penalise double posts and other netiquette slips by increasing `t.admin` stress and mistake odds.
- Increase automated moderation/admin workload during high-stress periods to keep staff activity visible.

## Simulation Core & Agent Behaviour
- Wire cooldown management and tick scheduling so agents honour post/thread/DM/report cooldown windows.
- Update loyalties and faction graphs from PN and reply exchanges to reflect relationship shifts.

## Agent Factory & Content Generation
- Enrich the agent factory with archetype templates (traits, triggers, initial needs, loyalty seeds).
- Provide generators for thread titles, post bodies, and PN text that align with agent archetypes.
- Record `needs_delta` and other state deltas for every action to support analytics and transparency.

## Oracle & Event Systems
- Implement a configurable oracle deck loader and execute card effects during ticks.
- Log and surface rare events (Omen spikes, Seance high-energy ticks, Graveyard archives) in both transparency dashboards and moderation overlays.

## Data Backfill & Replay Infrastructure
- Backfill `tick_number` on posts, messages, and moderation events, and restore board assignments on legacy content.
- Expand `TickLog` to capture full decision traces (inputs, chosen agents, rationale) and store deterministic seeds/hashes per tick.
- Ship a management command or script that replays ticks for debugging.

## Transparency UX & API
- Ship richer agent profile pages with needs/mood graphs, action timelines, and PN mailbox views (read-only).
- Extend thread views with post metadata (needs deltas, motivation, dice paths) and moderation overlays.
- Provide PN conversation viewers with loyalty deltas, moderation timelines with sanctions history, and a faction force-graph derived from PN/reply networks.
- Deliver the oracle canvas component (live & replay) with dice animation, card art, allocation bars, and replay scrubber controls.

## Public APIs & Live Updates
- Expose `/oracle/ticks` and `/ghosts/:id/dm-mirror` endpoints that match the transparency specification.
- Add a WebSocket or SSE channel that broadcasts tick results for real-time UI updates.

## Scheduling & Background Operations
- Handle background tasks for heavy analytics (faction graph generation, heat decay) asynchronously.

## Observability & Operations Dashboards
- Add admin/staff dashboards for inspecting models and tuning parameters safely.
- Build metrics collection for per-minute stats (agent counts, heat averages) to improve monitoring.

## Quality Engineering & Documentation
- Write unit and integration tests for models, the tick command, and action selection.
- Add fixtures/factories for sample agents and threads.
- Expand the README with architecture overviews, transparency guarantees, and UI usage guides.
- Document deployment steps (dependencies, static asset build, worker setup).
