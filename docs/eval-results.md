# Eval results

Provider: **anthropic**.

52 answerable questions and 8 that must be refused, run against the semantic layer.

Execution is scored over 41 of them: 11 are excluded because the expected query is truncated by a limit with no ordering, so Postgres returns an arbitrary hundred rows and there is no stable answer to compare against.

Exact is byte-identical intent. Relaxed ignores list order and any field the fixture left at its default. Execution compares the result sets the two queries actually return, and is the only metric that compares fairly against the raw text-to-SQL arm.

| Model | Exact | Relaxed | Execution | Chart | Correct refusals | Retries | Raw text-to-SQL |
|---|---|---|---|---|---|---|---|
| `claude-haiku-4-5` | 77% | 88% | 88% | 85% | 88% | 1 | 44% |

## Structural errors in the raw text-to-SQL arm

Scored over the same 41 comparable questions.

| Model | Invalid SQL | Missing table or column | Unreachable |
|---|---|---|---|
| `claude-haiku-4-5` | 0 | 0 | 0 |

The raw text-to-SQL arm has no chart column because it has no charts: it returns rows. Producing a visualisation from them is the work this design does and that one does not, which is worth stating rather than scoring as zero.


## Wrong chart — `claude-haiku-4-5`

- pct_of_total_by_region: wanted bar, drew pie (hint 'pie')
- previous_period_gas: wanted line, drew bar (hint 'bar')
- period_change_oil: wanted bar, drew line (hint 'line')
- cumulative_oil_2026: wanted area, drew line (hint 'line')

## Misses — `claude-haiku-4-5`

- oil_atyrau: got {"entity":"production","measures":[{"name":"oil"}],"dimensions":[{"field":"reading_date","grain":"month"}],"filters":[{"field":"region","op":"=","value":"Atyrau"}]}
- oil_between_dates: got {"entity":"production","measures":[{"name":"oil"}],"dimensions":[{"field":"reading_date","grain":"day"}],"filters":[{"field":"reading_date","op":"between","value":["2025-01-01","2025-12-31"]}]}
- gas_by_region_2026: got {"entity":"production","measures":[{"name":"gas"}],"dimensions":[{"field":"region"},{"field":"reading_date","grain":"year"}],"filters":[{"field":"reading_date","op":"in_year","value":2026}]}
- downtime_last_30: got {"entity":"production","measures":[{"name":"downtime"}],"dimensions":[{"field":"region"},{"field":"reading_date","grain":"day"}],"filters":[{"field":"reading_date","op":"last_n_days","value":30}]}
- period_change_oil: got {"entity":"production","measures":[{"name":"oil","transform":"period_change_pct"}],"dimensions":[{"field":"reading_date","grain":"month"}]}
- four_dimensions: answered a question it should have declined (four dimensions has no encoding left after x, y, colour and facet)

## Reconciliation — intent versus the builder

The `expected_chart` values in `fixtures_viz.yaml` were written from what each
question deserves, and committed (`910e4c9`) before any of the builder existed
(`822345c`). Reading them off `build_spec` would have scored 100% forever, since
it is a deterministic function of the compiled query — a thing `test_chart.py`
already covers for free.

Run the expected queries through the builder and **all 27 answerable fixtures
agree**. That is a regression net rather than outside validation: git proves the
ordering, but one session wrote both the fixtures and the rules, so the author is
not independent. It is pinned by
`test_the_builder_agrees_with_what_each_question_deserves`, which needs no model
call and therefore costs nothing to keep true.

The useful consequence is diagnostic. Because the builder agrees with intent on
every fixture, **every entry in the "wrong chart" list above is the model
choosing a different hint, not the shape rules misfiring.** The chart column is
currently measuring the model's hint discipline more than it measures the
builder.

### The four disagreements

Three of the four share one cause: the model volunteered a `chart_hint` for a
question that never asked for a chart shape, and an explicit hint outranks the
transform-aware default.

| Fixture | Intended | Drawn | Hint | Verdict |
|---|---|---|---|---|
| `period_change_oil` | `bar` | `line` | `line` | **Model.** "Month-over-month change" names no shape. Signed deltas are discrete, and a line implies a continuity between points that is not there. |
| `cumulative_oil_2026` | `area` | `line` | `line` | **Model.** "Running total" names no shape either. A filling quantity reads as filled. |
| `previous_period_gas` | `line` | `bar` | `bar` | **Model.** Two series over twenty-four months as paired bars is forty-eight bars where two lines would do. |
| `pct_of_total_by_region` | `bar` | `pie` | `pie` | **Neither, and the fixture is the weaker half.** A share of five regions is a defensible pie and a defensible bar. I wrote `bar` because five values compare better as lengths than as angles; the model read "what percentage of total" as a part-of-whole question, which it is. Left as a miss rather than edited to match, because moving the expectation toward the answer is how this metric stops meaning anything. |

Nothing here is a builder bug, and I have not changed a shape rule in response.
The first three point at the prompt: it says to give a hint "only if the question
asks for a specific chart shape", then offers *"trend"* as an example — which is
not a shape, and reads as licence to volunteer one.

### The refusal that was not

`four_dimensions` ("oil by month, region, well type and field") was answered
rather than declined. Four dimensions cannot be represented — `max_length` is
three — so the model had two honest moves: set `ambiguity`, or return its closest
attempt and say what it dropped. It silently dropped one and answered.

That is the failure this whole design is pointed at, surviving in the one place
the grammar cannot catch it: the grammar stops a query naming four dimensions,
but nothing stops a model naming three when it was asked for four. Worth
recording rather than smoothing over.

## What the comparison actually shows

The raw text-to-SQL arm wrote **zero** invalid queries, referenced **zero**
missing tables or columns, and failed to reach the database **zero** times, over
41 questions — and still returned the right rows 44% of the time against the
semantic layer's 88%.

So the case for this design is not that a model cannot write valid SQL. It
plainly can. The case is that a small closed grammar has a small surface to be
wrong on, and every operator added to it gives some of that back. The transforms
and the third dimension added in this round are exactly such a give-back, and the
88% here is the number to watch if more are added.
