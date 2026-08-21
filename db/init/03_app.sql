CREATE TABLE app.board (
    id         uuid PRIMARY KEY,
    title      text        NOT NULL,
    -- Tab order. Ties break on created_at, so a board never has to be
    -- given a position just to exist.
    position   integer     NOT NULL DEFAULT 0,
    revision   bigint      NOT NULL DEFAULT 0,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.card (
    id             uuid PRIMARY KEY,
    board_id       uuid NOT NULL REFERENCES app.board(id) ON DELETE CASCADE,
    title          text NOT NULL DEFAULT '',
    -- The semantic query is the source of truth. SQL is compiled at render
    -- time and cached as a by-product; frozen SQL rots.
    semantic_query jsonb,
    chart_hint     text,
    vega_spec      jsonb,
    prompt         text,                    -- provenance only, never re-executed
    state          text NOT NULL DEFAULT 'empty'
                   CHECK (state IN ('empty', 'ready', 'broken')),
    -- No default: app.store.cards.next_slot is the single source of
    -- truth for placement, and a second one here drifts from it.
    layout         jsonb NOT NULL,
    cache          jsonb,
    ttl_seconds    integer NOT NULL DEFAULT 900,
    previous       jsonb,                   -- one-step undo
    -- {question, asked} while a clarifying question is outstanding, so the
    -- answer to it has something to attach to. Cleared once resolved.
    pending_clarification jsonb,
    deleted_at     timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON app.card (board_id);

-- Browser-global conversation metadata. Result rows are deliberately absent
-- from transcript and plan tables; only transient_result may hold an expiring
-- cache envelope for an explicitly executed read-only query.
CREATE TABLE app.chat_thread (
    id         uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.chat_message (
    id                 uuid PRIMARY KEY,
    thread_id          uuid        NOT NULL REFERENCES app.chat_thread(id) ON DELETE CASCADE,
    role               text        NOT NULL
                       CONSTRAINT chat_message_role_check
                       CHECK (role IN ('user', 'assistant')),
    body               jsonb       NOT NULL,
    active_board_id    uuid,
    active_board_title text        NOT NULL,
    data_exposed       boolean     NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.chat_plan (
    id         uuid PRIMARY KEY,
    thread_id  uuid        NOT NULL REFERENCES app.chat_thread(id) ON DELETE CASCADE,
    action     jsonb       NOT NULL,
    resolved   jsonb       NOT NULL,
    basis      jsonb       NOT NULL,
    status     text        NOT NULL DEFAULT 'pending'
               CONSTRAINT chat_plan_status_check
               CHECK (status IN ('pending', 'confirmed', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.chat_action (
    id               uuid PRIMARY KEY,
    thread_id        uuid REFERENCES app.chat_thread(id) ON DELETE SET NULL,
    plan_id          uuid        NOT NULL UNIQUE,
    board_id         uuid,
    provider         text        NOT NULL,
    model            text        NOT NULL,
    effects          jsonb       NOT NULL,
    status           text        NOT NULL
                     CONSTRAINT chat_action_status_check
                     CHECK (status IN (
                         'queued', 'running', 'completed',
                         'completed_with_errors', 'stopped', 'failed',
                         'cancelled', 'undone'
                     )),
    cancel_requested boolean     NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    purge_after      timestamptz,
    -- Emergency ceiling for direct FK detachment. Normal conversation clear
    -- prepopulates the operator-configured retention (1..30 days).
    CONSTRAINT chat_action_retention_check CHECK (
        (thread_id IS NOT NULL AND purge_after IS NULL)
        OR
        (thread_id IS NULL AND purge_after IS NOT NULL
         AND purge_after <= updated_at + interval '30 days')
    )
);

CREATE FUNCTION app.set_chat_action_detached_retention()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.thread_id IS NOT NULL
       AND NEW.thread_id IS NULL
       AND NEW.purge_after IS NULL THEN
        NEW.updated_at := now();
        NEW.purge_after := NEW.updated_at + interval '30 days';
    END IF;
    RETURN NEW;
END;
$$;

ALTER FUNCTION app.set_chat_action_detached_retention() OWNER TO CURRENT_USER;
REVOKE ALL PRIVILEGES ON FUNCTION app.set_chat_action_detached_retention()
    FROM PUBLIC, app_rw;

CREATE TRIGGER chat_action_detached_retention_trigger
    BEFORE UPDATE OF thread_id ON app.chat_action
    FOR EACH ROW
    EXECUTE FUNCTION app.set_chat_action_detached_retention();

CREATE TABLE app.chat_action_item (
    id         uuid PRIMARY KEY,
    action_id  uuid        NOT NULL REFERENCES app.chat_action(id) ON DELETE CASCADE,
    ordinal    integer     NOT NULL CHECK (ordinal >= 0),
    request    jsonb       NOT NULL,
    card_id    uuid,
    status     text        NOT NULL DEFAULT 'queued'
               CONSTRAINT chat_action_item_status_check
               CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    error      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (action_id, ordinal)
);

CREATE TABLE app.chat_event (
    id         bigserial PRIMARY KEY,
    action_id  uuid        NOT NULL REFERENCES app.chat_action(id) ON DELETE CASCADE,
    kind       text        NOT NULL
               CONSTRAINT chat_event_kind_check
               CHECK (kind IN ('plan', 'item_started', 'card', 'item_failed', 'stopped', 'done')),
    payload    jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.chat_transient_result (
    id         uuid PRIMARY KEY,
    thread_id  uuid        NOT NULL REFERENCES app.chat_thread(id) ON DELETE CASCADE,
    query      jsonb       NOT NULL,
    chart_hint text,
    title      text,
    cache      jsonb       NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX chat_thread_updated_at_idx
    ON app.chat_thread (updated_at DESC);
CREATE INDEX chat_message_thread_created_idx
    ON app.chat_message (thread_id, created_at, id);
CREATE INDEX chat_plan_thread_created_idx
    ON app.chat_plan (thread_id, created_at, id);
CREATE UNIQUE INDEX chat_plan_one_pending_per_thread_idx
    ON app.chat_plan (thread_id) WHERE status = 'pending';
CREATE INDEX chat_action_thread_created_idx
    ON app.chat_action (thread_id, created_at, id);
CREATE INDEX chat_action_board_created_idx
    ON app.chat_action (board_id, created_at, id);
CREATE INDEX chat_action_purge_after_idx
    ON app.chat_action (purge_after) WHERE purge_after IS NOT NULL;
CREATE INDEX chat_action_item_card_idx
    ON app.chat_action_item (card_id) WHERE card_id IS NOT NULL;
CREATE INDEX chat_event_action_id_idx
    ON app.chat_event (action_id, id);
CREATE INDEX chat_transient_thread_created_idx
    ON app.chat_transient_result (thread_id, created_at, id);
CREATE INDEX chat_transient_expires_at_idx
    ON app.chat_transient_result (expires_at);
