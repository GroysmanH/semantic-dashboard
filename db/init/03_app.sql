CREATE TABLE app.board (
    id         uuid PRIMARY KEY,
    title      text        NOT NULL,
    -- Tab order. Ties break on created_at, so a board never has to be
    -- given a position just to exist.
    position   integer     NOT NULL DEFAULT 0,
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
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON app.card (board_id);
