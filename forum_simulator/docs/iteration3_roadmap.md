# Iteration 3 Roadmap: Governance & Moderation Blitz

## Guiding Themes
- Empower human-like staff flows with dedicated tooling for moderators and admins.
- Increase accountability and transparency for moderation actions and ignored reports.
- Strengthen community experience with better styling, layout, and behavioural feedback.
- Curb runaway API consumption and align watcher metrics with actual audience.

## Work Streams & Milestones

### 1. Moderator & Admin Tooling
- Build `/moderation` control panel: queue filters, ticket triage, quick actions (lock/move/delete/pin). Target: end-of-week.
- Build `/admin/command` console: adjust knobs (API caps, heat multipliers), manage archetype pools, broadcast announcements. Target: +3 days.
- Add background job for `t.admin` to review staff roster weekly; promote agents with high reliability into moderators.
- Enhance `ModerationEvent` log entries (actor role, ticket linkage, before/after state, risk level).

### 2. Community Reporting Loop
- Front-end: report button on posts (modal with message).
- Backend: `ReportTicket` model or extend `ModerationTicket` with source=report + reporter context.
- Workflow: moderators mark report as accepted/resolved/ignored; auto-feedback to reporting agent (affects satisfaction).
- Analytics: surface ignored report count per agent; drive frustration meter when threshold crossed.

### 3. Presence & Watchers Accuracy
- Track active sessions via middleware; persist `ActiveWatch` per `user_session_id` and thread.
- Sync thread `watchers` JSON from real active watches + fallback to simulated guests.
- Update `/who` view and API to reflect live session data; add SSE ping for front-end auto-refresh.

### 4. Experience & Styling Upgrades
- Role-based rendering: CSS classes + legend (purple admin, green mod, grey banned).
- Bulletin layout refresh: spacing, typography, pinned card highlight, mod/admin badges, quick filters.
- Transparent/Bulletin route parity checklist; ensure equivalent nav + content features.

### 5. Behaviour & Stress Modelling
- Introduce cooldown for double-posting; log infractions.
- Adjust `t.admin` mood/stress when infractions detected or reports ignored; influence allocation heuristics and error rate.
- Increase background moderation events during high-stress periods (auto audits, warnings).

### 6. API Governance
- Implement rate-limiting middleware (per API key / IP) with config in admin console.
- Telemetry: record usage per endpoint + breach alerts.
- Update docs and dashboard widgets for API budget.

## Dependencies & Risks
- Requires session identification for simulated visitors; confirm we can rely on cookie-based session IDs without auth.
- Need design assets for new dashboards (ensure CSS remains bulletproof across modes).
- Rate limiting must not block internal simulation tasks hitting APIs.

## Next Steps
1. Scaffold report submission flow (models, forms, endpoints).
2. Stand up staff dashboards with role checks and navigation.
3. Introduce session-aware watcher tracking.
4. Roll out styling & layout tweaks.
5. Implement rate limiting + stress feedback loops.

Progress will be tracked in `../roadmap.md` and summary updates appended to `docs/CHANGELOG.md` once features ship.
