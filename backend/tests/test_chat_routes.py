"""Chat routes, both gates, and the transient result cache.

Chat is off by default. When it is off these endpoints are 404 rather than
403: a "forbidden" tells a caller the feature exists and is worth probing.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.store import cards as store


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def chat_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_enabled", True)


@pytest.fixture
def board():
    return store.create_board("Operations")


@pytest.fixture
def thread(client, chat_on):
    return client.post("/chat/threads").json()


# -- the server gate -----------------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("post", "/chat/threads", None),
    ("get", "/chat/threads/{tid}", None),
    ("delete", "/chat/threads/{tid}", None),
    ("post", "/chat/threads/{tid}/turns",
     {"active_board_id": "00000000-0000-0000-0000-000000000000",
      "question": "hi"}),
])
def test_every_route_is_absent_while_chat_is_disabled(client, method, path,
                                                      body, monkeypatch):
    monkeypatch.setattr(settings, "chat_enabled", False)
    call = getattr(client, method)
    kwargs = {"json": body} if body is not None else {}
    assert call(path.format(tid=uuid.uuid4()), **kwargs).status_code == 404


def test_a_malformed_body_does_not_reveal_that_the_route_exists(client,
                                                                monkeypatch):
    """Answering 422 would confirm both that the endpoint is there and what
    shape it wants, which is what answering 404 was meant to avoid. The
    guard is a router dependency so it resolves before body validation."""
    monkeypatch.setattr(settings, "chat_enabled", False)
    response = client.post(f"/chat/threads/{uuid.uuid4()}/turns", json={})
    assert response.status_code == 404


def test_a_thread_can_be_created_and_reloaded(client, chat_on):
    created = client.post("/chat/threads").json()
    loaded = client.get(f"/chat/threads/{created['id']}")

    assert loaded.status_code == 200
    assert loaded.json()["id"] == created["id"]
    assert loaded.json()["messages"] == []


def test_an_unknown_thread_is_404(client, chat_on):
    assert client.get(f"/chat/threads/{uuid.uuid4()}").status_code == 404


def test_a_turn_against_an_unknown_dashboard_is_404(client, thread, chat_on):
    response = client.post(
        f"/chat/threads/{thread['id']}/turns",
        json={"active_board_id": str(uuid.uuid4()), "question": "hi"})
    assert response.status_code == 404


def test_an_unexpected_thread_preflight_failure_is_safe_and_request_scoped(
    board, chat_on, monkeypatch,
):
    from app.routes import chat as chat_routes

    monkeypatch.setattr(
        chat_routes, "_thread_or_404",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("raw thread store connection text")),
    )
    safe_client = TestClient(app, raise_server_exceptions=False)
    response = safe_client.post(
        f"/chat/threads/{uuid.uuid4()}/turns",
        headers={"x-request-id": "req-thread-read"},
        json={"active_board_id": str(board["id"]), "question": "hello"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "code": "chat_unexpected_error",
        "message": "I couldn’t finish that request safely. Nothing changed.",
        "request_id": "req-thread-read",
        "retryable": True,
    }
    assert "thread store" not in response.text
    assert "connection" not in response.text


def test_an_unexpected_dashboard_preflight_failure_is_safe_and_request_scoped(
    thread, chat_on, monkeypatch,
):
    from app.routes import chat as chat_routes

    monkeypatch.setattr(
        chat_routes.store, "get_board",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("raw board store credentials")),
    )
    safe_client = TestClient(app, raise_server_exceptions=False)
    response = safe_client.post(
        f"/chat/threads/{thread['id']}/turns",
        headers={"x-request-id": "req-board-read"},
        json={"active_board_id": str(uuid.uuid4()), "question": "hello"},
    )

    assert response.status_code == 500
    assert response.json()["request_id"] == "req-board-read"
    assert response.json()["code"] == "chat_unexpected_error"
    assert "credentials" not in response.text


def test_an_unexpected_turn_failure_returns_only_a_safe_envelope(
    thread, board, chat_on, monkeypatch, caplog,
):
    from app.routes import chat as chat_routes

    class Client:
        provider, model = "openai", "gpt-test"

    monkeypatch.setattr(chat_routes, "make_client", lambda *a, **k: Client())
    monkeypatch.setattr(
        chat_routes, "run_turn",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("raw Pydantic provider internals")),
    )
    safe_client = TestClient(app, raise_server_exceptions=False)
    response = safe_client.post(
        f"/chat/threads/{thread['id']}/turns",
        headers={"x-request-id": "req-route"},
        json={"active_board_id": str(board["id"]), "question": "change it"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "code": "chat_unexpected_error",
        "message": "I couldn’t finish that request safely. Nothing changed.",
        "request_id": "req-route",
        "retryable": True,
    }
    assert "Pydantic" not in response.text
    failure = next(r for r in caplog.records
                   if r.getMessage() == "unexpected chat turn failure")
    assert (failure.request_id, failure.provider, failure.model) == (
        "req-route", "openai", "gpt-test")
    assert failure.stage == "router"
    assert failure.schema == "ChatRouterResponse"
    assert failure.retry_count == 0


def test_an_unexpected_client_setup_failure_uses_the_same_safe_envelope(
    thread, board, chat_on, monkeypatch,
):
    from app.routes import chat as chat_routes

    monkeypatch.setattr(
        chat_routes, "make_client",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("raw client setup internals")),
    )
    safe_client = TestClient(app, raise_server_exceptions=False)
    response = safe_client.post(
        f"/chat/threads/{thread['id']}/turns",
        headers={"x-request-id": "req-setup"},
        json={"active_board_id": str(board["id"]), "question": "change it",
              "provider": "openai"},
    )

    assert response.status_code == 500
    assert response.json()["request_id"] == "req-setup"
    assert response.json()["code"] == "chat_unexpected_error"
    assert "raw client" not in response.text
    assert "internals" not in response.text


def test_an_unexpected_detail_failure_is_logged_at_the_detail_stage(
    client, thread, board, chat_on, monkeypatch, caplog,
):
    from app.chat import plan as planner
    from app.routes import chat as chat_routes

    class Client:
        provider, model = "gemini", "gemini-selected"

        def ask(self, system, user, schema):
            if "turn" in schema.model_fields:
                return schema.model_validate({"turn": {
                    "action": "task", "say": "I will rename it.",
                    "kind": "rename_dashboard",
                }})
            return schema.model_validate({
                "action": "rename_dashboard", "say": "",
                "board_id": str(board["id"]), "title": "Wells",
            })

    monkeypatch.setattr(chat_routes, "make_client", lambda *a, **k: Client())
    monkeypatch.setattr(
        planner, "resolve",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("raw detail resolver internals")),
    )
    response = client.post(
        f"/chat/threads/{thread['id']}/turns",
        headers={"x-request-id": "req-detail"},
        json={"active_board_id": str(board["id"]), "question": "rename it",
              "provider": "gemini"},
    )

    assert response.status_code == 500
    assert "resolver" not in response.text
    failure = next(r for r in caplog.records
                   if r.getMessage() == "unexpected chat turn failure")
    assert failure.request_id == "req-detail"
    assert (failure.provider, failure.model) == ("gemini", "gemini-selected")
    assert failure.stage == "detail"
    assert failure.schema == "RenameDashboardAction"
    assert failure.retry_count == 0


def test_clearing_returns_a_new_thread_id(client, thread, chat_on):
    replacement = client.delete(f"/chat/threads/{thread['id']}")

    assert replacement.status_code == 200
    assert replacement.json()["id"] != thread["id"]


def test_a_cleared_thread_keeps_no_transcript(client, thread, board, chat_on):
    from app.store import chat as chat_store
    chat_store.append_message(
        uuid.UUID(thread["id"]), role="user", body={"say": "secret question"},
        active_board_id=board["id"], active_board_title="Operations",
        data_exposed=False)

    client.delete(f"/chat/threads/{thread['id']}")

    assert chat_store.list_messages(uuid.UUID(thread["id"])) == []


# -- the consent gate ----------------------------------------------------

def test_the_browser_cannot_grant_consent_the_server_withheld(monkeypatch):
    """share_rows is an AND of two gates. A client flag alone must never
    open the data path."""
    from app.chat import turn as turn_module

    monkeypatch.setattr(settings, "chat_sees_data", False)
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        from app.chat.context import BuiltContext
        return BuiltContext(text="", data_exposed=False, exact_card_ids=(),
                            notices=())

    monkeypatch.setattr(turn_module, "build_context", spy)

    board = store.create_board("Operations")
    from app.store import chat as chat_store
    thread = chat_store.create_thread()

    class Client:
        provider, model = "gemini", "fake"

        def ask(self, system, user, schema):
            from app.chat.schema import ChatModelResponse
            return ChatModelResponse(turn={"action": "answer", "say": "ok"})

    turn_module.run_turn(
        turn_module.TurnRequest(
            thread_id=thread["id"], active_board_id=board["id"],
            question="q", provider="gemini", hard=False,
            share_visible_data=True),
        client=Client())

    assert seen["share_rows"] is False


def test_the_layer_route_states_both_gates(client):
    gates = client.get("/layer").json()["chat"]
    assert set(gates) == {"enabled", "data_sharing_permitted"}


# -- transient results ---------------------------------------------------

def test_rerunning_an_unknown_result_is_404(client, chat_on):
    response = client.post(f"/chat/transient/{uuid.uuid4()}/rerun")
    assert response.status_code == 404


def test_an_expired_result_is_gone_rather_than_silently_rerun(client, thread,
                                                              chat_on):
    """Reloading a conversation must not touch the warehouse. An expired
    result waits to be asked for again."""
    from app.store import chat as chat_store
    saved = chat_store.save_transient(
        uuid.UUID(thread["id"]),
        query={"entity": "production", "measures": ["oil"]},
        chart_hint=None, title="", cache={}, ttl_seconds=-1)

    assert chat_store.get_transient(saved["id"]) is None


def test_reloading_a_transcript_runs_no_query(client, thread, board, chat_on,
                                              monkeypatch):
    from app.chat import turn as turn_module
    from app.store import chat as chat_store

    chat_store.append_message(
        uuid.UUID(thread["id"]), role="assistant",
        body={"action": "run_query", "say": "here",
              "transient_result_id": str(uuid.uuid4())},
        active_board_id=board["id"], active_board_title="Operations",
        data_exposed=False)

    def explode(*a, **k):
        raise AssertionError("a transcript reload ran a query")

    monkeypatch.setattr(turn_module, "render", explode)

    assert client.get(f"/chat/threads/{thread['id']}").status_code == 200


# -- keeping a result ----------------------------------------------------

def test_a_chat_result_can_be_kept_as_a_card(client, thread, board,
                                             monkeypatch):
    """Ask a question, get a chart, keep it. Until this existed the answer
    lived in a transcript and expired there."""
    from app.deps import LAYER
    from app.render import render
    from app.semantic.query import SemanticQuery
    from app.store import chat as chat_store

    query = {"entity": "production", "measures": ["oil"],
             "dimensions": [{"field": "region"}]}
    r = render(SemanticQuery.model_validate(query), LAYER)
    stored = chat_store.save_transient(
        thread["id"], query=query, chart_hint=None, title="Oil by region",
        cache=r.cache or {}, ttl_seconds=900)

    out = client.post(f"/chat/transient/{stored['id']}/keep",
                      json={"board_id": str(board["id"])})

    assert out.status_code == 200
    cards = store.list_cards(board["id"])
    assert [c["title"] for c in cards] == ["Oil by region"]
    assert cards[0]["state"] == "ready"
    assert cards[0]["semantic_query"]["dimensions"][0]["field"] == "region"


def test_keeping_re_runs_rather_than_copying_a_stale_cache(client, thread,
                                                           board):
    """A result can be minutes old. A card that arrives already stale is
    the failure the freshness line exists to catch."""
    from app.store import chat as chat_store

    query = {"entity": "production", "measures": ["oil"],
             "dimensions": [{"field": "region"}]}
    stored = chat_store.save_transient(
        thread["id"], query=query, chart_hint=None, title="Oil",
        cache={"result": [{"region": "Nowhere", "oil": 1}], "row_count": 1},
        ttl_seconds=900)

    client.post(f"/chat/transient/{stored['id']}/keep",
                json={"board_id": str(board["id"])})

    kept = store.list_cards(board["id"])[0]
    regions = {row["region"] for row in kept["cache"]["result"]}
    assert "Nowhere" not in regions, "the stale cache was copied across"


def test_keeping_a_result_that_has_gone_is_not_found(client, thread, board):
    assert client.post(f"/chat/transient/{uuid.uuid4()}/keep",
                       json={"board_id": str(board["id"])}).status_code == 404


def test_keeping_onto_a_dashboard_that_has_gone_is_not_found(client, thread):
    from app.store import chat as chat_store

    stored = chat_store.save_transient(
        thread["id"], query={"entity": "production", "measures": ["oil"]},
        chart_hint=None, title="Oil", cache={}, ttl_seconds=900)

    assert client.post(f"/chat/transient/{stored['id']}/keep",
                       json={"board_id": str(uuid.uuid4())}
                       ).status_code == 404
