# Semantic Dashboard — Design Document

*Natural-language dashboards over Postgres, grounded in a semantic layer.*

---

## 1. What this is

A self-hosted web app where a manager describes a chart in plain language and gets a
saved, refreshable dashboard object. The distinguishing constraint: **the LLM never
writes SQL that touches the database.** It emits a structured query expressed in a
hand-curated semantic layer, which a deterministic compiler turns into SQL.

**Secondary constraint, load-bearing for the target environment:** no row-level data is
ever sent to the model **at query time**. Writing a query, the model sees only the layer
definition, and four tests hold that absolutely.

That scoping is deliberate and was not free. The assistant added in section 10 answers
questions about the charts already on screen, and no amount of layer definition lets it
answer "which region is dragging the total down". So the constraint is preserved on the
query path and relaxed on the assistant path, behind two independent switches, with the
transcript recording per message whether values were in scope. Section 10 states exactly
what each surface sees. This is a documented trade, not an erosion: the compiler still
never receives model-authored SQL, and the assistant still never writes a query.

### What this is not

- Not a BI tool for analysts. Analysts should use Metabase.
- Not a data-quality or profiling tool. That was the original idea; it was traded away
  when the user became managers rather than data engineers.
- Not a production system for KMG. It is built against a KMG-*shaped* synthetic
  database. Deployment is out of scope.

### Honest framing

Self-hosted Metabase is free and does natural-language querying with a bring-your-own
API key, grounded in its own semantic layer. This project is not competing with it.
The stated purpose is to learn how text-to-SQL, semantic layers, and chart-spec
compilation actually work by building the pipeline end to end. The interesting output
is the architecture and the eval numbers, not market displacement.

---

## 2. Users and roles

| Role | Who | Does |
|---|---|---|
| Layer author | You (a data engineer) | Runs bootstrap, reviews and edits the layer YAML, hand-writes escape-hatch SQL |
| Consumer | Manager | Creates cards, asks questions in natural language, refines cards, arranges boards |

No authentication or permissions in v0. Every user has personal boards; no sharing,
no roles, no ownership transfer. The two-sided model is *structurally* present — the
layer is authored, the boards are consumed — but the access-control system that
normally accompanies it is deferred.

---

## 3. Architecture

```
natural language
      │
      ▼
 [ LLM ]  ← sees: semantic layer YAML only (no data)
      │
      ▼
 semantic query JSON
      │
      ├── JSON-schema validation (reject/retry before any DB contact)
      ▼
 [ compiler ]  ← deterministic Python, unit-tested
      │
      ▼
    SQL  ──────────────► Postgres (SELECT-only credential)
      │                       │
      │                       ▼
      │                   result set ──► cache (TTL)
      ▼                                     │
 Vega-Lite spec ◄────────────────────────────┘
      │
      ▼
   rendered card
```

Three LLM touchpoints, all narrow:

1. **Bootstrap** (batch, offline): profile output → draft layer YAML.
2. **Query** (interactive): natural language + layer → semantic query JSON + Vega-Lite spec.
3. **Assistant** (interactive, section 10): the board plus, with both gates open, the rows
   already on screen → one action from a closed vocabulary. Every query it runs and every
   card it builds re-enters the pipeline above at step 2, so the diagram is the whole
   story either way.

Everything else is deterministic.

---

## 4. Semantic layer

Hand-curated YAML, LLM-drafted and human-reviewed. **Coverage is demand-driven** — model
the questions people actually ask, not the hundreds of tables that exist. Expect ten to
twenty entities to cover most demand. Exhaustive modelling of four databases is the
failure mode to avoid.

```yaml
entity: well_interventions
  table: ddh.fct_well_interventions
  description: One row per completed well intervention job.
  joins:
    wells:
      to: ddh.dim_wells
      on: wells.well_id = fct_well_interventions.well_id
      confidence: high
  dimensions:
    intervention_date:
      type: date
      grains: [day, month, quarter, year]
    region:
      type: string
      via: wells.region_name
    intervention_type:
      type: string
      values: [FRAC, WORKOVER, ACIDIZING, PERFORATION]
    status:
      type: string
      values: [COMPLETED, CANCELLED, IN_PROGRESS]
      confidence: low   # inferred from sample; verify business meaning
  measures:
    net_gain:
      agg: sum
      column: net_gain_bbl
      description: Incremental production attributed to the intervention.
    n_jobs:
      agg: count
      column: "*"
  synonyms:
    net_gain: [gain, uplift, incremental production]
```

