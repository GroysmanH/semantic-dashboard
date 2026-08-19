# Eval results

> **Stale.** These numbers predate the fix that excludes non-deterministic
> targets from the execution metric, so execution is understated. Re-run with
> `make eval` to refresh.

24 answerable questions and 6 that must be refused, run against the semantic layer.

Exact is byte-identical intent. Relaxed ignores list order and any field the fixture left at its default. Execution compares the result sets the two queries actually return, and is the only metric that compares fairly against the raw text-to-SQL arm.

| Model | Exact | Relaxed | Execution | Correct refusals | Retries | Raw text-to-SQL |
|---|---|---|---|---|---|---|
| `claude-opus-5` | 29% | 96% | 83% | 100% | 0 | 35% |
| `claude-sonnet-5` | 33% | 100% | 88% | 100% | 0 | 67% |
| `claude-haiku-4-5` | 75% | 88% | 88% | 83% | 0 | 48% |

## Structural errors in the raw text-to-SQL arm

Scored over the 20 questions where the comparison is fair; 3 are excluded because the semantic arm truncates them at a default limit the raw SQL has no way to know about.

| Model | Invalid SQL | Missing table or column | Unreachable |
|---|---|---|---|
| `claude-opus-5` | 0 | 0 | 0 |
| `claude-sonnet-5` | 0 | 0 | 0 |
| `claude-haiku-4-5` | 0 | 0 | 0 |

## Misses — `claude-opus-5`

- oil_atyrau: asked for clarification

## Misses — `claude-haiku-4-5`

- oil_atyrau: got {"entity":"production","measures":["oil"],"dimensions":[{"field":"reading_date","grain":"day"}],"filters":[{"field":"region","op":"=","value":"Atyrau"}]}
- gas_by_region_2026: got {"entity":"production","measures":["gas"],"dimensions":[{"field":"region"},{"field":"reading_date","grain":"year"}],"filters":[{"field":"reading_date","op":"in_year","value":2026}]}
- downtime_last_30: got {"entity":"production","measures":["downtime"],"dimensions":[{"field":"reading_date","grain":"day"},{"field":"region"}],"filters":[{"field":"reading_date","op":"last_n_days","value":30}]}
- ratio_across_grains: answered a question it should have declined (cross-grain ratios are deliberately not expressible)
