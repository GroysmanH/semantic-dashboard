# Semantic Dashboard — Design Document

*Natural-language dashboards over Postgres, grounded in a semantic layer.*

---

## 1. What this is

A self-hosted web app where a manager describes a chart in plain language and gets a
saved, refreshable dashboard object. The distinguishing constraint: **the LLM never
writes SQL that touches the database.** It emits a structured query expressed in a
hand-curated semantic layer, which a deterministic compiler turns into SQL.

**Secondary constraint, load-bearing for the target environment:** no row-level data is
ever sent to the model at query time. The model sees only the layer definition.

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

Two LLM touchpoints, both narrow:

1. **Bootstrap** (batch, offline): profile output → draft layer YAML.
2. **Query** (interactive): natural language + layer → semantic query JSON + Vega-Lite spec.

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

### Ambiguity

Detected by margin: the model returns candidate matches, and if the top candidate does
not win clearly, the card asks one clarifying question before rendering. Falls back to
refusal naming what's undefined. A confident wrong chart is how manager-facing BI loses
trust permanently — one bad number in one board meeting and nobody opens it again.

### Ungrounded fallback

Out-of-grammar questions get an answer, but never a durable one:

- LLM writes raw SQL; result renders **in the answer pane only**, marked ungrounded,
  SQL shown **expanded by default**.
- **Cannot be saved to a board.** The "add to dashboard" control is absent, not disabled.
- Offers "request this as a metric" instead — files the question to the layer author with
  the generated SQL attached as a starting point.

This preserves every guarantee above (they were only ever guarantees about *saved*
objects), always gives the manager an answer, and converts out-of-grammar questions into
a prioritised backlog of what the layer is missing.

A warning label on a saveable card was rejected: boilerplate goes unread within a week,
and the fallback fires precisely on the hard questions where being wrong matters most.

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
- It returns a **full replacement query**, validated identically to a fresh one. The diff
  is computed by you and displayed. Small models handle patch semantics badly; this gets
  the benefit of a patch without asking for one.
- **Route by diff:** if the semantic query is byte-identical and only the Vega-Lite spec
  changed (chart type, sort, colour), re-render from cache without touching the database.
  Chart-type flips feel instant instead of costing a 20-second warehouse scan.
- **One-step undo.** Store the previous query and spec on the card. Refinement will
  occasionally make a card worse, and with no undo the manager's only recourse is
  rebuilding — the moment they stop trusting the edit box.

---

## 10. Bootstrap and profiler

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

## 11. Dashboard and caching

- 12-column drag-resize grid (`react-grid-layout`), persisting `{x, y, w, h}` per card.
- Vega-Lite specs, validated against the schema — reject on failure rather than rendering
  something broken. LLM emits query and spec in one call.
- **TTL cache**, default 900s, configurable per card, manual refresh button. Query-on-load
  feels broken against a warehouse where a scan takes 20 seconds; scheduled materialisation
  needs a scheduler not worth operating yet. The TTL also gives you the honest version of
  freshness, which the manager needs anyway.

---

## 12. Eval harness

~30 natural-language questions paired with expected semantic-query JSON, run as a test
suite. **Write the fixtures before tuning the prompt.**

Measure exact-match on the JSON, plus a relaxed match ignoring key order and dimension
ordering. Report:

- Accuracy with the semantic layer vs. raw text-to-SQL baseline.
- Accuracy by model, so swapping in a local model is a measurement rather than a hope.

This is the highest-leverage item on the list for how the project reads to a technical
reviewer. *"78% exact-match on 30 held-out questions, 91% with the semantic layer versus
54% without"* is a sentence almost no side project can produce.

---

## 13. Stack

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

## 14. Build order

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
14. Ungrounded fallback (answer pane only, unsaveable).
15. `validate-all` command.
16. README.

Steps 1–7 are the spine; if you stop anywhere, stop after 12.

---

## 15. README framing (90 seconds)

In this order:

1. One-paragraph problem statement, **leading with the constraint**: *no row-level data
   ever reaches the model.* That's the sentence that makes an enterprise-minded reader
   keep reading, and it's true of the design rather than retrofitted.
2. GIF of the interaction — ask, render, refine.
3. Eval table.
4. Architecture diagram.
5. `docker compose up`.

Do **not** claim Metabase is unaffordable or cloud-only. It is free and self-hostable, and
anyone technical will know.

---

## 16. Risks

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
