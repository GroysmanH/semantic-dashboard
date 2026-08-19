"""Run the eval and emit the results table.

Four metrics for the semantic arm, one for the baseline:

  exact       canonical() dicts equal
  relaxed     equal ignoring the order of measures, dimensions and filters
  execution   both queries run, result sets equal as sorted tuples
  chart       the chart build_spec actually produced matches the fixture

Chart match scores the built chart, not the model's hint. The hint is a
suggestion the builder is free to overrule -- it does exactly that when a
pie would be meaningless -- so scoring the hint would measure the request
rather than the picture. The expectations were written from what each
question deserves, before the builder could produce any of it; reading them
off build_spec would score 100% forever, since it is a deterministic
function of the query and test_chart.py already covers that for free.

Execution match is the only metric that compares the two arms fairly:
comparing SQL strings is meaningless, so both sides are judged on what
they actually return.

Usage:  python -m eval.run_eval [--provider gemini] [--models a,b]
                               [--out docs/eval-results.md]
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

from app.config import settings
from app.db import close_pools, open_pools, warehouse_pool
from app.deps import LAYER, SYNONYMS
from app.llm.client import LLMRateLimited, make_client
from app.llm.query_step import ask
from app.render import render
from app.semantic.compile import compile_query
from app.semantic.query import SemanticQuery

from .baseline import run_baseline

SUITES = {
    "queries": Path(__file__).parent / "fixtures.yaml",
    "viz": Path(__file__).parent / "fixtures_viz.yaml",
}
FIXTURES = SUITES["queries"]

# Three tiers per provider, so the interesting comparison -- does the
# grammar let a small model do a big model's job? -- is available on both.
MODELS = {
    "anthropic": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
    "gemini": ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"],
    "openai": ["gpt-5", "gpt-5-mini", "gpt-5-nano"],
    "nvidia": ["deepseek-ai/deepseek-v4-pro"],
}


# Free tiers meter by the minute. A 429 is a fact about the clock, not
# about the model's answer, so scoring it as a miss would quietly report
# the quota as accuracy.
RETRIES = 5
BACKOFF = 20            # seconds, doubled each attempt


def patiently(call, what: str):
    """Run `call`, waiting out rate limits rather than counting them."""
    delay = BACKOFF
    for attempt in range(RETRIES):
        try:
            return call()
        except LLMRateLimited:
            if attempt == RETRIES - 1:
                raise
            print(f"  rate limited on {what}; waiting {delay}s",
                  file=sys.stderr, flush=True)
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")            # pragma: no cover


def relaxed_match(got: SemanticQuery, expected: SemanticQuery,
                  raw_expected: dict) -> bool:
    """Equal on everything the fixture actually specified.

    List order never matters. Beyond that, a field the fixture left at its
    default is not compared: when the fixture author expressed no opinion
    about ordering, a model that adds a sensible `order_by` has not made a
    mistake, and whether the rows come back the same is what `execution`
    is for. Where the fixture *does* specify ordering or a limit -- "top 5
    fields by oil" -- both are compared, so a model that drops the ranking
    is not quietly forgiven.
    """
    if got.entity != expected.entity:
        return False
    def measures(q):
        # Objects now, not strings: a transform is part of what the measure
        # *is*, so two queries naming `oil` differ if one takes a running
        # total. Sorted by the serialised form so order still does not count.
        return sorted(m.model_dump_json() for m in q.measures)

    if measures(got) != measures(expected):
        return False

    def dims(q):
        return sorted((d.field, d.grain) for d in q.dimensions)

    def filters(q):
        return sorted((f.field, f.op, repr(f.value)) for f in q.filters)

    if dims(got) != dims(expected) or filters(got) != filters(expected):
        return False

    if "order_by" in raw_expected:
        if sorted((o.field, o.dir) for o in got.order_by) != \
           sorted((o.field, o.dir) for o in expected.order_by):
            return False
    if "limit" in raw_expected and got.limit != expected.limit:
        return False
    return True


def normalise(rows) -> list[tuple]:
    """Result sets compare as sorted tuples of stringified values: column
    order and row order are not part of being right."""
    out = []
    for row in rows or []:
        vals = [f"{float(v):.4f}" if isinstance(v, (Decimal, float))
                else ("" if v is None else str(v)) for v in row]
        out.append(tuple(sorted(vals)))
    return sorted(out)


def execute(q: SemanticQuery) -> list[tuple] | None:
    try:
        compiled = compile_query(q, LAYER)
        with warehouse_pool.connection() as conn, conn.cursor() as cur:
            cur.execute(compiled.sql, compiled.params)
            return cur.fetchall()
    except Exception:                    # noqa: BLE001
        return None


@dataclass
class Tally:
    total: int = 0
    exact: int = 0
    relaxed: int = 0
    execution: int = 0
    exec_comparable: int = 0
    exec_skipped: int = 0
    chart: int = 0
    chart_total: int = 0
    chart_misses: list[str] = field(default_factory=list)
    refusals_total: int = 0
    refusals_correct: int = 0
    clarifies: int = 0
    retries: int = 0
    base_execution: int = 0
    base_comparable: int = 0
    base_errors: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    exec_only_failures: list[str] = field(default_factory=list)
    seconds: float = 0.0


def evaluate(model: str, fixtures: list[dict], *, with_baseline: bool,
             provider: str) -> Tally:
    t = Tally()
    client = make_client(provider, model=model)
    started = time.time()

    for fx in fixtures:
        question = fx["question"]
        should_refuse = fx.get("expect") == "refused"
        outcome = patiently(
            lambda: ask(question, LAYER, client, synonyms=SYNONYMS),
            f"{fx['id']} ({model})")
        t.retries += max(0, outcome.attempts - 1)

        if should_refuse:
            t.refusals_total += 1
            # A clarifying question is an acceptable way to decline: it
            # still refuses to draw a confidently wrong chart.
            if outcome.query is None:
                t.refusals_correct += 1
            else:
                t.failures.append(f"{fx['id']}: answered a question it should "
                                  f"have declined ({fx.get('because', '')})")
            continue

        t.total += 1
        if outcome.clarify:
            t.clarifies += 1
            t.failures.append(f"{fx['id']}: asked for clarification")
            continue
        if outcome.query is None:
            t.failures.append(f"{fx['id']}: refused — {outcome.refusal}")
            continue

        expected = SemanticQuery.model_validate(fx["expected"])
        got = outcome.query

        if got.canonical() == expected.canonical():
            t.exact += 1
        matched_relaxed = relaxed_match(got, expected, fx["expected"])
        if matched_relaxed:
            t.relaxed += 1

        # Chart match, scored over the fixtures that declare one. The
        # chart is built from the model's own query and its own rows, so
        # this measures the picture a person would actually see.
        if "expected_chart" in fx:
            # Scored on the model's own hint, never the fixture's. The
            # fixture's `hint` records what a correct model would say and
            # exists for the offline reconciliation; using it here would
            # hand the model an answer it was supposed to produce.
            t.chart_total += 1
            drawn = render(got, LAYER, chart_hint=outcome.chart_hint)
            built = drawn.chart_type or "unplottable"
            if built == fx["expected_chart"] and (
                    "expected_hint_rejected" not in fx
                    or drawn.hint_rejected == fx["expected_hint_rejected"]):
                t.chart += 1
            else:
                t.chart_misses.append(
                    f"{fx['id']}: wanted {fx['expected_chart']}, drew {built}"
                    + (f" (hint {outcome.chart_hint!r}"
                       f"{', overruled' if drawn.hint_rejected else ''})"
                       if outcome.chart_hint or drawn.hint_rejected else ""))

        want_rows = execute(expected)
        got_rows = execute(got)

        # A fixture whose expected result is truncated by a limit with no
        # ordering has no stable answer to compare against: Postgres returns
        # an arbitrary hundred rows. Comparing result sets against a
        # non-deterministic target measures nothing, so those are excluded
        # from execution scoring for both arms rather than counted as misses.
        unstable = (want_rows is not None
                    and len(want_rows) >= expected.limit
                    and not expected.order_by)
        if unstable:
            t.exec_skipped += 1
            continue

        t.exec_comparable += 1
        if want_rows is not None and normalise(want_rows) == normalise(got_rows):
            t.execution += 1
        elif not matched_relaxed:
            t.failures.append(
                f"{fx['id']}: got {got.model_dump_json(exclude_defaults=True)}")
        else:
            # Matched on everything the fixture specified, yet returned
            # different rows. This is the interesting failure: an added
            # order_by changes *which* rows survive the default limit, so
            # the card shows a different hundred. Reporting relaxed without
            # this would hide it.
            t.exec_only_failures.append(
                f"{fx['id']}: same intent, {len(want_rows or [])} vs "
                f"{len(got_rows or [])} rows — "
                f"{got.model_dump_json(exclude_defaults=True)}")

        if with_baseline:
            # A fixture whose result is truncated by a limit nobody asked for
            # cannot be compared fairly: the semantic arm stops at its default
            # 100 rows, the raw SQL has no LIMIT at all, and the two sets
            # differ for that reason alone. Scoring that against the baseline
            # would flatter the semantic arm for free.
            # Unstable fixtures already `continue`d above, so both arms are
            # scored over exactly the same comparable set.
            b = patiently(lambda: run_baseline(question, model, provider),
                          f"{fx['id']} baseline ({model})")
            t.base_comparable += 1
            if b.error_kind:
                t.base_errors[b.error_kind] = t.base_errors.get(b.error_kind, 0) + 1
            elif want_rows is not None and normalise(want_rows) == normalise(b.rows):
                t.base_execution += 1

    t.seconds = time.time() - started
    return t


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def report(results: dict[str, Tally], with_baseline: bool,
           provider: str) -> str:
    lines = [
        "# Eval results",
        "",
        f"Provider: **{provider}**.",
        "",
        f"{next(iter(results.values())).total} answerable questions and "
        f"{next(iter(results.values())).refusals_total} that must be refused, "
        "run against the semantic layer.",
        "",
        (lambda n: f"Execution is scored over "
                   f"{next(iter(results.values())).exec_comparable} of them: "
                   f"{n} {'is' if n == 1 else 'are'} excluded because the "
                   "expected query is truncated by a limit with no ordering, so "
                   "Postgres returns an arbitrary hundred rows and there is no "
                   "stable answer to compare against."
         )(next(iter(results.values())).exec_skipped),
        "",
        "Exact is byte-identical intent. Relaxed ignores list order and any "
        "field the fixture left at its default. Execution compares the result "
        "sets the two queries actually return, and is the only metric that "
        "compares fairly against the raw text-to-SQL arm.",
        "",
        "| Model | Exact | Relaxed | Execution | Chart | Correct refusals | Retries |"
        + (" Raw text-to-SQL |" if with_baseline else ""),
        "|---|---|---|---|---|---|---|" + ("---|" if with_baseline else ""),
    ]
    for model, t in results.items():
        row = (f"| `{model}` | {pct(t.exact, t.total)} | {pct(t.relaxed, t.total)} "
               f"| {pct(t.execution, t.exec_comparable)} "
               f"| {pct(t.chart, t.chart_total)} "
               f"| {pct(t.refusals_correct, t.refusals_total)} | {t.retries} |")
        if with_baseline:
            row += f" {pct(t.base_execution, t.base_comparable)} |"
        lines.append(row)

    if with_baseline:
        lines += ["", "## Structural errors in the raw text-to-SQL arm", "",
                  f"Scored over the same "
                  f"{next(iter(results.values())).base_comparable} comparable "
                  "questions.", "",
                  "| Model | Invalid SQL | Missing table or column | Unreachable |",
                  "|---|---|---|---|"]
        for model, t in results.items():
            e = t.base_errors
            lines.append(f"| `{model}` | {e.get('invalid_sql', 0)} | "
                         f"{e.get('missing_object', 0)} | {e.get('unreachable', 0)} |")

    if any(t.chart_total for t in results.values()):
        lines += ["", "The raw text-to-SQL arm has no chart column because it "
                  "has no charts: it returns rows. Producing a visualisation "
                  "from them is the work this design does and that one does "
                  "not, which is worth stating rather than scoring as zero.",
                  ""]

    for model, t in results.items():
        if t.chart_misses:
            lines += ["", f"## Wrong chart — `{model}`", ""]
            lines += [f"- {m}" for m in t.chart_misses]
        if t.failures:
            lines += ["", f"## Misses — `{model}`", ""]
            lines += [f"- {f}" for f in t.failures]
        if t.exec_only_failures:
            lines += ["", f"## Right intent, different rows — `{model}`", "",
                      "The query matched on everything the fixture specified but "
                      "returned a different result set. An added `order_by` "
                      "changes which rows survive the default limit, so the card "
                      "shows a different hundred.", ""]
            lines += [f"- {f}" for f in t.exec_only_failures]

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=settings.eval_provider,
                    choices=sorted(MODELS),
                    help="which API to run both arms against")
    ap.add_argument("--suite", default="both",
                    choices=[*SUITES, "both"],
                    help="queries (translation + refusals), viz "
                         "(charts + transforms), or both")
    ap.add_argument("--models", default=None,
                    help="comma-separated model ids; defaults to the "
                         "provider's three tiers")
    ap.add_argument("--out", default="/docs/eval-results.md")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the first N fixtures (for a smoke check)")
    args = ap.parse_args()

    models = (args.models.split(",") if args.models
              else MODELS[args.provider])

    chosen = list(SUITES) if args.suite == "both" else [args.suite]
    fixtures = [fx for name in chosen
                for fx in yaml.safe_load(SUITES[name].read_text())]
    if args.limit:
        fixtures = fixtures[:args.limit]

    open_pools()
    try:
        results = {
            model: evaluate(model, fixtures,
                            with_baseline=not args.no_baseline,
                            provider=args.provider)
            for model in models
        }
    finally:
        close_pools()

    text = report(results, with_baseline=not args.no_baseline,
                  provider=args.provider)
    print(text)
    out = Path(args.out)
    if out.parent.exists():
        out.write_text(text)
        print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