**Confidence gate:** any entity containing a `confidence: low` field is refused at query
time with a message naming the unverified field. This turns layer review from a chore
into an unblock — the only reliable way to get it done.

---

## 5. Semantic query grammar (v0)

```json
{
  "entity": "well_interventions",
  "measures": ["net_gain"],
  "dimensions": [
    {"field": "intervention_date", "grain": "month"},
    {"field": "region"}
  ],
  "filters": [
    {"field": "intervention_date", "op": "in_year", "value": 2026},
    {"field": "status", "op": "=", "value": "COMPLETED"}
  ],
  "order_by": [{"field": "net_gain", "dir": "desc"}],
  "limit": 100
}
```

**In scope:** one entity; N measures; up to 2 dimensions; date grains; filters with
`=`, `!=`, `in`, `between`, `in_year`, `last_n_days`; order; limit.

**Deliberately out of scope:** joins beyond declared `via` paths, window functions,
subqueries, self-joins, cross-grain measure ratios, cohort analysis.

Borrow Cube's query shape rather than designing from scratch, then trim to the above.

### What the grammar makes impossible

- Referencing a table that doesn't exist — `entity` is validated against the layer;
  there is no free-text table slot.
- Inventing a join — join paths are declared, not generated. This kills the most
  dangerous silent-wrongness class in text-to-SQL.
- Aggregating the wrong way — `net_gain` means `SUM(net_gain_bbl)`, one definition,
  everywhere.
- Any write — the grammar has no DML.
- Executing a malformed response — JSON-schema validation runs before the database is
  touched.

### Expressiveness ceiling

Anything outside the grammar is *impossible*, not merely hard. That is a feature for
managers and a wall for anyone else. Section 8 handles the wall.

---

## 6. Compiler contract

Plain Python, roughly 300 lines. A given semantic query compiles to exactly one SQL
string — which means the compiler gets ordinary unit tests, and the LLM eval only has to
compare two small dicts rather than judge SQL equivalence.

Responsibilities:

- Resolve `via` dimensions to their declared join paths; emit only the joins actually needed.
- Apply date grain via `date_trunc`.
- Build `SELECT`/`GROUP BY` from measures and dimensions.
- Enforce `limit` (hard cap, default 10,000 rows).
- Return `(sql, params)` — parameterised, never string-interpolated.

**Cross-check:** every field referenced in the Vega-Lite spec must appear in the SQL's
output columns. Verify programmatically. This eliminates the most common class of silent
render failure.

---

## 7. Data model

A **card** is the unit of persistence:

```
card
├── id, board_id, title
├── semantic_query    (JSON)   ← the source of truth
├── vega_spec         (JSON)
├── prompt            (text)   ← provenance only, never re-executed
├── state             (enum: empty | ready | broken)
├── layout            {x, y, w, h}
├── cache             {result, compiled_sql, fetched_at, row_count, data_max_ts}
├── ttl_seconds       (default 900)
└── previous          {semantic_query, vega_spec}   ← one-step undo
```

**The stored artifact is the semantic query, not SQL.** SQL is compiled at render time
and cached as a by-product. Frozen SQL rots: a renamed column breaks every stored query
with no bulk fix. A stored semantic query is validated against the layer, so a schema
change breaks the *layer* in one place and is fixed once. It also makes cards diffable,
editable, and re-targetable across databases.

An **empty card** is a real persisted row in `state: empty`. A manager sketching a layout
with four blank cards must not lose them on reload. The empty state hosts the prompt box
and example questions drawn from the layer — which doubles as the discoverability
mechanism for what vocabulary exists.

### Dashboard JSON export (version 1)

