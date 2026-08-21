"""Chat evaluation: does a turn choose the right action, and is its query right?

A separate runner rather than another entry in run_eval.py's SUITES. The
query suites score one thing — a semantic query against an expected one —
and share a scoring function to do it. A chat turn is scored on three
independent axes that fail for different reasons and get fixed in different
places:

    action accuracy   routing. Wrong action, perfect query: a prompt problem.
    query accuracy    grammar. Right action, wrong query: a layer or
                      vocabulary problem.
    plan accuracy     the second call. Right action, wrong cards named: the
                      detail prompt, not the router.

Averaging them into one number hides all three, which is why they are
reported side by side and never summed.

Nothing here changes a dashboard. A turn that proposes one writes a plan
and stops; applying it is a separate confirmation this suite never sends.
So a fixture expecting `delete_card` is scored on whether the turn said so,
and the card is still there afterwards.

Boards are built for real through the same compile/execute/render path a
live card uses, so the rows a turn sees are rows the warehouse returned.
They are torn down afterwards, including when a run is interrupted.
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app")

from app.chat.turn import TurnRequest, run_turn                    # noqa: E402
from app.config import settings                                    # noqa: E402
from app.db import close_pools, open_pools                         # noqa: E402
from app.deps import LAYER                                         # noqa: E402
from app.llm.client import make_client                             # noqa: E402
from app.render import render                                      # noqa: E402
from app.semantic.query import SemanticQuery                       # noqa: E402
from app.store import cards as store                               # noqa: E402
from app.store import chat as chat_store                           # noqa: E402

from .run_eval import load_fixtures, patiently, relaxed_match      # noqa: E402

FIXTURES = Path("/app/eval/fixtures_chat.yaml")

DIGIT = re.compile(r"\d")

# Actions that are settled inside the turn. Everything else is a change,
# which the turn only proposes: it writes a plan and stops, so the thing to
# score is the plan, not the state of the database afterwards.
SPOKEN = {"answer", "run_query", "clarify", "refuse"}


def build_boards(presets: dict[str, dict]) -> dict[str, dict]:
    """Create every preset board with rendered cards.

    Rendering here rather than faking rows is the point: a fixture that
    tests the prompt against invented data proves nothing about the app.
    """
    built: dict[str, dict] = {}
    for name, preset in presets.items():
        board = store.create_board(f"{preset['title']} [eval]")
        ids: dict[str, str] = {}
        for spec in preset.get("cards") or []:
            card = store.create_card(board["id"])
            query = SemanticQuery.model_validate(spec["query"])
            result = render(query, LAYER, title=spec["title"])
            store.update_card(
                card["id"],
                title=spec["title"],
                semantic_query=query.model_dump(mode="json"),
                vega_spec=result.vega_spec,
                state=result.state,
                cache=result.cache,
            )
            ids[spec["id"]] = str(card["id"])
        built[name] = {"board": board, "cards": ids,
                       "title": preset["title"]}
    return built


def teardown(built: dict[str, dict]) -> None:
    for entry in built.values():
        try:
            store.hard_delete_board(entry["board"]["id"])
        except Exception as exc:                    # noqa: BLE001
            print(f"  could not remove eval board: {exc}", file=sys.stderr)


def routed(message: Any) -> str:
    """Which action the turn actually chose.

    A change that the server then declined is stored as a refusal carrying
    the kind it set out to make. For routing that is the honest reading: a
    model that correctly says "this is a delete_dashboard" has routed
    correctly even when the application refuses to do it.
    """
    if message.action == "refuse" and message.task_kind:
        return message.task_kind
    return message.action


def score(fixture: dict, outcome: Any, built: dict) -> dict[str, Any]:
    """One fixture, scored on each axis it actually declares."""
    message = outcome.message
    action = routed(message)
    plan = getattr(outcome, "pending_plan", None)

    accepted = fixture.get("accepts_actions") or [fixture["expected_action"]]
    result: dict[str, Any] = {
        "id": fixture["id"],
        "tags": (fixture.get("tags") or ["easy"])[0],
        "expected_action": fixture["expected_action"],
        "got_action": action,
        "action_ok": action in accepted,
        "query_ok": None,
        "plan_ok": None,
        "claims_ok": None,
        "detail": "",
    }

    if "expected" in fixture:
        # A run_query turn carries its query on the transient result: that
        # is the object the person sees, restatement and SQL included.
        transient = getattr(outcome, "transient_result", None)
        got = transient.semantic_query if transient else None
        if got is None:
            result["query_ok"] = False
            result["detail"] = "no query on the turn"
        else:
            expected = SemanticQuery.model_validate(fixture["expected"])
            result["query_ok"] = relaxed_match(got, expected,
                                               fixture["expected"])

    # -- the plan axis ---------------------------------------------------
    #
    # Separate from action accuracy because they fail differently: routing
    # to new_cards and then proposing the wrong number of them is a
    # second-stage problem, and merging it into the first would hide which
    # of the two calls went wrong.
    if action not in SPOKEN and result["action_ok"]:
        if plan is None:
            # A change that produced no plan was refused by the server. The
            # routing still counted; there is simply no plan to check.
            result["detail"] = message.refusal or "no plan was written"
        else:
            checks = []
            if "expected_cards" in fixture:
                checks.append(len(plan.cards) == fixture["expected_cards"])
                if not checks[-1]:
                    result["detail"] = (f"proposed {len(plan.cards)} cards, "
                                        f"wanted {fixture['expected_cards']}")
            if "expected_target" in fixture:
                wanted = built["cards"].get(fixture["expected_target"])
                named = {str(o.card_id) for o in plan.operations if o.card_id}
                checks.append(wanted in named)
                if not checks[-1]:
                    result["detail"] = (f"the plan does not name the "
                                        f"{fixture['expected_target']} card")
            if checks:
                result["plan_ok"] = all(checks)

    if "expects_claim" in fixture:
        has = bool(message.claims)
        want = fixture["expects_claim"]
        enough = len(message.claims) >= fixture.get("min_claims", 1)
        result["claims_ok"] = (has and enough) if want else not has
        if want and not has:
            result["detail"] = "no verified claim survived"

    # With the data gate shut, a figure in the answer is a leak of something
    # the model was never given, which is worse than an unhelpful answer.
    if fixture.get("forbids_figures"):
        leaked = bool(DIGIT.search(message.say or ""))
        result["claims_ok"] = not leaked
        if leaked:
            result["detail"] = "stated a figure with values out of scope"

    if fixture.get("expects_missing_metric"):
        result["claims_ok"] = bool(message.missing_metric)
        if not message.missing_metric:
            result["detail"] = "refused without naming the missing metric"

    return result


def run(provider: str, limit: int = 0) -> list[dict]:
    raw = load_fixtures(FIXTURES)
    presets, fixtures = raw["boards"], raw["fixtures"]
    if limit:
        fixtures = fixtures[:limit]

    built = build_boards(presets)
    client = make_client(provider)
    rows: list[dict] = []

    try:
        for fixture in fixtures:
            entry = built[fixture["board"]]
            thread = chat_store.create_thread()
            request = TurnRequest(
                thread_id=thread["id"],
                active_board_id=entry["board"]["id"],
                question=fixture["utterance"],
                provider=provider,
                hard=False,
                # The fixture states the resolved gate directly. Flipping a
                # global setting mid-run would make the suite order-dependent.
                share_visible_data=fixture.get("share_rows", False),
            )
            settings.chat_sees_data = fixture.get("share_rows", False)
            outcome = patiently(
                lambda: run_turn(request, client=client), fixture["id"])
            # Every fixture gets a fresh thread, so a plan left pending
            # cannot block the next one. Cancelling it anyway keeps the
            # database free of plans nobody will ever answer.
            pending = chat_store.get_pending_plan(thread["id"])
            if pending is not None:
                chat_store.transition_plan(pending["id"], expected="pending",
                                           status="cancelled")
            rows.append(score(fixture, outcome, entry))
            mark = "ok  " if rows[-1]["action_ok"] else "MISS"
            print(f"  {mark} {fixture['id']}", file=sys.stderr, flush=True)
    finally:
        teardown(built)

    return rows


def report(provider: str, model: str, rows: list[dict]) -> str:
    def pct(subset, key):
        scored = [r for r in subset if r[key] is not None]
        if not scored:
            return "—"
        return f"{round(100 * sum(bool(r[key]) for r in scored) / len(scored))}%"

    easy = [r for r in rows if r["tags"] == "easy"]
    hard = [r for r in rows if r["tags"] == "hard"]

    out = ["# Chat eval results", "",
           f"Provider **{provider}**, model `{model}`. {len(rows)} turns, "
           f"all of them scored.", "",
           "Four axes, reported apart because they fail for different "
           "reasons and are fixed in different places.", "",
           "- **Action** is routing: which of the twelve things the turn "
           "decided this was. A change is scored on what it set out to do, "
           "so a `delete_dashboard` the server then declines still counts as "
           "routed correctly — declining it is the application's job, and it "
           "has its own tests.",
           "- **Query** is grammar: when the turn ran a query, was it the "
           "right one.",
           "- **Plan** is the second call: having routed to a change, did it "
           "propose the right cards, or name the right card to move or "
           "remove. Kept apart from action accuracy so it is obvious which "
           "of the two calls went wrong.",
           "- **Claims** is verification: did the figures survive being "
           "recomputed from the rows.", "",
           "No turn in this suite changed anything. A change is proposed as "
           "a frozen plan and applied only by a separate confirmation, so "
           "what is scored here is the proposal.", "",
           "| Set | Turns | Action | Query | Plan | Claims |",
           "|---|---|---|---|---|---|"]
    for label, subset in (("easy", easy), ("hard", hard), ("all", rows)):
        out.append(f"| {label} | {len(subset)} | {pct(subset, 'action_ok')} "
                   f"| {pct(subset, 'query_ok')} | {pct(subset, 'plan_ok')} "
                   f"| {pct(subset, 'claims_ok')} |")

    misses = [r for r in rows if r["action_ok"] is False
              or r["query_ok"] is False or r["plan_ok"] is False
              or r["claims_ok"] is False]
    if misses:
        out += ["", "## Misses", ""]
        for r in misses:
            bits = []
            if r["action_ok"] is False:
                bits.append(f"routed to `{r['got_action']}`, "
                            f"wanted `{r['expected_action']}`")
            if r["query_ok"] is False:
                bits.append("query did not match")
            if r["plan_ok"] is False:
                bits.append(r["detail"] or "the plan did not match")
            if r["claims_ok"] is False:
                bits.append(r["detail"] or "claim check failed")
            out.append(f"- **{r['id']}** ({r['tags']}): {'; '.join(bits)}")

    declined = [r for r in rows if r["action_ok"] and r["plan_ok"] is None
                and r["got_action"] not in SPOKEN and r["detail"]]
    if declined:
        out += ["", "## Routed, then declined by the server", "",
                "The intent was expressible and the application refused it. "
                "That is a correct outcome, and the rule behind each one is "
                "pinned by a test rather than by this suite.", ""]
        for r in declined:
            out.append(f"- **{r['id']}**: `{r['got_action']}` — {r['detail']}")

    routed = Counter(r["got_action"] for r in rows)
    out += ["", "## Actions chosen", "",
            ", ".join(f"`{a}` {n}" for a, n in sorted(routed.items()))]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=settings.eval_provider)
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the first N fixtures")
    ap.add_argument("--out", default="/docs/chat-eval-results.md")
    args = ap.parse_args()

    open_pools()
    was_sharing = settings.chat_sees_data
    try:
        rows = run(args.provider, args.limit)
    finally:
        settings.chat_sees_data = was_sharing
        close_pools()

    model = make_client(args.provider).model
    text = report(args.provider, model, rows)
    Path(args.out).write_text(text)
    print(text)
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
