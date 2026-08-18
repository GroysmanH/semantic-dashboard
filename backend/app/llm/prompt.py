"""Layer -> system prompt.

Two properties matter and both are tested. It contains no row-level data,
which is the project's headline claim. And it is byte-stable across
requests, without which prompt caching silently never hits.
"""

from __future__ import annotations

from ..layer.models import Layer

PREAMBLE = """\
You translate a manager's question into a semantic query against a curated \
semantic layer. You never write SQL. You never invent an entity, dimension, \
measure, join or value that is not listed below.

Rules:
- Choose exactly one entity. Every measure and dimension must belong to it.
- At most two dimensions. Prefer a date dimension plus one breakdown.
- Give a date dimension a grain from its listed grains.
- Filters may only use a listed field, and where a field lists its values, \
only those values.
- order_by may only name a measure or dimension you selected.
- If the question cannot be answered with the fields below, still return your \
closest attempt and set `ambiguity` explaining what is missing.
- If a term in the question could mean two different fields, set `ambiguity` \
with both candidates rather than guessing.
- chart_hint is optional. Give one only if the question asks for a specific \
chart shape ("as a bar chart", "trend"). Otherwise leave it null.
- title is a short label for the card, in the manager's own words.

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

        if e.synonyms:
            out.append("synonyms:")
            for key in sorted(e.synonyms):
                out.append(f"  - {key}: {', '.join(sorted(e.synonyms[key]))}")

    out.append("\nFilter operators: =, !=, in, between, in_year, last_n_days.")
    return "\n".join(out)
