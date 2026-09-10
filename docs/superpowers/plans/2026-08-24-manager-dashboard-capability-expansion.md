# Manager Dashboard Capability Expansion

## Context and global constraints

Implement the approved manager-dashboard plan on the existing 12-column desktop grid. Keep direct deletion, transient SQL/Edit expansion, light mode, and the existing 220 ms/reduced-motion behavior. Canonical layouts are server-owned; SQL/Edit heights are never persisted. Existing cards and dashboards default to free layout and must not move or auto-size on migration. New blank cards auto-size at most once. Unit/integration tests use deterministic fakes; optional live LLM tests prefer Gemini, then DeepSeek if supported, and never silently spend on another provider.

## Task 1: Persisted layout policy, canonical resolver, and card duplication

- Add `app.board.layout_mode` constrained to `free | auto_pack`, default `free`, in fresh DDL and an idempotent migration.
- Add `app.card.auto_size_pending`; migrated rows default false while every newly created blank card (direct or chat-created) explicitly starts true.
- Add typed layout input/output. `PATCH /boards/{id}/layout` accepts complete card layouts plus `manually_resized` card IDs, validates 12-column integer bounds and ownership, clears pending sizing for those IDs, resolves collisions, applies upward-only auto-pack when enabled, persists transactionally, and returns `{layouts, revision}`.
- Add a pure deterministic vertical packer: sort by desired `y`, `x`, stable ID; preserve `x/w/h`; move each card to the smallest non-colliding non-negative `y` at its `x`.
- `PATCH /boards/{id}` accepts layout mode. Enabling Auto-pack immediately resolves/persists the board; disabling freezes it. Board duplication copies layout mode.
- Add `POST /cards/{id}/duplicate`. Copy title as `Copy of <title>`, semantic query, visualization, prompt, state, cache, TTL and exact `w/h`; exclude previous/pending clarification; set auto-size false. Prefer right of source, then below, then nearest deterministic free anchor. Apply board packing and return the duplicate plus canonical layouts.
- Run packing after delete and server/chat layout mutations when the board has Auto-pack enabled.
- Tests first: migration defaults/convergence; packer cases; layout validation/ownership; immediate enablement; deletion packing; duplicate fields, placement, independence, and transactionality.

## Task 2: One-time visualization-aware sizing

- Add a pure backend sizing function based on final chart type/spec metadata and rendered row cardinality, not DOM size.
- Profiles: scalar/KPI `4x6`; ordinary chart `6x10`; bars 9-16 categories and temporal >36 points or 4-8 series and moderately dense scatter `8x12`; bars >16 use `w=8,h=min(16,9+ceil(categories/3))`; map/heatmap/facets `8x12`; >4 facets or >8 visible series `12x14`.
- On the first successful render of a pending card, resize at the existing anchor, clear the flag atomically, push only the collision chain downward, then Auto-pack if enabled. Clarifications, refusals, transport failures, and unsuccessful renders do not resize or consume the pending first-success opportunity. Edits or refreshes after success, duplicates, migrated cards, and manually resized blank cards must not trigger later sizing.
- Integrate the rule into both direct card queries and asynchronous chat-created card completion.
- Tests first for every profile, first-success-only behavior, manual-resize cancellation, growth collisions, chat/direct consistency, and duplicate/existing-card exclusion.

## Task 3: Frontend duplication, Auto-pack, and authoritative layout integration

- Extend generated/manual API types for `layout_mode`, `auto_size_pending`, duplicate responses, and canonical layout responses.
- Add `Duplicate card` to the card overflow. Show busy/error state; after success apply returned layouts, reload/hydrate, select, scroll, and focus the copy.
- Add a restrained labeled Auto-pack switch in the board masthead near the Add card action. It is per-dashboard, keyboard accessible, announces state, immediately animates enabling, and preserves reduced motion.
- Keep optimistic drag/resize previews, then reconcile to server-returned layouts. Pass the resized card ID only on resize stop. Keep transient Edit/SQL rows excluded. Free mode retains the existing professional settle behavior; Auto-pack preview moves cards upward only.
- Tests first for menu duplication, errors/focus/scroll, toggle persistence, immediate packing, canonical reconciliation, resize metadata, unchanged transient behavior, keyboard access, and reduced motion.

## Task 4: Reliable card and dashboard exports

- Refactor visible-chart preparation into a pure shared builder that injects rows/outlines, size, and theme configuration for both visible and export views.
- Ready cards always expose CSV and PNG actions after reload. CSV uses full `render.rows`.
- PNG uses the mounted view when valid; otherwise build/finalize a temporary off-screen Vega view from the hydrated spec/rows and measured/export bounds.
- Dashboard PNG awaits every ready visual card and never silently drops one. If any cannot render, abort the partial export and name those cards in a plain-language notice.
- Preserve filenames, captions, layout proportions, and existing error notices.
- Tests first for reloaded existing-card CSV/PNG, stale view replacement, off-screen fallback cleanup, dashboard waiting, complete composition, and named failure rather than omission.

## Task 5: Realistic operational warehouse profile

- Extend fresh warehouse DDL and add an idempotent migration for well attributes (asset, operator, basin, operating status, lift method), raw staging mirrors, downtime events, monthly field targets, and a monthly actual/target performance mart, with query-path indexes.
- Keep demo seed behavior unchanged. Add explicit `demo|realistic` profile parsing and configurable end date. Realistic defaults to August 2018-August 2026 with exactly 600 wells (480 producer, 90 injector, 30 observation), 24 fields, about 1.4M daily rows, 16,000 interventions, deterministic downtime, targets, and monthly performance.
- Keep legacy codes/casing/nulls/orphans in staging; map documented status codes in curated data so interventions become queryable.
- Add semantic entities for downtime and production performance; enrich existing production/intervention dimensions without widening the one-entity grammar.
- Add a confirmation-gated `seed-realistic` command and dedicated realistic test/benchmark target. Never auto-truncate a populated database.
- Tests first for profile parsing, deterministic counts/horizon, staged dirtiness versus curated statuses, DDL/migration convergence, layer validation/compiler output, and representative manager queries.

## Task 6: Manager-safe model failures and recovery

- Wrap validation errors thrown directly by OpenAI-compatible `chat.completions.parse` as `LLMSchemaError` so the existing same-provider one-retry path runs.
- After a second schema miss, store a safe refusal: `I couldn’t prepare that change safely. Nothing changed.` with stable failure code, retryable flag, retry text, and optional target card ID. Never switch provider silently.
- Add a structured API error envelope (`code`, `message`, `request_id`, `retryable`) for unexpected chat failures. Log provider/model, router/detail stage, schema, retry count, request ID, and raw validation only on the server.
- Parse safe API errors in the browser; unknown 5xx responses become a generic manager-facing message rather than raw text.
- Add Try again and conditional Edit card actions. Relabel transcript metadata to `Dashboard: <title>` and `Used the numbers shown on this dashboard.`
- Tests first for parse-time ValidationError conversion, same-provider retry, safe persisted refusal, error envelope/no raw detail, retry/edit actions, and common-language metadata.

## Completion gate

- Regenerate API schema/types.
- Run targeted red/green tests per task, then full backend pytest, frontend Vitest, TypeScript/Vite build, and migration/semantic validation.
- Perform a broad whole-branch review and resolve all Critical/Important findings before branch handoff.