Dashboard JSON is a portable definition, not a snapshot of result rows. Version 1 has
this shape:

```json
{
  "version": 1,
  "title": "Operations",
  "exported_at": "2026-08-20T12:00:00.000Z",
  "cards": [
    {
      "title": "Oil by region",
      "semantic_query": {
        "entity": "daily_production",
        "measures": [{"name": "oil_production"}],
        "dimensions": [{"field": "region"}]
      },
      "chart_hint": "bar",
      "layout": {"x": 0, "y": 0, "w": 6, "h": 10},
      "ttl_seconds": 900
    }
  ]
}
```

`exported_at` is an ISO-8601 timestamp. Empty placeholder cards are omitted. Import is
not supported; the version exists so a future importer can identify this exact shape.

---

## 8. Trust surface

The manager cannot read SQL by construction, so trust is architectural, not
presentational. Every card header shows:

1. **Plain-English restatement**, generated **deterministically from the compiled query
   object** — never by the LLM. A second thing that can lie is not a safeguard.
   > *Sum of net gain, by calendar month and region, filtered to 2026 and status
   > COMPLETED, from Well Interventions — 48 rows, data through 14 Aug 2026.*
2. **Row count and data max timestamp.** Catches staleness bugs no chart ever will.
3. **Cache timestamp** — "as of 14:32".
4. **Collapsible SQL panel**, collapsed by default.

The assistant has an equivalent, described in section 10: every figure it states is
recomputed from the rows before the sentence is shown, and a figure that does not check
out is removed rather than captioned. Same principle in both places — the thing a reader
trusts is computed, never asserted by the model.

### Ambiguity

Detected by margin: the model returns candidate matches, and if the top candidate does
not win clearly, the card asks one clarifying question before rendering. Falls back to
refusal naming what's undefined. A confident wrong chart is how manager-facing BI loses
trust permanently — one bad number in one board meeting and nobody opens it again.

### Out-of-grammar questions

**Not built, and the reasoning is worth keeping.** The original design gave these an
ungrounded answer: the model writes raw SQL, the result renders in the answer pane only,
marked ungrounded and unsaveable. The argument was that every guarantee above is a
guarantee about *saved* objects, so an ephemeral answer costs nothing and always gives
the manager something.

It was dropped because it puts model-authored SQL in front of the database for the first
time, which is the one thing section 1 says this app does not do. "Ephemeral" is a
property of the rendering, not of the query: the SELECT still runs, on a credential that
can read everything the layer deliberately does not expose. That is a different system
with a different threat model, not a fallback.

What shipped is the second half of the original idea without the first. A refusal names
what is missing and hands back a copyable request for the layer author — the question
stated in their terms, with the missing metric named. Out-of-grammar questions still
become a backlog of what the layer lacks; they just do not become SQL on the way.

A warning label on a saveable card was rejected for its own reasons: boilerplate goes
unread within a week, and the fallback would fire precisely on the hard questions where
being wrong matters most.

### Broken cards

