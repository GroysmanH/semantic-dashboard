<div align="center">

# Semantic Dashboard

### Ask a question in plain language. Get a chart you can trust.

**The model never writes SQL, and never sees a row while writing a query.**

[Why](#why-this-exists) · [Results](#does-it-actually-work) · [Try it](#try-it-in-two-minutes) · [How it works](#how-it-works) · [Adding an entity](#adding-an-entity)

</div>

![A dashboard of four charts built from plain-language questions](docs/images/dashboard.png)

---

## Where this came from

Built during an internship at KazMunayGas, for managers who needed answers from a data
warehouse and could not write SQL — and who, reasonably, would not trust a chart they
couldn't check.

**This public version contains no company data of any kind.** No real schema, table or
column names; no business data; no operational details. It ships with a synthetic
oil-field warehouse that generates itself on first run — 200 wells and two years of daily
production — so everything you see below is reproducible on your own machine in two
minutes.

What survived the move is the part that was worth keeping: **the architecture**, and the
measurements showing it works.

---

## Why this exists

Point an LLM at a database schema and ask it for SQL, and it will give you SQL. That's the
problem. The query runs, a chart appears, and nothing on screen tells you the model guessed
what `dayfact_v2` meant, joined on the wrong key, or silently summed a month-to-date
column across thirty days.

For a manager who can't read SQL, a wrong chart is indistinguishable from a right one.

This project takes the opposite approach: **narrow what the model can say until every
sentence it can produce is one somebody already verified.** It picks from a hand-written
menu of entities, measures and dimensions — a *semantic layer* — and returns a structured
object naming its choice. A deterministic compiler turns that into SQL.

|  | Text-to-SQL | This |
|---|---|---|
| Model outputs | SQL | An object: entity, measures, dimensions, filters |
| Column meanings | inferred from names | declared once, by a human |
| Bad question | plausible wrong answer | explicit refusal |
| SQL injection | mitigated | impossible — no string concatenation exists |
| Unverified field | queried anyway | blocks the whole entity until confirmed |

---

## Does it actually work?

Benchmarked against a raw text-to-SQL baseline — **same model, same 60 questions, same
database**:

| | Semantic layer | Raw text-to-SQL |
|---|---|---|
| **Execution accuracy** | **88%** | 44% |
| Correct refusals | **88%** | — |
| Right chart chosen | **85%** | n/a — returns rows, not pictures |

Run it yourself with `make eval`. Every miss is listed in
[`docs/eval-results.md`](docs/eval-results.md), including the four charts it drew wrong.

---

## The interface is one text box

No query builder, no drag-and-drop, no field picker. A card starts empty with a prompt and
a few suggestions, and that is the entire learning curve.

Ask something clear and you get a chart. Ask something ambiguous and **it asks you back
instead of guessing**:

![A card responding: 'Profitable' could mean highest absolute production volume (actual_oil) or best performance against plan (attainment). Which measure did you intend?](docs/images/asks-back.png)

That is the whole design in one screenshot. *"Which region is the most profitable?"* has no
profit measure behind it, so rather than substituting revenue and drawing a confident
chart, the card names the two measures it does have and waits. You answer in the same box —
it remembers what it asked.

### Every card shows its work

One click opens the exact SQL that produced the numbers. No black box: if a manager
doubts a figure, an analyst can read the query, run it, and check.

![A bar chart with its compiled SQL expanded underneath, showing quoted identifiers, a declared join and a deterministic ORDER BY](docs/images/compiled-sql.png)

Note the plain-English sentence under the title — *"Sum of oil production, by region, from
Daily Production"* — and the row count, freshness and cache state beside it. A partial
answer says so: cut a breakdown off at the row limit and the sentence reads *"showing the
top 100; more rows exist"*, because a hundred rows otherwise looks exactly like a complete
answer that happens to have a hundred rows.

### Charts follow the shape of the data

Column types and cardinality choose the encoding — line for time, bar for categories,
scatter for two measures, map when the entity declares coordinates. Values only get a say
in three judgement calls: whether a pie has too many slices, whether a third dimension is
sparse enough to facet, whether two measures span enough categories to scatter.

![Four cards showing a geographic bubble map, a scatter plot, a period-change line and a stacked bar chart](docs/images/chart-types.png)

### An assistant, with the data tap closed by default

A side panel can build and edit cards for you, and answer questions about what's on screen.
Whether it may read the actual numbers is a switch — and it's off unless two independent
gates are open, one on the server and one in the browser:

<img src="docs/images/chat-gates.png" width="420" alt="Assistant input with model picker, a Think harder toggle, and a toggle reading 'Reading the numbers is off'">

With it off the assistant still works — it just reasons about structure rather than values.
Every figure it does state is recomputed from the warehouse before you see it.

---

## Try it in two minutes

```bash
git clone https://github.com/GroysmanH/semantic-dashboard.git
cd semantic-dashboard
cp .env.example .env          # add one LLM API key
make up
```

Open <http://localhost:5173> and type *"oil production by region"*.

The bundled Postgres seeds itself on first boot, so there is no external database to
configure. Works with Anthropic, Google, OpenAI or NVIDIA — each question runs on a cheap
model by default, with a "this one is hard" toggle that escalates to a stronger one.

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
dimensions and filters — never SQL, never a raw column name.

**3. The compiler builds the query.** Every identifier is quoted, every value bound as a
parameter. Injection isn't mitigated; concatenation never happens.

**4. The chart follows the result's shape**, then the sentence describes it in English.

### Design decisions worth knowing

Each was made against a specific failure. [`docs/design.md`](docs/design.md) argues them
all in full.

**Refusing is a feature.** An unanswerable question gets a refusal naming what the layer
*does* have. This is the whole point, not a limitation of it.

**Unverified fields block the entity.** Mark a field `confidence: low` and every query on
that entity refuses, naming the field. Use it freely while you're still learning a schema —
a missing measure is a question someone asks; a wrong measure is an answer nobody questions.

**One dashboard, one schema.** Narrowing the menu makes a model *more* dangerous, not less:
shown only a planning mart and asked about production, it has no right answer available,
and a model with no right answer picks the closest wrong one. A deterministic guard catches
that and refuses by name — **before any API call**, in about 19 ms.

**An entity can be a slice of its table.** Warehouses stack facts: production and delivery
of the same oil, in one table, told apart by a `type` column. Modelled as one entity, *"how
much oil in August"* answers with nearly double the truth and draws a perfectly normal chart
doing it. Entities declare constraints welded into the `WHERE` ahead of anything the model
asks for.

**Ordering is never left to chance.** A grouped query without `ORDER BY` returns whichever
rows the database felt like — on a distributed warehouse, a *different set* each run. Every
grouped query gets a deterministic order.

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

### Describing a schema you don't know

```bash
docker compose exec backend python /scripts/describe_schema.py YOUR_SCHEMA -o /scripts/out.md
```

Reads the catalogue and `pg_stats` only — **no table scan**, whatever the size. Prints
types, nullability, cardinality and declared keys, but no value from any column unless you
pass `--values`. Enough to tell a dimension from an identifier without naming anything.

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
| Assistant, both gates open | Row values from the cards on screen |

`CHAT_SEES_DATA=false` (the default) means no row value can reach a provider, whatever the
browser asks for. For a fully local setup, point the NVIDIA provider at a self-hosted vLLM
or Ollama endpoint — it speaks the OpenAI wire protocol.

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

A working prototype, exercised against a real distributed warehouse. Honest about what it
isn't:

- **No authentication or per-user isolation.** Dashboards are global.
- **The layer is hand-written.** An automated profiler was specified and never built, so
  covering a new schema is human work.
- **Expressiveness has a ceiling.** No arbitrary joins, no subqueries, no free-form
  expressions — by design, and it will sometimes refuse something reasonable.
- **Not a Metabase replacement.** Metabase is free, self-hostable and better at everything
  an analyst needs. This targets the manager who won't open Metabase, and the architecture
  is the point.

MIT licensed. Issues and questions welcome.
