# TODO Before Feature-Ready

## Iteration 2: Bulletin Board Cohesion

### Content & Thread Quality
- [x] Ensure the thread generator produces substantial body text using non-technical theme packs that feel relatable.
- [x] Seed guaranteed replies for new threads and schedule follow-up replies based on heat/hotness so boards never look empty.

### Board Mechanics
- [x] Introduce pinned thread metadata, CRUD, and board-level ordering controls in the bulletin-board view.
- [x] Implement thread bubbling logic that reorders listings based on recent replies, heat score, and moderation state.
- [x] Stand up a `Garbage` board with move/archive workflow and filters to keep primary boards clean.

### Moderation Systems
- [x] Ship moderation ticket model, queue UI, and state machine (open/in-progress/resolved/discarded).
- [x] Emit moderation events (moves, locks, closures) to the timeline/log and surface them in thread + dashboard overlays.
- [x] Automate moving trolls/closed threads into the `Garbage` board through moderator tools.

### Presence & Watchers
- [x] Restore the "is watching" footer in bulletin-board threads using the watcher service.
- [x] Refresh watcher lists on focus change and enforce a single active watch per user.
- [x] Add a `/who` presence route that shows online users and their current board/thread watch targets.

### Community UX
- [x] Enable username click-through to profile popovers/pages with quick actions (message, view history).
- [x] Implement reply quoting with `[username]`/`@username` mention linking back to user profiles.
- [x] Expand the transparency dashboard with board filters, moderation overview widgets, and navigation shortcuts.

## Iteration 3: Governance & Moderation Blitz

- [x] Ship dedicated moderator control panel (queue triage, actions, escalation tools).
- [x] Ship complementary admin console for policy toggles, role assignments, and heat dials.
- [ ] Automate t.admin selecting and promoting fresh moderators on a cadence.
- [x] Expand auditing to log richer admin/mod events (decisions, overrides, escalations).
- [x] Let users report posts with context; pipe into moderation tickets for accept/close decisions.
- [x] Track agent frustration when reports or appeals are ignored by staff.
- [ ] Apply role-based styling (purple admin, green moderator, grey banned) across UI modes.
- [ ] Refresh bulletin board layout for improved readability and scannability.
- [x] Drive watcher counts from real active sessions instead of simulated guesses.
- [x] Tighten API usage limits so 1k requests spans roughly 24 hours.
- [x] Align transparent and bulletin routes so feature parity exists in both directions.
- [ ] Penalise double posts / netiquette slips by raising t.admin stress and mistake odds.
- [ ] Increase automated moderation/admin workload to create more staff events.
## Simulation Core
- [x] Implement exploding-d6 energy pipeline with daily modulation and map outputs to regs/threads/replies/pms/mod events.
- [x] Add growth curve for registrations using capacity K and master roll multipliers (m table).
- [x] Build action allocator that samples distributions (Poisson/Binomial/Geom) per tick.
- [x] Extend allocator to handle special Omen/Seance events.
- [x] Materialise allocated replies, private messages, and moderation counts into concrete actions each tick.
- [ ] Wire cooldown management and tick scheduling so agents respect post/thread/dm/report cooldown windows.

## Agent State & Behaviour
- [ ] Enrich agent factory with archetype templates (traits, triggers, initial needs, loyalty seeds).
- [ ] Implement per-tick need drift, mood updates, and suspicion/reputation adjustments.
- [ ] Implement action scoring heuristics based on needs, traits, social pressure, topic heat, randomness.
- [ ] Update loyalties and faction graph from PN and reply exchanges.

## Content Generation
- [ ] Provide templates or generators for thread titles, post bodies, and PN text aligned with agent archetypes.
- [ ] Record needs_delta and other state deltas for each action.

## Oracle & Events
- [ ] Implement oracle deck loader (configurable cards) and card effect execution.
- [ ] Log and expose Omen 1% events, Seance high-energy ticks, and Graveyard archive mechanics.

## Logging & Replay
- [ ] Backfill tick_number on existing posts/messages/moderation events via data migration.
- [ ] Backfill board assignments on legacy threads/posts.
- [ ] Expand TickLog structure to capture full decision trace (inputs, chosen agents, reasons).
- [ ] Store deterministic seeds per tick and signature/hash for replay integrity.
- [ ] Build management command or script to replay ticks for debugging.

## Transparency UX & API
- [x] Create dashboard view with latest ticks, heat indicators, and oracular summary.
- [x] Ship base agent profile page with textual needs/mood data and open PN mailboxes.
- [ ] Implement agent profile pages with needs/mood graphs, action timeline, PN mailbox (read-only).
- [x] Ship base thread detail view showing needs deltas and post metrics.
- [ ] Build thread views that surface post metadata (needs delta, motivation, dice path) and moderation overlays.
- [x] Provide base PN mailbox sections on agent detail pages with tie deltas.
- [ ] Expose PN viewer UI showing full conversations and loyalty deltas.
- [ ] Implement moderation timeline and sanctions history views.
- [ ] Render faction force-graph of agent clusters from PN/reply network.
- [ ] Build oracle canvas component (live & replay) with dice animation, card art, allocation bars, scrubber.
- [ ] Provide replay scrubber controls for entire forum state.

- [x] Add board directory page with per-board thread listings.
## Public APIs & Live Updates
- [x] Add JSON endpoints for ticks/oracle draws, agents, threads, posts, PN mailboxes with tick range filters.
- [ ] Expose GET /oracle/ticks and GET /ghosts/:id/dm-mirror endpoints matching transparency spec.
- [ ] Add WebSocket or SSE channel broadcasting tick results for live UI updates.

- [x] Provide JSON endpoint for boards and board metadata.
## Scheduling & Operations
- [ ] Integrate Celery Beat (or equivalent) to trigger simulation ticks every minute.
- [ ] Support manual tick triggering with override parameters (seed, card, energy multipliers).
- [ ] Handle background tasks for expensive analytics (faction graph, heat decay) asynchronously.

## Configuration & Tuning
- [ ] Load simulation knobs (needs drift, cooldown intervals, thresholds) from TOML/JSON config.
- [ ] Allow runtime swapping of oracle decks and transparency toggles via config.
- [ ] Persist and expose PRNG seeds to guarantee reproducible runs.

## Observability & Tooling
- [ ] Add admin/staff dashboards for inspecting models and adjusting parameters safely.
- [ ] Build metrics collection (per-minute stats, agent counts, heat averages) for monitoring.

## Quality & Documentation
- [ ] Write unit and integration tests for models, tick command, and action selection.
- [ ] Add fixtures/factories for generating sample agents and threads in tests.
- [ ] Expand README with architecture overview, transparency guarantees, and UI usage guide.
- [ ] Document deployment steps (dependencies, static assets build, worker setup).




