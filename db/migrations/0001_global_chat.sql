ALTER TABLE app.board
    ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

ALTER TABLE app.card
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

CREATE TABLE IF NOT EXISTS app.chat_thread (
    id         uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.chat_message (
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

CREATE TABLE IF NOT EXISTS app.chat_plan (
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

CREATE TABLE IF NOT EXISTS app.chat_action (
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

DO $migration_security$
DECLARE
    existing_owner text;
BEGIN
    SELECT owner_record.rolname
      INTO existing_owner
      FROM pg_proc function_record
      JOIN pg_namespace namespace_record
        ON namespace_record.oid = function_record.pronamespace
      JOIN pg_roles owner_record ON owner_record.oid = function_record.proowner
     WHERE namespace_record.nspname = 'app'
       AND function_record.proname = 'set_chat_action_detached_retention'
       AND pg_get_function_identity_arguments(function_record.oid) = '';

    IF FOUND AND existing_owner <> current_user THEN
        RAISE EXCEPTION
            'retention function owner is %, expected current user %',
            existing_owner, current_user
            USING ERRCODE = '42501';
    END IF;
END;
$migration_security$;

DROP TRIGGER IF EXISTS chat_action_detached_retention_trigger ON app.chat_action;
DROP FUNCTION IF EXISTS app.set_chat_action_detached_retention() RESTRICT;

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

CREATE TABLE IF NOT EXISTS app.chat_action_item (
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

CREATE TABLE IF NOT EXISTS app.chat_event (
    id         bigserial PRIMARY KEY,
    action_id  uuid        NOT NULL REFERENCES app.chat_action(id) ON DELETE CASCADE,
    kind       text        NOT NULL
               CONSTRAINT chat_event_kind_check
               CHECK (kind IN ('plan', 'item_started', 'card', 'item_failed', 'stopped', 'done')),
    payload    jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.chat_transient_result (
    id         uuid PRIMARY KEY,
    thread_id  uuid        NOT NULL REFERENCES app.chat_thread(id) ON DELETE CASCADE,
    query      jsonb       NOT NULL,
    chart_hint text,
    title      text,
    cache      jsonb       NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_thread_updated_at_idx
    ON app.chat_thread (updated_at DESC);
CREATE INDEX IF NOT EXISTS chat_message_thread_created_idx
    ON app.chat_message (thread_id, created_at, id);
CREATE INDEX IF NOT EXISTS chat_plan_thread_created_idx
    ON app.chat_plan (thread_id, created_at, id);
CREATE UNIQUE INDEX IF NOT EXISTS chat_plan_one_pending_per_thread_idx
    ON app.chat_plan (thread_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS chat_action_thread_created_idx
    ON app.chat_action (thread_id, created_at, id);
CREATE INDEX IF NOT EXISTS chat_action_board_created_idx
    ON app.chat_action (board_id, created_at, id);
CREATE INDEX IF NOT EXISTS chat_action_purge_after_idx
    ON app.chat_action (purge_after) WHERE purge_after IS NOT NULL;
DROP INDEX IF EXISTS app.chat_action_item_action_ordinal_idx;
CREATE INDEX IF NOT EXISTS chat_action_item_card_idx
    ON app.chat_action_item (card_id) WHERE card_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS chat_event_action_id_idx
    ON app.chat_event (action_id, id);
CREATE INDEX IF NOT EXISTS chat_transient_thread_created_idx
    ON app.chat_transient_result (thread_id, created_at, id);
CREATE INDEX IF NOT EXISTS chat_transient_expires_at_idx
    ON app.chat_transient_result (expires_at);

-- Existing volumes ran 04_grants.sql before this sequence existed. Grant it
-- explicitly here; fresh installations also receive the schema-wide grants.
GRANT ALL ON SEQUENCE app.chat_event_id_seq TO app_rw;

-- Match fresh-install defaults so later Task migrations create runtime
-- objects with the same application privileges on upgraded volumes.
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT ALL ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT ALL ON SEQUENCES TO app_rw;
