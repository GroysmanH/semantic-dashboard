# Semantic Dashboard

**Ask a question in plain language, get a chart you can trust.**

The model never writes SQL, and never sees a row while writing a query. It picks from a
hand-written menu of entities, measures and dimensions — a *semantic layer* — and returns
a structured object naming what it chose. A deterministic compiler turns that object into
SQL.

So the failure mode is *"I don't have a measure for that"*, not a confident query against
columns nobody verified.

![A dashboard of oil-production charts built from natural-language questions](board.png)

---

## Why this exists

Point an LLM at a database schema and ask it for SQL, and it will give you SQL. That's the
problem. The query runs, a chart appears, and nothing on screen tells you the model guessed
what `dayfact_v2` meant, joined on the wrong key, or silently summed a month-to-date column
across thirty days.

For a manager who can't read SQL, a wrong chart is indistinguishable from a right one.

This project takes the opposite approach: **narrow what the model can say until every
sentence it can produce is one you already verified.**

| | Text-to-SQL | This |
|---|---|---|
| Model outputs | SQL | A structured object: entity, measures, dimensions, filters |
| Column meanings | inferred from names | declared once, by a human |
| Bad question | plausible wrong answer | explicit refusal |
| SQL injection | mitigated | impossible — no string concatenation exists |
| Unverified field | queried anyway | blocks the whole entity until confirmed |

**Measured on 60 benchmark questions** against a synthetic oil-field warehouse
(`claude-haiku-4-5`):

| | Semantic layer | Raw text-to-SQL |
|---|---|---|
| Execution accuracy | **88%** | 44% |
| Correct refusals | **88%** | — |
| Right chart chosen | **85%** | n/a — returns rows, not charts |

Same model, same questions, same database. The gap is the architecture.
Full breakdown, including every miss: [`docs/eval-results.md`](docs/eval-results.md).

---

## Try it

```bash
git clone <this-repo> && cd semantic-dashboard
cp .env.example .env          # add one LLM API key
make up
```

Open <http://localhost:5173>. A bundled Postgres seeds itself with a synthetic oil-field
warehouse — 200 wells, two years of daily production — so there's nothing external to
configure. Type *"oil production by region"* and you get a bar chart.

Works with Anthropic, Google, OpenAI or NVIDIA. Each request runs on a cheap model by
default; a "this one is hard" toggle escalates to a stronger one, so ordinary questions
stay cheap.

```bash
make test      # 1049 backend tests
make eval      # the accuracy sweep above (~$0.30 of API credit)
make down
```

---

## How it works

```
question ──► LLM ──► SemanticQuery ──► compiler ──► SQL ──► warehouse
              ▲         (JSON)          (Python)             (read-only)
              │
        semantic layer
     (hand-written YAML)
```

**1. The layer is the menu.** Each entity names a table, its measures, its dimensions, and
the words people actually use for them. This is the only thing the model sees. It contains
no rows.

**2. The model orders from it.** It returns an entity name, measure names, up to three
dimensions and filters — never SQL, never a column name.

**3. The compiler builds the query.** Every identifier is quoted, every value is bound as a
parameter. Injection isn't mitigated; concatenation never happens.

**4. The chart follows the shape.** Column types and cardinality choose the encoding. Values
get a say in exactly three judgement calls: whether a pie has too many slices, whether a
third dimension is sparse enough to facet, whether two measures span enough categories to
scatter.

### Six design decisions

Each was made against a specific failure. [`docs/design.md`](docs/design.md) has the full
argument.

**Refusing is a feature.** *"Which region is most profitable?"* has no profit measure. The
card says so and names what it does have, instead of substituting revenue and drawing a
confident chart.

**Unverified fields block the entity.** Mark a field `confidence: low` and every query on
that entity refuses, naming the field. It's meant to be used freely while you're still
learning a schema — a missing measure is a question someone asks; a wrong measure is an
answer nobody questions.

**One dashboard, one schema.** Narrowing the menu makes a model *more* dangerous, not less:
shown only a planning mart and asked about production, it has no right answer available, and
a model with no right answer picks the closest wrong one. A deterministic guard catches
this and refuses by name — **before any API call**, in about 19 ms.

**An entity can be a slice of its table.** Warehouses stack facts: production and delivery
of the same oil, in one table, told apart by a `type` column. Modelled as one entity, *"how
much oil in August"* answers with nearly double the truth and draws a perfectly normal chart
doing it. Entities declare constraints welded into the `WHERE` ahead of anything the model
asks for, so the double-count stops being a mistake anyone can make.

**Every number is recomputed before display.** The chat assistant can be shown the rows
already on screen, but only when two independent switches are open — a server setting and a
per-request browser consent, both defaulting to closed. Any figure it states is recomputed
from the warehouse before it reaches you.

**Partial answers say they're partial.** A breakdown cut off by a row limit is ordered
deterministically and labelled *"showing the top 100; more rows exist"* — because a hundred
rows otherwise looks exactly like a complete answer that happens to have a hundred rows.

---

## Adding an entity

