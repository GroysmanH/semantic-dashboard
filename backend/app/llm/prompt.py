"""Layer -> system prompt.

Two properties matter and both are tested. It contains no row-level data,
which is the project's headline claim. And it is byte-stable across
requests, without which prompt caching silently never hits.
"""

from __future__ import annotations

from ..layer.models import Layer
from ..semantic.query import MAX_WINDOW

PREAMBLE = """\
You translate a manager's question into a semantic query against a curated \
semantic layer. You never write SQL. You never invent an entity, dimension, \
measure, join or value that is not listed below.

Rules:
- Choose exactly one entity. Every measure and dimension must belong to it.
- At most three dimensions. Prefer a date dimension plus one or two \
breakdowns; the third is drawn as small multiples, so it must be a field \
with few distinct values.
- Give a date dimension a grain from its listed grains.
- Filters may only use a listed field, and where a field lists its values, \
only those values.
- The current date is supplied with each question. Resolve every relative \
period against it and never guess a year: "last year" and "this year" are \
in_year with the resolved year, "the last 30 days" is last_n_days, and an \
explicit span is between.
- order_by may only name a measure or dimension you selected. A transformed \
measure is named by its output column, not its base name — a running total \
of `oil` is ordered by `oil_cumulative`.
- If the question cannot be answered with the fields below, still return your \
closest attempt and set `ambiguity` explaining what is missing.
- If a term in the question could mean two different fields, set `ambiguity` \
with both candidates rather than guessing.
- chart_hint is optional and is usually null. The chart is already chosen \
from the shape of the query, and that choice accounts for what a transform \
means: a running total fills, a signed change gets a zero baseline, a share \
is drawn as a percentage. Give a hint only when the question names a shape \
in so many words — "as a pie chart", "on a map", "as stacked bars". Do not \
give one because a shape seems suitable. "Month over month change" and \
"running total" name no shape, and a hint there overrides a better default.
- title is a short label for the card, in the manager's own words.

Measures may be written as a plain name, or as an object with a transform:
  "oil"
  {"name": "oil", "transform": "percent_of_total"}
  {"name": "gas", "transform": "ratio", "per": "oil"}
  {"name": "oil", "transform": "moving_average", "window": 3}

Transforms available:
- percent_of_total — this group's share, within each period if the query has \
a date dimension, otherwise of the whole result.
- previous_period — the same measure one period earlier. Needs a date \
dimension with a grain.
- period_change — the change against the previous period. Needs a date grain.
- period_change_pct — that change as a fraction of the previous period. This \
is (current - previous) / previous, NOT current / previous.
- cumulative — a running total along the date dimension. Needs a date grain.
- moving_average — the mean over the last `window` periods. Needs a date grain.
- rank — this group's position by the measure, highest first.
- ratio — this measure divided by another. Set `per` to the other measure's \
name. Both must be measures on the same entity. Use this for any plain \
arithmetic between two measures; only definitions with an agreed business \
meaning are listed separately as measures of their own.

Chart hints available: line, bar, area, point, heatmap, big_number, pie, \
donut, stacked_bar, normalised_bar, scatter, bubble, map.

The entities available are:
"""


def build_system_prompt(layer: Layer) -> str:
    """Rendered deterministically: sorted keys, no timestamps, no data."""
    out = [PREAMBLE]

    for name in sorted(layer):
        e = layer[name]
        out.append(f"\n## entity: {name}")
        if e.description:
            out.append(f"description: {e.description}")
        if e.has_low_confidence:
            out.append("NOTE: this entity has unverified fields and is "
                       "currently refused at query time.")

        out.append("dimensions:")
        for key in sorted(e.dimensions):
            d = e.dimensions[key]
            bits = [f"  - {key} ({d.type})"]
            if d.grains:
                bits.append(f"grains: {', '.join(d.grains)}")
            if d.values:
                bits.append(f"values: {', '.join(d.values)}")
            out.append("; ".join(bits))

        out.append("measures:")
        for key in sorted(e.measures):
            m = e.measures[key]
            line = f"  - {key} ({m.agg} of {m.label})"
            if m.description:
                line += f" — {m.description}"
            out.append(line)

        if e.derived:
            # Listed apart from the base measures so it is visible that
            # these carry an agreed definition rather than being arithmetic
            # the model could have composed itself.
            out.append("measures with an agreed definition "
                       "(use these rather than composing them):")
            for key in sorted(e.derived):
                d = e.derived[key]
                line = f"  - {key} ({d.label})"
                if d.description:
                    line += f" — {' '.join(d.description.split())}"
                out.append(line)

        if e.synonyms:
            out.append("synonyms:")
            for key in sorted(e.synonyms):
                out.append(f"  - {key}: {', '.join(sorted(e.synonyms[key]))}")

    out.append("\nFilter operators: =, !=, in, between, in_year, last_n_days.")
    out.append(f"A moving_average window is between 2 and {MAX_WINDOW}.")
    return "\n".join(out)
