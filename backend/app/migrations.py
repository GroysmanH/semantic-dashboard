"""Small, ordered, transactional raw-SQL migration runner."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

import psycopg


_MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_[^.]+\.sql$")
_MIGRATION_LOCK_ID = 0x4E4C3256495A

_CHAT_COLUMNS = {
    "chat_thread": [
        ("id", "uuid", True, None),
        ("created_at", "timestamp with time zone", True, "now()"),
        ("updated_at", "timestamp with time zone", True, "now()"),
    ],
    "chat_message": [
        ("id", "uuid", True, None),
        ("thread_id", "uuid", True, None),
        ("role", "text", True, None),
        ("body", "jsonb", True, None),
        ("active_board_id", "uuid", False, None),
        ("active_board_title", "text", True, None),
        ("data_exposed", "boolean", True, "false"),
        ("created_at", "timestamp with time zone", True, "now()"),
    ],
    "chat_plan": [
        ("id", "uuid", True, None),
        ("thread_id", "uuid", True, None),
        ("action", "jsonb", True, None),
        ("resolved", "jsonb", True, None),
        ("basis", "jsonb", True, None),
        ("status", "text", True, "'pending'::text"),
        ("created_at", "timestamp with time zone", True, "now()"),
        ("updated_at", "timestamp with time zone", True, "now()"),
    ],
    "chat_action": [
        ("id", "uuid", True, None),
        ("thread_id", "uuid", False, None),
        ("plan_id", "uuid", True, None),
        ("board_id", "uuid", False, None),
        ("provider", "text", True, None),
        ("model", "text", True, None),
        ("effects", "jsonb", True, None),
        ("status", "text", True, None),
        ("cancel_requested", "boolean", True, "false"),
        ("created_at", "timestamp with time zone", True, "now()"),
        ("updated_at", "timestamp with time zone", True, "now()"),
        ("purge_after", "timestamp with time zone", False, None),
    ],
    "chat_action_item": [
        ("id", "uuid", True, None),
        ("action_id", "uuid", True, None),
        ("ordinal", "integer", True, None),
        ("request", "jsonb", True, None),
        ("card_id", "uuid", False, None),
        ("status", "text", True, "'queued'::text"),
        ("error", "text", False, None),
        ("created_at", "timestamp with time zone", True, "now()"),
        ("updated_at", "timestamp with time zone", True, "now()"),
    ],
    "chat_event": [
        ("id", "bigint", True, "nextval('app.chat_event_id_seq'::regclass)"),
        ("action_id", "uuid", True, None),
        ("kind", "text", True, None),
        ("payload", "jsonb", True, None),
        ("created_at", "timestamp with time zone", True, "now()"),
    ],
    "chat_transient_result": [
        ("id", "uuid", True, None),
        ("thread_id", "uuid", True, None),
        ("query", "jsonb", True, None),
        ("chart_hint", "text", False, None),
        ("title", "text", False, None),
        ("cache", "jsonb", True, None),
        ("expires_at", "timestamp with time zone", True, None),
        ("created_at", "timestamp with time zone", True, "now()"),
    ],
}

_CHAT_CONSTRAINTS = {
    ("chat_thread", "chat_thread_pkey"): "PRIMARY KEY (id)",
    ("chat_message", "chat_message_pkey"): "PRIMARY KEY (id)",
    ("chat_message", "chat_message_role_check"): (
        "CHECK (role = ANY (ARRAY['user'::text, 'assistant'::text]))"
    ),
    ("chat_message", "chat_message_thread_id_fkey"): (
        "FOREIGN KEY (thread_id) REFERENCES app.chat_thread(id) ON DELETE CASCADE"
    ),
    ("chat_plan", "chat_plan_pkey"): "PRIMARY KEY (id)",
    ("chat_plan", "chat_plan_status_check"): (
        "CHECK (status = ANY (ARRAY['pending'::text, 'confirmed'::text, "
        "'cancelled'::text]))"
    ),
    ("chat_plan", "chat_plan_thread_id_fkey"): (
        "FOREIGN KEY (thread_id) REFERENCES app.chat_thread(id) ON DELETE CASCADE"
    ),
    ("chat_action", "chat_action_pkey"): "PRIMARY KEY (id)",
    ("chat_action", "chat_action_plan_id_key"): "UNIQUE (plan_id)",
    ("chat_action", "chat_action_status_check"): (
        "CHECK (status = ANY (ARRAY['queued'::text, 'running'::text, "
        "'completed'::text, 'completed_with_errors'::text, 'stopped'::text, "
        "'failed'::text, 'cancelled'::text, 'undone'::text]))"
    ),
    ("chat_action", "chat_action_retention_check"): (
        "CHECK (thread_id IS NOT NULL AND purge_after IS NULL OR thread_id IS NULL "
        "AND purge_after IS NOT NULL AND purge_after <= (updated_at + "
        "'30 days'::interval))"
    ),
    ("chat_action", "chat_action_thread_id_fkey"): (
        "FOREIGN KEY (thread_id) REFERENCES app.chat_thread(id) ON DELETE SET NULL"
    ),
    ("chat_action_item", "chat_action_item_pkey"): "PRIMARY KEY (id)",
    ("chat_action_item", "chat_action_item_action_id_ordinal_key"): (
        "UNIQUE (action_id, ordinal)"
    ),
    ("chat_action_item", "chat_action_item_ordinal_check"): "CHECK (ordinal >= 0)",
    ("chat_action_item", "chat_action_item_status_check"): (
        "CHECK (status = ANY (ARRAY['queued'::text, 'running'::text, "
        "'succeeded'::text, 'failed'::text, 'cancelled'::text]))"
    ),
    ("chat_action_item", "chat_action_item_action_id_fkey"): (
        "FOREIGN KEY (action_id) REFERENCES app.chat_action(id) ON DELETE CASCADE"
    ),
    ("chat_event", "chat_event_pkey"): "PRIMARY KEY (id)",
    ("chat_event", "chat_event_kind_check"): (
        "CHECK (kind = ANY (ARRAY['plan'::text, 'item_started'::text, "
        "'card'::text, 'item_failed'::text, 'stopped'::text, 'done'::text]))"
    ),
    ("chat_event", "chat_event_action_id_fkey"): (
        "FOREIGN KEY (action_id) REFERENCES app.chat_action(id) ON DELETE CASCADE"
    ),
    ("chat_transient_result", "chat_transient_result_pkey"): "PRIMARY KEY (id)",
    ("chat_transient_result", "chat_transient_result_thread_id_fkey"): (
        "FOREIGN KEY (thread_id) REFERENCES app.chat_thread(id) ON DELETE CASCADE"
    ),
}

_CHAT_INDEXES = {
    "chat_thread_pkey": "CREATE UNIQUE INDEX chat_thread_pkey ON app.chat_thread USING btree (id)",
    "chat_thread_updated_at_idx": "CREATE INDEX chat_thread_updated_at_idx ON app.chat_thread USING btree (updated_at DESC)",
    "chat_message_pkey": "CREATE UNIQUE INDEX chat_message_pkey ON app.chat_message USING btree (id)",
    "chat_message_thread_created_idx": "CREATE INDEX chat_message_thread_created_idx ON app.chat_message USING btree (thread_id, created_at, id)",
    "chat_plan_pkey": "CREATE UNIQUE INDEX chat_plan_pkey ON app.chat_plan USING btree (id)",
    "chat_plan_thread_created_idx": "CREATE INDEX chat_plan_thread_created_idx ON app.chat_plan USING btree (thread_id, created_at, id)",
    "chat_plan_one_pending_per_thread_idx": "CREATE UNIQUE INDEX chat_plan_one_pending_per_thread_idx ON app.chat_plan USING btree (thread_id) WHERE (status = 'pending'::text)",
    "chat_action_pkey": "CREATE UNIQUE INDEX chat_action_pkey ON app.chat_action USING btree (id)",
    "chat_action_plan_id_key": "CREATE UNIQUE INDEX chat_action_plan_id_key ON app.chat_action USING btree (plan_id)",
    "chat_action_thread_created_idx": "CREATE INDEX chat_action_thread_created_idx ON app.chat_action USING btree (thread_id, created_at, id)",
    "chat_action_board_created_idx": "CREATE INDEX chat_action_board_created_idx ON app.chat_action USING btree (board_id, created_at, id)",
    "chat_action_purge_after_idx": "CREATE INDEX chat_action_purge_after_idx ON app.chat_action USING btree (purge_after) WHERE (purge_after IS NOT NULL)",
    "chat_action_item_pkey": "CREATE UNIQUE INDEX chat_action_item_pkey ON app.chat_action_item USING btree (id)",
    "chat_action_item_action_id_ordinal_key": "CREATE UNIQUE INDEX chat_action_item_action_id_ordinal_key ON app.chat_action_item USING btree (action_id, ordinal)",
    "chat_action_item_card_idx": "CREATE INDEX chat_action_item_card_idx ON app.chat_action_item USING btree (card_id) WHERE (card_id IS NOT NULL)",
    "chat_event_pkey": "CREATE UNIQUE INDEX chat_event_pkey ON app.chat_event USING btree (id)",
    "chat_event_action_id_idx": "CREATE INDEX chat_event_action_id_idx ON app.chat_event USING btree (action_id, id)",
    "chat_transient_result_pkey": "CREATE UNIQUE INDEX chat_transient_result_pkey ON app.chat_transient_result USING btree (id)",
    "chat_transient_thread_created_idx": "CREATE INDEX chat_transient_thread_created_idx ON app.chat_transient_result USING btree (thread_id, created_at, id)",
    "chat_transient_expires_at_idx": "CREATE INDEX chat_transient_expires_at_idx ON app.chat_transient_result USING btree (expires_at)",
}


class MigrationError(RuntimeError):
    """Base class for deterministic migration failures."""


class MigrationDiscoveryError(MigrationError):
    """A migration directory cannot be interpreted safely."""


class MigrationChecksumError(MigrationError):
    """An already-applied migration no longer matches its source file."""


class MigrationLedgerError(MigrationError):
    """The migration ledger catalog shape is not exactly trusted."""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str


def _read_utf8(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    try:
        sql = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationDiscoveryError(
            f"migration {path.name} must contain valid UTF-8"
        ) from exc
    return raw, sql


def discover_migrations(directory: Path) -> list[Migration]:
    """Validate and return numbered SQL files in ascending version order."""
    if not directory.is_dir():
        raise MigrationDiscoveryError(f"migration directory does not exist: {directory}")

    migrations: list[Migration] = []
    versions: set[str] = set()
    for path in directory.glob("*.sql"):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationDiscoveryError(
                f"migration filename must match NNNN_name.sql: {path.name}"
            )
        version = match.group("version")
        if version in versions:
            raise MigrationDiscoveryError(f"duplicate migration version {version}")
        versions.add(version)
        raw, _ = _read_utf8(path)
        migrations.append(
            Migration(version=version, path=path, checksum=sha256(raw).hexdigest())
        )

    return sorted(migrations, key=lambda migration: int(migration.version))


def _validate_app_schema(cur: psycopg.Cursor) -> None:
    cur.execute(
        """
        SELECT owner_record.rolname, current_user
          FROM pg_namespace namespace_record
          JOIN pg_roles owner_record ON owner_record.oid = namespace_record.nspowner
         WHERE namespace_record.nspname = 'app'
        """
    )
    row = cur.fetchone()
    if row is None:
        raise MigrationLedgerError("required app schema does not exist")
    owner, current_user = row
    if owner != current_user:
        raise MigrationLedgerError(
            f"app schema owner is {owner!r}, expected current user {current_user!r}"
        )


def _ledger_oid(cur: psycopg.Cursor) -> int | None:
    cur.execute(
        """
        SELECT table_record.oid
          FROM pg_class table_record
          JOIN pg_namespace namespace_record ON namespace_record.oid = table_record.relnamespace
         WHERE namespace_record.nspname = 'app'
           AND table_record.relname = 'schema_migration'
        """
    )
    row = cur.fetchone()
    return None if row is None else row[0]


def _validate_ledger(cur: psycopg.Cursor, ledger_oid: int) -> None:
    cur.execute(
        """
        SELECT table_record.relkind, owner_record.rolname, current_user,
               table_record.relrowsecurity, table_record.relforcerowsecurity
          FROM pg_class table_record
          JOIN pg_roles owner_record ON owner_record.oid = table_record.relowner
         WHERE table_record.oid = %s
        """,
        (ledger_oid,),
    )
    relkind, owner, current_user, row_security, force_row_security = cur.fetchone()
    if relkind != "r":
        raise MigrationLedgerError("app.schema_migration is not an ordinary table")
    if owner != current_user:
        raise MigrationLedgerError(
            "app.schema_migration owner is "
            f"{owner!r}, expected current user {current_user!r}"
        )
    if row_security or force_row_security:
        raise MigrationLedgerError("app.schema_migration must not use row security")

    cur.execute(
        """
        SELECT attribute_record.attname,
               format_type(attribute_record.atttypid, attribute_record.atttypmod),
               attribute_record.attnotnull,
               pg_get_expr(default_record.adbin, default_record.adrelid),
               attribute_record.attidentity,
               attribute_record.attgenerated,
               attribute_record.attacl
          FROM pg_attribute attribute_record
          LEFT JOIN pg_attrdef default_record
            ON default_record.adrelid = attribute_record.attrelid
           AND default_record.adnum = attribute_record.attnum
         WHERE attribute_record.attrelid = %s
           AND attribute_record.attnum > 0
           AND NOT attribute_record.attisdropped
         ORDER BY attribute_record.attnum
        """,
        (ledger_oid,),
    )
    columns = cur.fetchall()
    expected_columns = [
        ("version", "text", True, None, "", "", None),
        ("checksum", "text", True, None, "", "", None),
        (
            "applied_at",
            "timestamp with time zone",
            True,
            "now()",
            "",
            "",
            None,
        ),
    ]
    if columns != expected_columns:
        raise MigrationLedgerError(
            "app.schema_migration has unexpected columns, defaults, or column ACLs"
        )

    cur.execute(
        """
        SELECT constraint_record.contype, constraint_record.conkey
          FROM pg_constraint constraint_record
         WHERE constraint_record.conrelid = %s
         ORDER BY constraint_record.oid
        """,
        (ledger_oid,),
    )
    if cur.fetchall() != [("p", [1])]:
        raise MigrationLedgerError(
            "app.schema_migration must have only a primary key on version"
        )

    cur.execute(
        "SELECT count(*) FROM pg_trigger WHERE tgrelid = %s AND NOT tgisinternal",
        (ledger_oid,),
    )
    if cur.fetchone()[0] != 0:
        raise MigrationLedgerError("app.schema_migration has an unexpected trigger")

    cur.execute("SELECT count(*) FROM pg_rewrite WHERE ev_class = %s", (ledger_oid,))
    if cur.fetchone()[0] != 0:
        raise MigrationLedgerError("app.schema_migration has an unexpected rewrite rule")

    cur.execute("SELECT count(*) FROM pg_policy WHERE polrelid = %s", (ledger_oid,))
    if cur.fetchone()[0] != 0:
        raise MigrationLedgerError("app.schema_migration has an unexpected policy")


def _revoke_ledger_access(cur: psycopg.Cursor) -> None:
    cur.execute(
        "REVOKE ALL PRIVILEGES ON TABLE app.schema_migration FROM app_rw, PUBLIC"
    )
    cur.execute(
        """
        REVOKE SELECT (version, checksum, applied_at),
               INSERT (version, checksum, applied_at),
               UPDATE (version, checksum, applied_at),
               REFERENCES (version, checksum, applied_at)
          ON TABLE app.schema_migration
        FROM app_rw, PUBLIC
        """
    )


def _validate_global_chat_sequence(cur: psycopg.Cursor) -> None:
    cur.execute(
        """
        SELECT sequence_record.oid, sequence_record.relkind,
               owner_record.rolname, current_user,
               format_type(shape_record.seqtypid, NULL),
               shape_record.seqstart, shape_record.seqincrement,
               shape_record.seqmax, shape_record.seqmin,
               shape_record.seqcache, shape_record.seqcycle
          FROM pg_class sequence_record
          JOIN pg_namespace namespace_record ON namespace_record.oid = sequence_record.relnamespace
          JOIN pg_roles owner_record ON owner_record.oid = sequence_record.relowner
          JOIN pg_sequence shape_record ON shape_record.seqrelid = sequence_record.oid
         WHERE namespace_record.nspname = 'app'
           AND sequence_record.relname = 'chat_event_id_seq'
        """
    )
    row = cur.fetchone()
    if row is None:
        raise MigrationLedgerError("missing app.chat_event_id_seq")
    (
        sequence_oid,
        relkind,
        owner,
        current_user,
        sequence_type,
        start,
        increment,
        maximum,
        minimum,
        cache,
        cycles,
    ) = row
    if relkind != "S":
        raise MigrationLedgerError("chat_event_id_seq has unexpected relation kind")
    if owner != current_user:
        raise MigrationLedgerError(
            f"chat_event_id_seq owner is {owner!r}, expected {current_user!r}"
        )
    if (
        sequence_type,
        start,
        increment,
        maximum,
        minimum,
        cache,
        cycles,
    ) != ("bigint", 1, 1, 9223372036854775807, 1, 1, False):
        raise MigrationLedgerError("chat_event_id_seq has an unexpected definition")

    cur.execute(
        """
        SELECT referenced_record.relname, dependency_record.refobjsubid,
               dependency_record.deptype
          FROM pg_depend dependency_record
          JOIN pg_class referenced_record ON referenced_record.oid = dependency_record.refobjid
         WHERE dependency_record.classid = 'pg_class'::regclass
           AND dependency_record.objid = %s
           AND dependency_record.deptype IN ('a', 'i')
        """,
        (sequence_oid,),
    )
    if cur.fetchall() != [("chat_event", 1, "a")]:
        raise MigrationLedgerError(
            "chat_event_id_seq must be owned by app.chat_event.id"
        )
    cur.execute(
        """
        SELECT has_sequence_privilege('app_rw', %s, 'SELECT'),
               has_sequence_privilege('app_rw', %s, 'UPDATE'),
               has_sequence_privilege('app_rw', %s, 'USAGE')
        """,
        (sequence_oid, sequence_oid, sequence_oid),
    )
    if cur.fetchone() != (True, True, True):
        raise MigrationLedgerError(
            "chat_event_id_seq lacks the required app_rw runtime privileges"
        )


def _validate_global_chat_tables(cur: psycopg.Cursor) -> None:
    table_names = list(_CHAT_COLUMNS)
    cur.execute(
        """
        SELECT table_record.relname, table_record.relkind,
               owner_record.rolname, current_user,
               table_record.relrowsecurity, table_record.relforcerowsecurity,
               table_record.oid
          FROM pg_class table_record
          JOIN pg_namespace namespace_record ON namespace_record.oid = table_record.relnamespace
          JOIN pg_roles owner_record ON owner_record.oid = table_record.relowner
         WHERE namespace_record.nspname = 'app'
           AND table_record.relname = ANY(%s)
        """,
        (table_names,),
    )
    relations = {row[0]: row[1:] for row in cur.fetchall()}
    if set(relations) != set(table_names):
        raise MigrationLedgerError("global chat tables are missing or unexpected")

    for table_name in table_names:
        relkind, owner, current_user, rls, force_rls, table_oid = relations[table_name]
        if relkind != "r":
            raise MigrationLedgerError(f"{table_name} is not an ordinary table")
        if owner != current_user:
            raise MigrationLedgerError(
                f"{table_name} owner is {owner!r}, expected {current_user!r}"
            )
        if rls or force_rls:
            raise MigrationLedgerError(f"{table_name} must not use row security")

        cur.execute(
            """
            SELECT attribute_record.attname,
                   format_type(attribute_record.atttypid, attribute_record.atttypmod),
                   attribute_record.attnotnull,
                   pg_get_expr(default_record.adbin, default_record.adrelid),
                   attribute_record.attidentity,
                   attribute_record.attgenerated,
                   attribute_record.attacl
              FROM pg_attribute attribute_record
              LEFT JOIN pg_attrdef default_record
                ON default_record.adrelid = attribute_record.attrelid
               AND default_record.adnum = attribute_record.attnum
             WHERE attribute_record.attrelid = %s
               AND attribute_record.attnum > 0
               AND NOT attribute_record.attisdropped
             ORDER BY attribute_record.attnum
            """,
            (table_oid,),
        )
        columns = cur.fetchall()
        shape = [row[:4] for row in columns]
        if shape != _CHAT_COLUMNS[table_name]:
            raise MigrationLedgerError(f"{table_name} has unexpected columns")
        if any(row[4] or row[5] or row[6] is not None for row in columns):
            raise MigrationLedgerError(
                f"{table_name} has unexpected identity, generated, or column ACL state"
            )
        cur.execute("SELECT count(*) FROM pg_rewrite WHERE ev_class = %s", (table_oid,))
        if cur.fetchone()[0] != 0:
            raise MigrationLedgerError(f"{table_name} has an unexpected rewrite rule")
        cur.execute("SELECT count(*) FROM pg_policy WHERE polrelid = %s", (table_oid,))
        if cur.fetchone()[0] != 0:
            raise MigrationLedgerError(f"{table_name} has an unexpected policy")

    cur.execute(
        """
        SELECT table_record.relname, constraint_record.conname,
               pg_get_constraintdef(constraint_record.oid, true)
          FROM pg_constraint constraint_record
          JOIN pg_class table_record ON table_record.oid = constraint_record.conrelid
          JOIN pg_namespace namespace_record ON namespace_record.oid = table_record.relnamespace
         WHERE namespace_record.nspname = 'app'
           AND table_record.relname = ANY(%s)
        """,
        (table_names,),
    )
    constraints = {(row[0], row[1]): row[2] for row in cur.fetchall()}
    if constraints != _CHAT_CONSTRAINTS:
        raise MigrationLedgerError("global chat constraints have unexpected definitions")


def _validate_global_chat_indexes(cur: psycopg.Cursor) -> None:
    cur.execute(
        """
        SELECT index_record.relname, pg_get_indexdef(index_record.oid),
               index_record.relkind, owner_record.rolname, current_user,
               shape_record.indisvalid, shape_record.indisready,
               shape_record.indislive
          FROM pg_index shape_record
          JOIN pg_class index_record ON index_record.oid = shape_record.indexrelid
          JOIN pg_class table_record ON table_record.oid = shape_record.indrelid
          JOIN pg_namespace namespace_record ON namespace_record.oid = table_record.relnamespace
          JOIN pg_roles owner_record ON owner_record.oid = index_record.relowner
         WHERE namespace_record.nspname = 'app'
           AND table_record.relname = ANY(%s)
        """,
        (list(_CHAT_COLUMNS),),
    )
    indexes = {row[0]: row[1:] for row in cur.fetchall()}
    missing = set(_CHAT_INDEXES) - set(indexes)
    unexpected = set(indexes) - set(_CHAT_INDEXES)
    if missing or unexpected:
        raise MigrationLedgerError(
            "global chat indexes differ: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )
    for index_name, expected_definition in _CHAT_INDEXES.items():
        definition, relkind, owner, current_user, valid, ready, live = indexes[index_name]
        if definition != expected_definition:
            raise MigrationLedgerError(f"{index_name} has an unexpected definition")
        if relkind != "i" or owner != current_user or not (valid and ready and live):
            raise MigrationLedgerError(f"{index_name} has untrusted provenance or state")


def _validate_global_chat_function_and_trigger(cur: psycopg.Cursor) -> None:
    cur.execute(
        """
        SELECT function_record.oid, owner_record.rolname, current_user,
               language_record.lanname, function_record.prorettype = 'trigger'::regtype,
               function_record.prosecdef, function_record.provolatile,
               function_record.prokind, function_record.proconfig,
               function_record.prosrc,
               has_function_privilege('app_rw', function_record.oid, 'EXECUTE'),
               EXISTS (
                   SELECT 1
                     FROM aclexplode(
                         COALESCE(
                             function_record.proacl,
                             acldefault('f', function_record.proowner)
                         )
                     ) acl_record
                    WHERE acl_record.grantee = 0
                      AND acl_record.privilege_type = 'EXECUTE'
               )
          FROM pg_proc function_record
          JOIN pg_namespace namespace_record ON namespace_record.oid = function_record.pronamespace
          JOIN pg_roles owner_record ON owner_record.oid = function_record.proowner
          JOIN pg_language language_record ON language_record.oid = function_record.prolang
         WHERE namespace_record.nspname = 'app'
           AND function_record.proname = 'set_chat_action_detached_retention'
           AND pg_get_function_identity_arguments(function_record.oid) = ''
        """
    )
    row = cur.fetchone()
    if row is None:
        raise MigrationLedgerError("missing retention function")
    (
        function_oid,
        owner,
        current_user,
        language,
        returns_trigger,
        security_definer,
        volatility,
        function_kind,
        config,
        source,
        app_can_execute,
        public_can_execute,
    ) = row
    if owner != current_user:
        raise MigrationLedgerError("retention function has an unexpected owner")
    if (
        language,
        returns_trigger,
        security_definer,
        volatility,
        function_kind,
        config,
        app_can_execute,
        public_can_execute,
    ) != ("plpgsql", True, False, "v", "f", ["search_path=pg_catalog"], False, False):
        raise MigrationLedgerError("retention function has an unexpected definition or ACL")
    normalized_source = " ".join(source.split())
    expected_source = (
        "BEGIN IF OLD.thread_id IS NOT NULL AND NEW.thread_id IS NULL AND "
        "NEW.purge_after IS NULL THEN NEW.updated_at := now(); NEW.purge_after := "
        "NEW.updated_at + interval '30 days'; END IF; RETURN NEW; END;"
    )
    if normalized_source != expected_source:
        raise MigrationLedgerError("retention function body is not trusted")

    cur.execute(
        """
        SELECT trigger_record.tgname, table_record.relname,
               trigger_record.tgfoid, trigger_record.tgenabled,
               pg_get_triggerdef(trigger_record.oid, true)
          FROM pg_trigger trigger_record
          JOIN pg_class table_record ON table_record.oid = trigger_record.tgrelid
          JOIN pg_namespace namespace_record ON namespace_record.oid = table_record.relnamespace
         WHERE namespace_record.nspname = 'app'
           AND table_record.relname = ANY(%s)
           AND NOT trigger_record.tgisinternal
        """,
        (list(_CHAT_COLUMNS),),
    )
    triggers = cur.fetchall()
    expected_definition = (
        "CREATE TRIGGER chat_action_detached_retention_trigger BEFORE UPDATE OF "
        "thread_id ON app.chat_action FOR EACH ROW EXECUTE FUNCTION "
        "app.set_chat_action_detached_retention()"
    )
    if triggers != [
        (
            "chat_action_detached_retention_trigger",
            "chat_action",
            function_oid,
            "O",
            expected_definition,
        )
    ]:
        raise MigrationLedgerError("global chat trigger definition is not trusted")


def _validate_global_chat_schema(cur: psycopg.Cursor) -> None:
    """Fail closed if any object 0001 could adopt is not exactly trusted."""
    _validate_global_chat_sequence(cur)
    _validate_global_chat_tables(cur)
    _validate_global_chat_indexes(cur)
    _validate_global_chat_function_and_trigger(cur)


def run_migrations(admin_url: str, directory: Path) -> list[str]:
    """Apply every pending migration atomically under one transaction lock.

    Files are fully validated and decoded before a database connection is
    opened. Each file is then passed to PostgreSQL intact: SQL is deliberately
    not split on semicolons because functions and procedural blocks contain
    meaningful internal semicolons.
    """
    migrations = discover_migrations(directory)
    sql_by_version: dict[str, str] = {}
    for migration in migrations:
        raw, sql = _read_utf8(migration.path)
        if sha256(raw).hexdigest() != migration.checksum:
            raise MigrationDiscoveryError(
                f"migration changed during discovery: {migration.path.name}"
            )
        sql_by_version[migration.version] = sql

    applied_now: list[str] = []
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_ID,))
                _validate_app_schema(cur)
                # Runtime DML never requires object creation. Revoke this
                # before inspecting or creating the administrative ledger.
                cur.execute("REVOKE CREATE ON SCHEMA app FROM app_rw, PUBLIC")

                ledger_oid = _ledger_oid(cur)
                if ledger_oid is None:
                    # No IF NOT EXISTS: a concurrent untrusted create must
                    # conflict and abort rather than be silently adopted.
                    cur.execute(
                        """
                        CREATE TABLE app.schema_migration (
                            version text PRIMARY KEY,
                            checksum text NOT NULL,
                            applied_at timestamptz NOT NULL DEFAULT now()
                        )
                        """
                    )
                    ledger_oid = _ledger_oid(cur)
                    assert ledger_oid is not None

                _validate_ledger(cur, ledger_oid)
                _revoke_ledger_access(cur)
                cur.execute("SELECT version, checksum FROM app.schema_migration")
                applied = dict(cur.fetchall())

                discovered_versions = {
                    migration.version for migration in migrations
                }
                missing_versions = sorted(set(applied) - discovered_versions)
                if missing_versions:
                    raise MigrationDiscoveryError(
                        "applied migration versions missing from disk: "
                        + ", ".join(missing_versions)
                    )

                for migration in migrations:
                    existing_checksum = applied.get(migration.version)
                    if existing_checksum is not None:
                        if existing_checksum != migration.checksum:
                            raise MigrationChecksumError(
                                "checksum mismatch for applied migration "
                                f"{migration.version} ({migration.path.name})"
                            )
                        if migration.version == "0001":
                            _validate_global_chat_schema(cur)
                        continue

                    cur.execute(sql_by_version[migration.version])
                    if migration.version == "0001":
                        _validate_global_chat_schema(cur)
                    cur.execute(
                        """
                        INSERT INTO app.schema_migration (version, checksum)
                        VALUES (%s, %s)
                        """,
                        (migration.version, migration.checksum),
                    )
                    applied_now.append(migration.version)

    return applied_now
