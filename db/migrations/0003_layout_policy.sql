-- Persist layout policy without changing any existing dashboard geometry.
ALTER TABLE app.board
    ADD COLUMN IF NOT EXISTS layout_mode text NOT NULL DEFAULT 'free';

DO $layout_policy$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint constraint_record
          JOIN pg_class table_record
            ON table_record.oid = constraint_record.conrelid
          JOIN pg_namespace namespace_record
            ON namespace_record.oid = table_record.relnamespace
         WHERE namespace_record.nspname = 'app'
           AND table_record.relname = 'board'
           AND constraint_record.conname = 'board_layout_mode_check'
    ) THEN
        ALTER TABLE app.board
            ADD CONSTRAINT board_layout_mode_check
            CHECK (layout_mode IN ('free', 'auto_pack'));
    END IF;
END;
$layout_policy$;

ALTER TABLE app.card
    ADD COLUMN IF NOT EXISTS auto_size_pending boolean NOT NULL DEFAULT false;