When the layer changes underneath a card, it renders an **error card naming the missing
field**. Not stale-but-pretty cached results (that's how weeks-old numbers reach board
decks), not LLM auto-repair (that's a chart silently changing meaning).

Ship a `validate-all` command that checks every saved card against the current layer and
reports breakage *before* a layer change goes out.

---

## 9. Refinement

The manager types "break this down by region" into an existing card.

- The LLM receives **the card's current semantic query plus the layer** — not conversation
  history. The card's state *is* the context, which sidesteps multi-turn drift entirely.
  This stays true even though the assistant in section 10 does carry history: refinement
  and conversation are different surfaces, and the card is the one with a state to be the
  context.
- It returns a **full replacement query**, validated identically to a fresh one. The diff
  is computed by you and displayed. Small models handle patch semantics badly; this gets
  the benefit of a patch without asking for one.
- **Route by diff:** if the semantic query is byte-identical and only the Vega-Lite spec
  changed (chart type, sort, colour), re-render from cache without touching the database.
  Chart-type flips feel instant instead of costing a 20-second warehouse scan.
- **A clarifying question is stored on the card.** Without that the card asks "oil or
  gas?", the person types "oil", and the next request arrives as one word attached to
  nothing. The card carries the exchange — the question and the *original* request, which
  is what has to be rebuilt — and clears it once resolved so it cannot colour a later,
  unrelated question. An edit the assistant makes fills the same field the edit box does.
- **One-step undo.** Store the previous query and spec on the card. Refinement will
  occasionally make a card worse, and with no undo the manager's only recourse is
  rebuilding — the moment they stop trusting the edit box.

---

## 10. The assistant

A side panel, spanning every dashboard rather than scoped to one, that answers questions
about what is on screen and proposes changes to it. It shares the query path: every
query it runs is written by the same `ask()` the card's own box uses.

### What each surface sees

Three surfaces, three different answers, stated here because section 1's headline claim
is only true of the first.

| Surface | Layer | Row values | Conversation |
|---|---|---|---|
| Query path (`/ask`, refinement) | yes | **never** | never |
| Chart compilation | n/a | yes, but no model is involved | n/a |
| Assistant | yes | only with both gates open | last N turns, as prose |

Row values reach the assistant only when `chat_sees_data` (server) **and**
`share_visible_data` (browser) are both true. The client can withhold consent it was
given; it can never grant consent the server withheld. With the gates shut the context
carries structure only — titles, restatements, row counts, chart types — and the
assistant is told to say it cannot see the numbers and offer to fetch them.

Only the *active* dashboard is described in detail. Other dashboards are listed by name
so they can be referred to and changed, never read: their rows are not on screen, and
putting them in scope would share data nobody is looking at.

The row budget is a crash guard, not a policy cap. Past it, the largest remaining card
degrades to a deterministic summary and **the transcript says so**, because an answer
built on a summary must not read as if it were built on the rows.

### A turn is two calls

The action contract has twelve variants. None of them can be asked for: a
structured-output schema is compiled into a grammar, and Anthropic rejects one past
roughly 8.4KB of JSON Schema. Measured live on `claude-haiku-4-5`, four read-only
variants come to 8293B and compile; the same four plus one three-field variant come to
8568B and do not.

So a turn is one call when the answer is prose and two when it is not:

1. **Router** — four variants: `answer`, `clarify`, `refuse`, or `task` with a `kind`.
   4937B, with room to spare.
2. **Detail** — a single-variant schema for that kind. The largest is under 2.1KB.

The split earns more than a fit. The router carries no card id, no board id and no
query, so a reply cannot arrive already half-applied. And two kinds need no schema at
all: `run_query` and `edit_card` resolve through `query_step.ask()`, which already has
the query prompt, the synonym guard, layer validation and a retry. **The assistant never
writes a semantic query.** It says which question to ask; the one path that has always
turned English into a query turns this one too.

### Nothing is applied by the turn that proposes it

A change becomes a **frozen plan**: a row holding both what will be applied and the
line-by-line preview shown to the person, in one document, so the sentence they read and
the change they authorise cannot come from different decisions. Applying it is a separate
request from the browser.

- **Confirmation asks no model anything.** It reads the resolved document, never the
  action the assistant proposed. An edit's replacement query was written while the plan
  was being resolved, which is what lets the preview be an exact diff.
- **A plan knows what it was computed against.** It records the `revision` of every board
  it touches. If any of them moved, confirming is refused rather than applied to a
  dashboard that is no longer the one described.
- **One pending plan per conversation.** Two would mean confirming the older one after
  reading the newer one's preview.
- **Claiming the plan is a compare-and-set.** Two browsers confirming the same plan is a
  real sequence of events; the loser is told, not served a second copy of the effect.
- **Removals are soft**, and the server refuses to remove the last remaining dashboard.

### Generated cards

New cards carry questions, not queries. The dashboard and its empty cards are created at
once so the grid is settled, then each question is answered one model call at a time in
the background, sequentially — six concurrent calls would finish sooner and arrive as one
indistinguishable batch. Each card succeeds or fails alone: a question the layer cannot
answer costs that card and nothing else, and its placeholder stays and says why.

Progress is a **log, not a stream**: the browser asks for the events it has not seen. Same
events, same order, same payloads a stream would carry; a reconnect is just another
request with a different number in it.

Placement is the application's decision. A layout the assistant asks for is honoured only
when it fits and lands on nothing; otherwise the card takes the next free slot — the same
slot `next_slot` would give a card the person created. A model that could place one card
on top of another could hide the board.

### Every number is still recomputed

An answer states figures only as **claims**, and a claim is an address plus an operation —
which card, which column, which row, and what arithmetic. The server reads the value out
of the row and redoes the sum. Any figure the model supplies is ignored, and a claim that
does not check out is removed from the answer rather than shown with a caveat. This is the
assistant's equivalent of the restatement: the trust surface is computed, never asserted.

Addressing rows *by position* rather than by matching column values was forced by
behaviour, not chosen. Asked three ways for the keys identifying a row, Haiku still sent
an empty key set — which matches every row — and tightening the type did not help, because
constrained decoding does not enforce `minProperties`. A position is mechanical: the rows
are listed in order, the server holds one snapshot for the whole turn, and an index that
does not exist is simply withdrawn.

---

## 11. Bootstrap and profiler

A batch job, run offline by the layer author.

**Profile deterministically, then let the LLM interpret the profile.** Never ask a model
to guess a null rate it could be told. From `pg_catalog` and aggregate queries: table and
column names, types, PK/FK constraints, row counts, distinct counts, null rates, min/max.

### Redaction levels

Set per run, recorded in the output:

| Level | Sends |
|---|---|
| `metadata_only` | Names, types, constraints, counts, null rates |
| `low_cardinality_values` *(default)* | Above + distinct values for columns under ~25 distinct — `status` codes are schema, not data |
| `sample_rows` | Above + N raw rows per table |

Plus a `--dry-run` flag that writes the exact prompt to a file instead of sending it.
"Is this safe" stops being an argument and becomes something you look at. Costs an hour;
it's the difference between a security posture and a hope.

Use `sample_rows` when a local model is in play — nothing leaves the perimeter, and weak
models need the extra signal. Note the exposure window is a handful of batch runs, not
every user interaction: at query time the LLM sees only the layer YAML, which contains
no data.

### Review

LLM emits YAML; you edit it in your IDE. **No review UI** — that's a week of work
replacing a text editor you already like. Every inferred join and business-meaning guess
carries `confidence: low|high` and a `# TODO: verify` annotation. The model can propose
groupings, classify dimension vs measure vs key, infer joins, draft descriptions. It
cannot know that revenue excludes intercompany transfers or that `status = 3` means
cancelled. Review is where the value is added.

---

## 12. Dashboard and caching

- 12-column drag-resize grid (`react-grid-layout`), persisting `{x, y, w, h}` per card.
- Vega-Lite specs, validated against the schema — reject on failure rather than rendering
  something broken. LLM emits query and spec in one call.
- **TTL cache**, default 900s, configurable per card, manual refresh button. Query-on-load
  feels broken against a warehouse where a scan takes 20 seconds; scheduled materialisation
  needs a scheduler not worth operating yet. The TTL also gives you the honest version of
  freshness, which the manager needs anyway.

---

## 13. Eval harness

~30 natural-language questions paired with expected semantic-query JSON, run as a test
suite. **Write the fixtures before tuning the prompt.**

Measure exact-match on the JSON, plus a relaxed match ignoring key order and dimension
ordering. Report:

- Accuracy with the semantic layer vs. raw text-to-SQL baseline.
- Accuracy by model, so swapping in a local model is a measurement rather than a hope.

The assistant gets its own suite and its own runner, because a turn fails in ways a query
cannot and the fixes live in different places. Four axes, never averaged: **action**
(which of the twelve things the turn decided this was), **query** (was the query right),
**plan** (having routed to a change, did it name the right cards), and **claims** (did the
figures survive recomputation). Routing to `new_cards` and then proposing two cards
instead of three is a second-stage failure; folding it into action accuracy would hide
which of the two calls went wrong.

A change fixture is scored on what the turn *set out to do*. A `delete_dashboard` the
server then declines still routed correctly — declining it is the application's job, and a
unit test holds that rule. No eval turn changes anything: a change is proposed as a plan
and applied only by a confirmation the suite never sends.

This is the highest-leverage item on the list for how the project reads to a technical
reviewer. *"78% exact-match on 30 held-out questions, 91% with the semantic layer versus
54% without"* is a sentence almost no side project can produce.

---

## 14. Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI (Python) | `pg_catalog` introspection, profiling, SQL compilation — SQLAlchemy/pandas territory, strongest language |
| Frontend | React + TypeScript | `react-grid-layout` and `react-vega` have no good substitute |
| Contract | Pydantic → generated TS types | Single source of truth for the semantic-query schema; contract can't drift |
| DB | Postgres, `SELECT`-only credential | Destructive operations impossible by construction, not by intention |
| Run | Monorepo, `docker compose up` | One command gives a seeded database and a working app |

Streamlit was rejected: it cannot give you a draggable dashboard grid, and you'd be
fighting it within a week.

### Demo data

Oil-and-gas shaped — wells, interventions, production readings, statuses — with `stg`
and `ddh` schemas mirroring the layered architecture. Generic-friendly naming. Seeded by
script so anyone can `docker compose up` into a populated database. It's the domain you
understand, the messiness is messiness you've seen, and it's more memorable than another
orders-and-customers demo.

---

## 15. Build order

1. Seed database — `stg`/`ddh` schemas, synthetic dirty data, seed script.
2. Profiler — `pg_catalog` introspection, three redaction levels, `--dry-run`.
3. Bootstrap — profile → draft layer YAML with confidence annotations.
4. **Hand-write the layer for two entities.** Do this before automating anything.
5. Compiler + unit tests — semantic query → SQL.
6. FastAPI endpoints — compile, execute, cache.
7. Single card render — one hard-coded semantic query → Vega-Lite.
8. LLM query step — NL + layer → semantic query + spec, JSON-schema validated.
9. Grid layout, empty cards, persistence.
10. Trust surface — restatement, SQL panel, freshness, broken-card state.
11. TTL cache + refresh.
12. **Eval harness** — 30 fixtures, baseline comparison.
13. Refinement + one-step undo.
14. ~~Ungrounded fallback~~ — dropped; see section 8 for why.
15. `validate-all` command.
16. README.
17. Tabs, board ordering, export (PNG, CSV, JSON).
18. Assistant: read-only turns, board context behind two gates, claim verification.
19. Assistant: frozen plans, confirmation, generated dashboards.

Steps 1–7 are the spine; if you stop anywhere, stop after 12.

---

## 16. README framing (90 seconds)

In this order:

1. One-paragraph problem statement, **leading with the constraint**: *the model never
   writes SQL, and never sees a row while writing a query.* That's the sentence that makes
   an enterprise-minded reader keep reading, and it's true of the design rather than
   retrofitted. State the assistant's scope in the same breath rather than in a footnote:
   it can be shown the rows already on screen, both switches are the operator's, and every
   figure it states is recomputed before it is shown. A reader who finds that out later
   discounts everything else on the page.
2. GIF of the interaction — ask, render, refine.
3. Eval table.
4. Architecture diagram.
5. `docker compose up`.

Do **not** claim Metabase is unaffordable or cloud-only. It is free and self-hostable, and
anyone technical will know.

---

## 17. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Layer authoring burden exceeds appetite | High | Demand-driven coverage; two entities is a valid v0 |
| Entity/measure resolution accuracy is poor | High | Eval harness measures it early; synonyms in the layer |
| Expressiveness ceiling frustrates users | Medium | Unsaveable fallback + metric request backlog |
| Scope creep — no time cap was accepted | High | Section 14 build order is the scope cap; stop after step 12 |
| Local model can't hit useful accuracy | Medium | Deferred; the eval harness makes it a measurement, not a gamble |
| "Why not Metabase" from a reviewer | Low | Answer honestly: it's a learning project, and the architecture is the artifact |

---

*Settled across five grilling rounds. Nothing below the frontier remains assumed.*