The one recurring task. Coverage is demand-driven: model the questions people ask, not the
warehouse. Ten to twenty entities covers most demand across a hundred tables.

```yaml
entity: oil_mined
label: Oil Mined
table: dm_upstream.daily_production_summary
description: >
  Oil and condensate lifted from the ground, one row per company per day
  per raw material. Quantities are tonnes.
time_column: date

filters:                       # true of every row this entity describes
  - field: type
    value: Mining

dimensions:
  company:
    label: company
    type: string
    column: company_name

measures:
  actual:
    label: actual production
    agg: sum
    column: day_actual
    description: Tonnes produced that day.

derived:
  attainment:
    label: plan attainment
    formula: actual / plan * 100    # arithmetic over MEASURES, not columns

synonyms:
  actual: [actual production, mined, lifted]
```

The schema is the prefix of `table:` — never written twice. Then `make validate-all` and
`make test`.

**Four rules the tests enforce**, each learned by breaking it:

| Rule | What goes wrong |
|---|---|
| No physical column name in the prompt — **including in `synonyms`** | The model must work in layer vocabulary. A measure `losses` over a column `losses` fails; it's a substring check across the whole prompt |
| Don't claim bare common nouns as synonyms | The cross-schema guard reads measures. Claiming `production` makes that word *belong* to your schema, and other dashboards get told their own subject lives elsewhere |
| Never `agg: sum` a running total or a stock level | A month-to-date column summed across 30 days gives ~15× the truth, on a chart that looks entirely normal |
| Nothing sensitive in a `values:` list | Every word of a layer file is sent to the model on every question |

### Describing an unfamiliar schema

```bash
docker compose exec backend python /scripts/describe_schema.py YOUR_SCHEMA -o /scripts/out.md
```

Reads the catalogue and `pg_stats` only — no table scan, whatever the size. Prints column
types, nullability, cardinality and declared keys, but **no value from any column** unless
you pass `--values`. Enough to tell a dimension from an identifier without naming anything.

---

## Connecting a real database

```bash
PROFILE_URL=postgresql://readonly_user:PASSWORD@host:5432/database
DEFAULT_SCHEMA=your_schema
VISIBLE_SCHEMAS=your_schema      # allowlist; empty means everything readable
```

Three DSNs, deliberately separate:

| Variable | Points at | Privilege |
|---|---|---|
| `WAREHOUSE_URL` (via `PROFILE_URL`) | the data you query | **SELECT only**, read-only session, 30 s statement timeout |
| `APP_URL` | dashboards and cards | owns its own schema |
| `ADMIN_URL` | migrations | **never the warehouse** — migrations run DDL |

The app asserts at startup that its warehouse connection is read-only and **refuses to
start** if it isn't. Give it a SELECT-only role anyway: that assertion is the last line of
defence, not the first.

> ⚠️ `WAREHOUSE_URL` is composed in `docker-compose.yml`, where `environment:` overrides
> `env_file:`. Set `PROFILE_URL`; setting `WAREHOUSE_URL` in `.env` silently does nothing.

### What leaves your network

| Surface | What is sent |
|---|---|
| Query path | The layer only — table names, column names, labels, declared `values:` domains |
| Chart compilation | Nothing. No model involved |
| Chat, both gates open | Row values from the cards on screen |

Set `CHAT_SEES_DATA=false` (the default) and no row value can reach a provider, whatever a
browser asks for. For fully local operation, point the NVIDIA provider at a self-hosted
vLLM or Ollama endpoint — it speaks the OpenAI wire protocol.

**There is no authentication.** Anyone who reaches port 8000 sees every dashboard. Bind it
to localhost, or put an auth proxy in front, before exposing it to anyone.

---

## Stack

Python 3.12 · FastAPI · psycopg3 · Pydantic · PostgreSQL · React · TypeScript · Vega-Lite ·
Docker Compose. **1049 backend tests, 165 frontend, no type errors.**

```
backend/app/
  layer/          entity definitions (YAML), models, schema scoping
  semantic/       query grammar, compiler, validation, restatement
  llm/            provider clients and prompt building — never touches the database
  chat/           the assistant: plans, confirmation, undo, context
  render.py       compile → execute → chart spec → sentence
backend/eval/     accuracy harness and fixtures
frontend/src/     React + Vega-Lite
db/               init SQL, migrations, synthetic seed
scripts/          schema profiler, test-db bootstrap, type generation
docs/design.md    why each decision was made
```

---

## Status and limits

A working prototype, built as a learning project and exercised against a real distributed
warehouse. Honest about what it isn't:

- **No authentication or per-user isolation.** Dashboards are global.
- **The layer is hand-written.** An automated profiler was specified and never built, so
  covering a new schema is human work.
- **Expressiveness has a ceiling.** No arbitrary joins, no subqueries, no free-form
  expressions — by design, and it will sometimes refuse something reasonable.
- **Not a Metabase replacement.** Metabase is free, self-hostable and better at everything
  an analyst needs. This targets the manager who won't open Metabase, and the architecture
  is the point.

MIT licensed. Issues and questions welcome.
