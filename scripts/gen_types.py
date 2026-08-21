"""Pydantic -> JSON Schema -> TypeScript.

The semantic query schema is the contract between the model, the compiler
and the frontend. Generating the TS from the Python models is what stops
the two halves drifting apart silently.

Run via `make types`, which then pipes this through json2ts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from pydantic import BaseModel  # noqa: E402

from app.chat.schema import (  # noqa: E402
    ChatAction,
    ChatEventEnvelope,
    ChatThreadView,
    ChatTurnResponse,
    PendingPlanView,
    PlanConfirmedView,
    TransientResultView,
)
from app.render import Render  # noqa: E402
from app.semantic.query import SemanticQuery  # noqa: E402

OUT = Path("/frontend/src/api/schema.json")


class ApiContract(BaseModel):
    """A single root so json2ts emits one file with shared $defs.

    Every chat model the browser touches is named here. A model reachable
    only through a route body would be typed by hand on the other side,
    which is exactly the drift this file exists to prevent.
    """

    semantic_query: SemanticQuery
    render: Render
    chat_action: ChatAction
    chat_turn_response: ChatTurnResponse
    chat_thread_view: ChatThreadView
    chat_pending_plan: PendingPlanView
    chat_plan_confirmed: PlanConfirmedView
    chat_transient_result: TransientResultView
    chat_event: ChatEventEnvelope


def main() -> None:
    schema = ApiContract.model_json_schema(mode="serialization")
    schema["title"] = "ApiContract"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
