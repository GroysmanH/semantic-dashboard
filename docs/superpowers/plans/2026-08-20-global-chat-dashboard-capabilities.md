# Global Chat and Dashboard Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish dashboard tabs/exports and add a privacy-gated global assistant that can answer from visible cards, preview and confirm typed dashboard mutations, stream generated dashboards, verify numeric claims, and undo its work.

**Architecture:** Preserve `/ask` and `/query` as the existing history-free semantic path. Add a separate browser-global chat thread whose deterministic context is optionally populated from visible active-dashboard rows, whose model output is a strict discriminated union, and whose mutations become frozen server-resolved plans before confirmation. PostgreSQL stores transcript metadata, plans, effects, generation items, and replayable events; it never stores chat row context, except for an explicitly expiring transient-query cache.

**Tech Stack:** Python 3.12, FastAPI 0.115+, Pydantic 2.9+, Psycopg 3.2+, PostgreSQL 16, React 18.3, TypeScript 5.6, Vite 5, Vitest/Testing Library, react-grid-layout, Vega-Lite, dnd-kit, Server-Sent Events.

**Spec:** `docs/superpowers/specs/2026-08-20-global-chat-dashboard-capabilities-design.md`

## Global Constraints

- Keep the application light-theme only; do not introduce dark-mode tokens or media-query variants.
- `CHAT_ENABLED=false` and `CHAT_SEES_DATA=false` are the deployment defaults.
- Visible rows enter an LLM prompt only when both the server gate and remembered per-browser consent are true.
- The query/refinement prompt remains row-free, SQL-free, byte-stable, and history-free.
- Persisted chat messages never contain raw board rows or the assembled context string.
- Context limits are 2,000 exact rows, 60,000 serialized characters, and six eligible global turns.
- One browser thread has at most one pending mutation plan.
- Every mutation is previewed and confirmed; confirmation executes the frozen plan without a second planning call.
- Structural multi-operation plans are transactional. Streaming card generation is the only partial-success exception.
- The dashboard grid has 12 columns; model coordinates are clamped and resolved collision-free in every direction.
- Generation runs at most two card workers concurrently and never silently changes provider or tier.
- Direct menu deletion remains permanent; confirmed chat deletion is soft and undoable; the final visible dashboard cannot be deleted.
- Numeric claims are shown only after deterministic source resolution and recomputation.
- Unit/integration tests use fake LLM clients and make no API calls. Live evals prefer DeepSeek/NVIDIA, then Gemini; paid providers require an explicit CLI selection.
- Preserve the warehouse/application role split in `db/init/04_grants.sql`.

---

## Phase 0: Documentation Discovery and Existing Patterns

### Allowed APIs

- Copy Pydantic’s documented [`Annotated[Union[...], Field(discriminator=...)]`](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions) pattern and [`ConfigDict(extra="forbid")`](https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.extra). Do not use an untagged union or one model with unrelated optional fields.
- Copy Psycopg’s documented [`with psycopg.connect(..., autocommit=True)` plus `with conn.transaction()`](https://www.psycopg.org/psycopg3/docs/basic/transactions.html#transaction-contexts) pattern for atomic migration/effect blocks.
- Use PostgreSQL’s documented [`pg_advisory_xact_lock`](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS) for startup migration serialization. Do not use a session lock that survives rollback.
- Copy FastAPI’s documented [`StreamingResponse(async_generator(), media_type="text/event-stream")`](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse) pattern and include regular awaits in the generator.
- Follow the WHATWG [`text/event-stream`, `event`, `data`, `id`, and `Last-Event-ID`](https://html.spec.whatwg.org/multipage/server-sent-events.html) wire format. Use a GET subscription after the POST confirmation so native `EventSource` can reconnect.
- Copy dnd-kit’s [`DndContext`, pointer/keyboard sensors, `SortableContext`, `useSortable`, `arrayMove`, and `sortableKeyboardCoordinates`](https://docs.dndkit.com/presets/sortable) pattern; retain Enter/Space/arrow/Escape keyboard behavior from its [accessibility guide](https://docs.dndkit.com/guides/accessibility).
- Follow React’s rule that network subscriptions established in [`useEffect`](https://react.dev/reference/react/useEffect) return symmetrical cleanup.

### Existing repository patterns to copy

- Structured provider seam: `backend/app/llm/client.py:116-120` and `backend/app/llm/client.py:439-470`.
- One-retry validation and rate-limit exception ordering: `backend/app/llm/query_step.py:113-170`.
- Semantic render/trust payload: `backend/app/render.py:49-130`.
- Board-scoped layout persistence and grid constants: `backend/app/store/cards.py:90-157`.
- Route tests without FastAPI lifespan shutdown: `backend/tests/test_boards.py:18-27`.
- Generated Python-to-TypeScript API contract: `scripts/gen_types.py:18-38` and `frontend/package.json:10`.
- Browser-local active-board persistence: `frontend/src/App.tsx:10-59`.
- Existing transient card panel measurement and canonical layout separation: `frontend/src/components/Board.tsx:247-330`.
- Existing PNG/CSV/JSON implementations: `frontend/src/export/`.

### Discovery verification

- [ ] Confirm installed dependency floors match `backend/pyproject.toml` and `frontend/package.json` before copying APIs.
- [ ] Run `git status --short --branch` and verify implementation begins from a clean `codex/work` or a new isolated worktree.
- [ ] Run `docker compose exec -T backend pytest -q` and `docker compose exec -T frontend npm test` to record the pre-change baseline.
- [ ] Run `docker compose exec -T frontend npm run build` to catch the host-specific Rollup optional-dependency issue before code changes; if missing, perform a clean frontend dependency install instead of adding an application workaround.

### Phase 0 anti-pattern guards

- Do not invent an LLM tool-calling loop; NVIDIA/DeepSeek must stay on the common structured JSON contract.
- Do not implement POST-only SSE parsing in the browser when a POST-confirm/GET-subscribe resource split permits native reconnection.
- Do not store raw assembled context in `chat_message.body` for debugging.
- Do not build mutations directly into the chat route; planning, applying, and streaming are separate units.

---

## Phase 1: Close Stage 1 — Sortable Tabs and Dedicated Export Menus

**Documentation references:** dnd-kit Sortable and Accessibility links in Phase 0; existing export functions under `frontend/src/export/`.

**Phase verification:** tab order persists after reload; pointer and keyboard sorting announce moves; card and board exports are in separate right-side `Export ▾` controls; New card stays upper-left.

**Anti-pattern guards:** no pointer-only sorting, no export actions inside the destructive overflow menu, no optimistic order left on screen after API failure.

### Task 1: Finish tab ordering and export placement

**Files:**
- Create: `frontend/src/components/SortableTab.tsx`
- Create: `frontend/src/components/ExportMenu.tsx`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/src/App.tsx:14-124`
- Modify: `frontend/src/components/TabBar.tsx:1-114`
- Modify: `frontend/src/components/CardHeader.tsx:40-205`
- Modify: `frontend/src/components/Board.tsx:288-520`
- Modify: `frontend/src/components/Card.tsx:94-187`
- Modify: `frontend/src/styles.css`
- Modify: `docs/design.md`
- Test: `frontend/src/components/TabBar.test.tsx`
- Test: `frontend/src/components/Board.test.tsx`
- Test: `frontend/src/components/CardHeader.test.tsx`

**Interfaces:**
- Consumes: `api.reorderBoards(order: string[]): Promise<void>`, `chartPng`, `csvBlob`, `boardPng`, `dashboardBlob`.
- Produces: `TabBar.onReorder(order: string[]): void`; reusable `ExportMenu({label, items})`; dedicated card and board export menus.

- [ ] **Step 1: Install documented sortable dependencies**

Run:

```bash
cd frontend
npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities
```

Expected: `package.json` and `package-lock.json` contain all three direct dependencies.

- [ ] **Step 2: Write failing accessible-sort tests**

Add tests that render three boards, invoke dnd-kit’s keyboard path, and assert:

```tsx
expect(props.onReorder).toHaveBeenCalledWith(["b", "a", "c"]);
expect(screen.getByRole("tab", {name: "Operations"})).toHaveAttribute("aria-selected", "true");
```

Also assert Escape cancels, a failed `api.reorderBoards` restores the old array, and tab selection/inline rename still work.

- [ ] **Step 3: Run the tests to verify the reorder contract is missing**

Run: `cd frontend && npm test -- src/components/TabBar.test.tsx`

Expected: FAIL because `TabBar` has no `onReorder` prop or sortable semantics.

- [ ] **Step 4: Implement `SortableTab` from the documented dnd-kit pattern**

Use these public imports and no undocumented sensor hooks:

```tsx
import {useSortable} from "@dnd-kit/sortable";
import {CSS} from "@dnd-kit/utilities";

export interface SortableTabProps {
  id: string;
  disabled: boolean;
  children: React.ReactNode;
}

export function SortableTab({id, disabled, children}: SortableTabProps) {
  const {attributes, listeners, setNodeRef, transform, transition, isDragging} =
    useSortable({id, disabled});
  return (
    <li
      ref={setNodeRef}
      className={`tab${isDragging ? " tab-dragging" : ""}`}
      style={{transform: CSS.Transform.toString(transform), transition}}
    >
      <button className="tab-drag" aria-label="Reorder dashboard" {...attributes} {...listeners} />
      {children}
    </li>
  );
}
```

Wrap the list in `DndContext` and a horizontal `SortableContext`; configure `PointerSensor` with an 8px activation distance and `KeyboardSensor` with `sortableKeyboardCoordinates`. On a valid drop, call `arrayMove` and `onReorder` once.

- [ ] **Step 5: Add optimistic reorder with rollback in `App.tsx`**

Implement:

```ts
const reorder = (order: string[]) => guard(async () => {
  const before = boards;
  const next = order.map((id, position) => ({
    ...before.find((board) => board.id === id)!, position,
  }));
  setBoards(next);
  try {
    await api.reorderBoards(order);
  } catch (error) {
    setBoards(before);
    throw error;
  }
});
```

Pass it as `onReorder`. Keep active-board selection independent of drag activation.

- [ ] **Step 6: Write failing export-placement tests**

Assert the card header contains a button named `Export Oil by month`, its menu contains PNG and CSV, `Card actions` contains only removal, and the masthead’s right action host contains one active-dashboard Export button with PNG and JSON.

- [ ] **Step 7: Implement a focused export menu and split masthead hosts**

Create:

```ts
export interface ExportItem {
  label: string;
  disabled?: boolean;
  run: () => void | Promise<void>;
}

export default function ExportMenu(props: {
  label: string;
  items: ExportItem[];
  align?: "start" | "end";
}): JSX.Element;
```

Give it outside-click/Escape dismissal, `aria-haspopup="menu"`, `aria-expanded`, focus return, and an error callback handled by its owner. Add `board-export-action` at the masthead’s far right; keep `board-primary-action` at the upper left. Remove PNG/CSV from `Card actions` and mount `ExportMenu` beside it.

Store the live Vega view in state as well as the export ref so the card rerenders when PNG becomes available; do not derive menu availability from a ref mutation that cannot trigger render. Keep CSV tied to the full `render.rows`, not presentation-only `chart_rows`.

- [ ] **Step 8: Document dashboard JSON version 1**

Add the exact shape already emitted by `frontend/src/export/json.ts` to `docs/design.md`, including `version`, `title`, `exported_at`, and each card’s `semantic_query`, `chart_hint`, `layout`, and `ttl_seconds`. State that import is unsupported.

- [ ] **Step 9: Verify Stage 1**

Run:

```bash
cd frontend
npm test -- src/components/TabBar.test.tsx src/components/Board.test.tsx src/components/CardHeader.test.tsx
npm run build
```

Expected: all tests PASS and the build completes.

- [ ] **Step 10: Commit Stage 1 closure**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src docs/design.md
git commit -m "feat: finish sortable tabs and export menus"
```

---

## Phase 2: Schema Evolution, Persistence, and Typed Contracts

**Documentation references:** Psycopg transaction contexts, PostgreSQL advisory locks, and Pydantic discriminated unions from Phase 0.

**Phase verification:** migrations apply once and detect checksum drift; existing volumes gain chat tables; global transcript metadata persists without rows; generated TypeScript reflects every action variant.

**Anti-pattern guards:** no reset-only migration, no DDL outside the locked transaction, no JSON blob whose action-specific validity is checked only in route code, no cascading transcript deletion when a board is removed.

### Task 2: Add the sequential raw-SQL migration runner

**Files:**
- Create: `backend/app/migrations.py`
- Create: `db/migrations/0001_global_chat.sql`
- Modify: `backend/app/config.py:9-73`
- Modify: `backend/app/main.py:11-16`
- Modify: `.env.example`
- Modify: `db/init/03_app.sql`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `settings.admin_url`, numbered `db/migrations/*.sql` files.
- Produces: `run_migrations(admin_url: str, directory: Path) -> list[str]` returning applied versions.

- [ ] **Step 1: Write migration-runner failure tests**

Cover ordered discovery, apply-once, checksum mismatch, rollback of both DDL and version row on invalid SQL, and two concurrent runner calls. Use a temporary directory with `0001_one.sql`, `0002_two.sql`; use a unique temporary schema/table prefix per test and drop it in fixture cleanup.

- [ ] **Step 2: Run the migration tests and confirm the module is absent**

Run: `docker compose exec -T backend pytest tests/test_migrations.py -v`

Expected: FAIL importing `app.migrations`.

- [ ] **Step 3: Implement the documented transaction/advisory-lock pattern**

Define:

```python
@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str

def discover_migrations(directory: Path) -> list[Migration]: ...

def run_migrations(admin_url: str, directory: Path) -> list[str]:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (0x4E4C3256495A,))
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS app.schema_migration (
                        version text PRIMARY KEY,
                        checksum text NOT NULL,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                """)
                # Validate every applied checksum, execute unapplied SQL in order,
                # then insert its version and checksum in the same transaction.
```

Reject duplicate version prefixes and non-UTF-8 files before opening the transaction. Do not split SQL on semicolons; pass each complete migration file to `execute()`.

- [ ] **Step 4: Add final schema and migration SQL**

`0001_global_chat.sql` must add `board.revision`, `board.deleted_at`, `card.deleted_at`, and create:

```sql
app.chat_thread(id uuid PRIMARY KEY, created_at timestamptz, updated_at timestamptz)
app.chat_message(id uuid PRIMARY KEY, thread_id uuid, role text, body jsonb,
                 active_board_id uuid NULL, active_board_title text,
                 data_exposed boolean, created_at timestamptz)
app.chat_plan(id uuid PRIMARY KEY, thread_id uuid, action jsonb, resolved jsonb,
              basis jsonb, status text, created_at timestamptz, updated_at timestamptz)
app.chat_action(id uuid PRIMARY KEY, thread_id uuid NULL, plan_id uuid,
                board_id uuid NULL, provider text, model text, effects jsonb,
                status text, cancel_requested boolean, created_at timestamptz,
                updated_at timestamptz, purge_after timestamptz NULL)
app.chat_action_item(id uuid PRIMARY KEY, action_id uuid, ordinal integer,
                     request jsonb, card_id uuid NULL, status text, error text,
                     created_at timestamptz, updated_at timestamptz)
app.chat_event(id bigserial PRIMARY KEY, action_id uuid, kind text,
               payload jsonb, created_at timestamptz)
app.chat_transient_result(id uuid PRIMARY KEY, thread_id uuid, query jsonb,
                          chart_hint text, title text, cache jsonb,
                          expires_at timestamptz, created_at timestamptz)
```

Use explicit CHECK constraints for roles/statuses, indexes on thread/message time and action/event ID, and no board/card foreign key from transcript JSON. Messages, pending plans, and transients cascade from a cleared thread. Completed `chat_action.thread_id` uses `ON DELETE SET NULL`; its items/events cascade from the action and `purge_after` limits detached retention to 30 days. Mirror the final definitions in `db/init/03_app.sql` using `IF NOT EXISTS` only where the migration must no-op on a fresh final schema.

- [ ] **Step 5: Run migrations before pools open**

Add `migration_dir: Path = Path("/db/migrations")` and the exact chat settings from Global Constraints to `Settings`. In `lifespan`, call `run_migrations(settings.admin_url, settings.migration_dir)` before `open_pools()`.

- [ ] **Step 6: Verify and commit migrations**

Run:

```bash
docker compose restart backend
docker compose exec -T backend pytest tests/test_migrations.py tests/test_grants.py -v
```

Expected: migrations report no checksum errors on the second startup; both tests PASS; `warehouse_ro` still lacks `USAGE` on `app`.

```bash
git add backend/app/migrations.py backend/app/config.py backend/app/main.py backend/tests/test_migrations.py db .env.example
git commit -m "feat: add transactional app migrations"
```

### Task 3: Make board revisions and chat persistence authoritative

**Files:**
- Modify: `backend/app/store/cards.py:14-157`
- Modify: `backend/app/routes/boards.py:30-76`
- Modify: `backend/app/routes/cards.py:21-106`
- Create: `backend/app/store/chat.py`
- Test: `backend/tests/test_boards.py`
- Create: `backend/tests/test_chat_store.py`

**Interfaces:**
- Consumes: migrated tables from Task 2.
- Produces: visible-only board/card reads, explicit `hard_delete_*` and `soft_delete_*`, monotonic board revisions, and chat store functions used by every later task.

- [ ] **Step 1: Write failing revision/deletion tests**

Assert semantic edits, layout saves, card membership, board rename, and board reorder increment affected revisions; cache-only refresh updates do not. Assert visible reads hide soft-deleted records, chat restore reveals them, direct routes hard-delete them, and no store function permits soft-deleting the last visible board.

- [ ] **Step 2: Write failing global-thread store tests**

Test `create_thread`, append/list messages across two active board labels, latest-N ordering, data-exposed filtering, one-pending-plan uniqueness, plan status transitions, action/effect storage, ordered items, monotonically ordered events, transient expiry, and `clear_thread` behavior.

- [ ] **Step 3: Run the store tests to establish failures**

Run: `docker compose exec -T backend pytest tests/test_boards.py tests/test_chat_store.py -v`

Expected: FAIL on missing columns/module and unchanged revisions.

- [ ] **Step 4: Refactor card/board store mutation boundaries**

Add `revision` to `BOARD_COLUMNS`; filter `deleted_at IS NULL` in normal list/get functions. Expose:

```python
def hard_delete_board(board_id: UUID) -> None: ...
def soft_delete_board(board_id: UUID, *, conn=None) -> dict: ...
def restore_board(board_id: UUID, *, conn=None) -> dict: ...
def hard_delete_card(card_id: UUID) -> None: ...
def soft_delete_card(card_id: UUID, *, conn=None) -> dict: ...
def restore_card(card_id: UUID, *, conn=None) -> dict: ...
def board_basis(board_ids: Iterable[UUID]) -> dict[str, int]: ...
```

Keep `delete_board`/`delete_card` as temporary aliases to hard-delete only until all existing callers are renamed in this task, then remove the aliases. For substantive card updates, update the card and increment its owning board revision in the same connection transaction. Treat only `cache`, `state`, and derived `vega_spec` as non-substantive.

- [ ] **Step 5: Implement focused chat persistence functions**

`backend/app/store/chat.py` exports these exact seams:

```python
def create_thread() -> dict: ...
def get_thread(thread_id: UUID) -> dict | None: ...
def list_messages(thread_id: UUID, *, limit: int | None = None,
                  include_data_exposed: bool = True) -> list[dict]: ...
def append_message(thread_id: UUID, *, role: str, body: dict,
                   active_board_id: UUID | None, active_board_title: str,
                   data_exposed: bool) -> dict: ...
def clear_thread(thread_id: UUID, *, tombstone_days: int) -> None: ...
def save_pending_plan(thread_id: UUID, *, action: dict, resolved: dict,
                      basis: dict) -> dict: ...
def transition_plan(plan_id: UUID, *, expected: str, status: str) -> dict: ...
def create_action(plan: dict, *, provider: str, model: str,
                  effects: dict) -> dict: ...
def append_event(action_id: UUID, kind: str, payload: dict) -> dict: ...
def list_events(action_id: UUID, *, after_id: int = 0) -> list[dict]: ...
def purge_expired_transients() -> int: ...
```

Use `dict_row`, parameterized SQL, and the same connection/transaction discipline as `store/cards.py`. Never accept table or column names from request values.

`clear_thread` must cancel pending/running work, delete messages/plans/transients, detach completed actions by setting `thread_id=NULL` and `purge_after=now()+tombstone_days`, then delete the old thread. Purge detached actions only after `purge_after`; never return them to a model or new thread.

- [ ] **Step 6: Update existing routes to explicit hard deletion**

Change direct DELETE routes to `hard_delete_board`/`hard_delete_card`; return 409 when direct board deletion would remove the last visible board. Preserve current frontend confirmation copy.

- [ ] **Step 7: Verify and commit persistence**

Run: `docker compose exec -T backend pytest tests/test_boards.py tests/test_chat_store.py tests/test_undo.py -v`

Expected: PASS; existing per-card Undo remains one-step.

```bash
git add backend/app/store backend/app/routes backend/tests
git commit -m "feat: persist global chat and board revisions"
```

### Task 4: Define the strict chat/action API and generate TypeScript

**Files:**
- Create: `backend/app/chat/__init__.py`
- Create: `backend/app/chat/schema.py`
- Modify: `scripts/gen_types.py:18-38`
- Modify: `backend/app/routes/ask.py:69-80`
- Modify: `backend/tests/test_model_routing.py`
- Modify: `frontend/src/api/schema.json`
- Modify: `frontend/src/api/types.gen.ts`
- Create: `backend/tests/test_chat_schema.py`

**Interfaces:**
- Consumes: `SemanticQuery`, `ChartHint`, `Render`.
- Produces: `ChatAction`, `ChatTurnResponse`, `PendingPlanView`, `ChatMessageOut`, `ChatEvent`, `CardRequest`, provider tier capabilities, and generated TypeScript equivalents.

- [ ] **Step 1: Write failing discriminated-union tests**

Test one valid instance of every action, reject extra fields, reject missing target fields, reject `cards=[]`, reject more than six cards, reject duplicate `request_id`, reject zero/negative layouts, and verify JSON Schema exposes `action` as the discriminator.

- [ ] **Step 2: Run schema tests and confirm the package is absent**

Run: `docker compose exec -T backend pytest tests/test_chat_schema.py -v`

Expected: FAIL importing `app.chat.schema`.

- [ ] **Step 3: Implement strict reusable value models**

Use immutable literals and default factories:

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class LayoutRequest(StrictModel):
    x: int
    y: int
    w: int
    h: int

class CardRequest(StrictModel):
    request_id: str
    question: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=120)
    chart_hint: ChartHint | None = None
    layout: LayoutRequest | None = None

class ClaimOperand(StrictModel):
    card_id: UUID
    field: str
    keys: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

class Claim(StrictModel):
    text: str
    displayed_value: str
    operation: Literal["exact", "rounded", "sum", "difference", "ratio",
                       "percentage", "percentage_change"]
    operands: list[ClaimOperand] = Field(min_length=1, max_length=20)
```

- [ ] **Step 4: Implement one model per action**

Define literal-tagged models for `answer`, `run_query`, `clarify`, `refuse`, `new_cards`, `edit_card`, `new_dashboard`, `layout`, `rename_dashboard`, `reorder_dashboards`, `delete_card`, and `delete_dashboard`. Then define:

```python
ChatAction = Annotated[
    AnswerAction | RunQueryAction | ClarifyAction | RefuseAction |
    NewCardsAction | EditCardAction | NewDashboardAction | LayoutAction |
    RenameDashboardAction | ReorderDashboardsAction |
    DeleteCardAction | DeleteDashboardAction,
    Field(discriminator="action"),
]

class ChatModelResponse(StrictModel):
    turn: ChatAction
```

Pass `ChatModelResponse`, not the union alias, to `LLMClient.ask`; consume `.turn`. This preserves the existing `type[T] where T: BaseModel` provider contract. Mutating models carry intent only. They do not carry effects, SQL, provider IDs, or confirmation booleans. `EditCardAction` must carry the complete replacement `semantic_query: SemanticQuery` and `chart_hint`, not a natural-language instruction, so its confirmation preview can show an exact deterministic diff without another model call.

`RefuseAction` contains `reason`, `missing_metric: str | None`, and `request_text: str | None`; the latter two power copyable metric-request UI without persisting a backlog.

- [ ] **Step 5: Define transport envelopes and SSE event schema**

Add:

```python
class ChatTurnResponse(StrictModel):
    message: ChatMessageOut
    pending_plan: PendingPlanView | None = None
    transient_result: TransientResultView | None = None

class ChatThreadView(StrictModel):
    id: UUID
    messages: list[ChatMessageOut]
    pending_plan: PendingPlanView | None = None
    active_actions: list[ActionProgressView] = Field(default_factory=list)

class PlanEvent(StrictModel):
    version: Literal[1] = 1
    id: int
    action_id: UUID
    kind: Literal["plan"]
    payload: PlanEventPayload

class CardEvent(StrictModel):
    version: Literal[1] = 1
    id: int
    action_id: UUID
    kind: Literal["card"]
    payload: CardEventPayload

ChatEvent = Annotated[
    PlanEvent | ItemStartedEvent | CardEvent | ItemFailedEvent | StoppedEvent | DoneEvent,
    Field(discriminator="kind"),
]
```

Define one typed payload model for every event kind; do not use `dict[str, Any]` in the public event contract. `PendingPlanView` includes operation rows and resolved mini-grid layouts but not internal basis hashes or row data.

- [ ] **Step 6: Generate and inspect frontend types**

Extend `ApiContract` in `scripts/gen_types.py` with representative fields for all exported chat models. Extend `GET /layer` provider metadata to return `{default, available, capabilities: {provider: {strong_available, default_model, strong_model}}}` using `settings.models(provider)`; `strong_available` is true only when the two configured model IDs differ. Add `chat: {enabled: settings.chat_enabled, data_sharing_permitted: settings.chat_sees_data}` so the consent control can state both server gates truthfully. Run:

```bash
make types
cd frontend
npm run build
```

Expected: generated TypeScript contains an `action`-discriminated union and no `any` fields for action-specific payloads.

- [ ] **Step 7: Verify and commit contracts**

Run: `docker compose exec -T backend pytest tests/test_chat_schema.py tests/test_providers.py -v`

Expected: PASS across schema serialization and provider clients.

```bash
git add backend/app/chat backend/tests/test_chat_schema.py scripts/gen_types.py frontend/src/api
git commit -m "feat: define strict chat action contract"
```

---

## Phase 3: Privacy-Gated Read-Only Global Chat

**Documentation references:** existing provider/retry patterns listed in Phase 0; Pydantic strict models; React effect cleanup.

**Phase verification:** a global thread survives board switches and reloads; data-off prompts contain no values; data-on context is deterministic and bounded; answers and transient queries show source/trust evidence; no mutation is applied.

**Anti-pattern guards:** no current-board transcript foreign key, no context in the system prompt, no hidden warehouse rerun, no numeric claim rendered from model prose alone, no provider selector duplicated per card.

### Task 5: Build deterministic global context and the chat prompt

**Files:**
- Create: `backend/app/chat/context.py`
- Create: `backend/app/chat/prompt.py`
- Create: `backend/tests/test_chat_context.py`
- Create: `backend/tests/test_chat_prompt.py`

**Interfaces:**
- Consumes: visible boards/cards, rendered card rows, eligible transcript entries, layer metadata, data-sharing gates.
- Produces: `BuiltContext` and byte-stable `build_chat_system_prompt(layer) -> str`.

- [ ] **Step 1: Write failing data-off and global-history tests**

Construct two dashboards containing distinctive values such as `987654321`. With sharing off, assert the context contains dashboard/card titles, restatements, row counts, and dashboard labels on history, but contains neither the distinctive value nor any row dictionary. Mark one earlier message `data_exposed=True` and assert it is excluded only after consent is turned off.

- [ ] **Step 2: Write failing budget and ordering tests**

Cover exact-row priority (explicit card, selected card, title match, layout order), deterministic summaries, 2,000-row cutoff, 60,000-character cutoff, six-turn cutoff, stable serialization across repeated calls, and explicit truncation disclosures.

- [ ] **Step 3: Run tests and confirm the modules are absent**

Run: `docker compose exec -T backend pytest tests/test_chat_context.py tests/test_chat_prompt.py -v`

Expected: FAIL importing `app.chat.context` and `app.chat.prompt`.

- [ ] **Step 4: Implement typed context inputs and deterministic summaries**

Define:

```python
@dataclass(frozen=True)
class ContextLimits:
    max_rows: int = 2_000
    max_chars: int = 60_000
    history_turns: int = 6

@dataclass(frozen=True)
class BuiltContext:
    text: str
    data_exposed: bool
    exact_card_ids: tuple[str, ...]
    notices: tuple[str, ...]

def build_context(*, boards: list[dict], active_board: dict,
                  rendered_cards: list[dict], messages: list[dict],
                  question: str, selected_card_id: str | None,
                  share_rows: bool, limits: ContextLimits) -> BuiltContext: ...
```

Sort dictionaries and fields before `json.dumps(..., sort_keys=True, separators=(",", ":"), default=str)`. Summaries report structural types and row count in both modes; min/max/total/top-five values are added only when `share_rows=True`. For numeric totals use `Decimal(str(value))` and skip null/non-finite values.

- [ ] **Step 5: Implement the exact-row admission algorithm**

Normalize titles with lowercase word tokens. Rank cards by:

```python
(explicit_reference_rank, selected_rank, title_match_rank, layout_y, layout_x, card_id)
```

Append complete rows only while both budgets remain. Before appending each card, serialize the candidate whole context and fall back to its summary if either ceiling would be crossed. Add a human-readable notice naming every summarized card and its row count.

- [ ] **Step 6: Implement a byte-stable chat system prompt**

The system block describes the semantic grammar, strict action variants, confirmation boundary, numeric-claim schema, inactive-dashboard limits, and data-off refusal behavior. It contains no date, history, board ID/title, SQL, rows, provider name, or consent state. Put the current date, board context, eligible history, and user message into the user block after the provider cache breakpoint.

- [ ] **Step 7: Verify context invariants and commit**

Run:

```bash
docker compose exec -T backend pytest tests/test_chat_context.py tests/test_chat_prompt.py tests/test_llm.py -v
```

Expected: all tests PASS, including the existing query prompt invariants.

```bash
git add backend/app/chat/context.py backend/app/chat/prompt.py backend/tests/test_chat_context.py backend/tests/test_chat_prompt.py
git commit -m "feat: build privacy-gated chat context"
```

### Task 6: Verify every numeric claim deterministically

**Files:**
- Create: `backend/app/chat/verify.py`
- Create: `backend/tests/test_chat_verify.py`

**Interfaces:**
- Consumes: structured `Claim` objects and current `rows_by_card`.
- Produces: `VerificationResult` with verified claims, withdrawn claim texts, and source metadata for the frontend.

- [ ] **Step 1: Write failing exact/derived claim tests**

Cover exact integers/decimals, comma grouping, `K/M/B` suffixes, percentages, declared rounding, sum, difference, ratio, percentage, percentage change, nulls, duplicate key matches, missing cards/fields, invented values, zero denominators, and numeric literals hidden in `say`.

Require each `Claim.text` to contain exactly one numeric literal matching `displayed_value`; sentences needing multiple figures must be split into multiple claims.

- [ ] **Step 2: Run tests and confirm verification is absent**

Run: `docker compose exec -T backend pytest tests/test_chat_verify.py -v`

Expected: FAIL importing `app.chat.verify`.

- [ ] **Step 3: Implement strict source resolution**

Define:

```python
class VerifiedClaim(StrictModel):
    text: str
    displayed_value: str
    computed_value: Decimal
    source_card_ids: list[UUID]

class VerificationResult(StrictModel):
    safe_say: str
    claims: list[VerifiedClaim]
    withdrawn: list[str]

def verify_turn(*, say: str, claims: list[Claim],
                rows_by_card: dict[UUID, list[dict]]) -> VerificationResult: ...
```

Resolve an operand only when exactly one row matches every declared key and its field is finite numeric data. Ignore any model-supplied numeric value; read the resolved row value.

- [ ] **Step 4: Implement operation recomputation and formatting tolerance**

Use `Decimal` for all operations. For `rounded`, infer precision from `displayed_value`; for suffixes, normalize before comparison. Permit half a unit in the displayed last place, never a relative “close enough” window that would accept a materially different large number. Require two operands for difference/ratio/percentage/percentage-change and at least one for sum.

- [ ] **Step 5: Scrub unverifiable numeric prose**

Reject numeric literals in `say` after removing dates and source-chip labels. Omit failed claim sentences and append exactly: `I couldn’t verify that number from the visible card data.` once, regardless of failure count. Never render a flagged number with warning styling as if it may still be true.

- [ ] **Step 6: Verify and commit claim verification**

Run: `docker compose exec -T backend pytest tests/test_chat_verify.py -v`

Expected: PASS for every operation and refusal edge.

```bash
git add backend/app/chat/verify.py backend/tests/test_chat_verify.py
git commit -m "feat: verify chat numeric claims"
```

### Task 7: Orchestrate read-only turns and transient query results

**Files:**
- Create: `backend/app/chat/turn.py`
- Create: `backend/app/routes/chat.py`
- Modify: `backend/app/routes/__init__.py`
- Modify: `backend/app/main.py:8-30`
- Modify: `backend/app/store/chat.py`
- Modify: `backend/app/chat/schema.py`
- Create: `backend/tests/test_chat_turn.py`
- Create: `backend/tests/test_chat_routes.py`
- Create: `backend/tests/test_chat_transient.py`

**Interfaces:**
- Consumes: `LLMClient.ask`, `build_context`, `build_chat_system_prompt`, `verify_turn`, `render`, chat store.
- Produces: thread CRUD, `POST /chat/threads/{thread_id}/turns`, transient rerun, and a stored read-only transcript.

- [ ] **Step 1: Write failing turn-discipline tests**

Use fake clients to assert one schema retry, `LLMRateLimited` re-raise before `LLMError`, provider/model reporting, data-off value refusal, global dashboard labels, ambiguous post-switch clarification, verified answers, and no mutation store calls for read-only actions.

- [ ] **Step 2: Write failing route/transient tests**

Cover thread creation/load/clear, unknown thread 404, server-disabled chat 404, two-gate consent, no raw row values in stored message bodies, validated `run_query`, restatement/SQL/freshness response, 15-minute expiry, explicit rerun, and no silent execution during transcript reload.

- [ ] **Step 3: Run tests and establish failures**

Run:

```bash
docker compose exec -T backend pytest tests/test_chat_turn.py tests/test_chat_routes.py tests/test_chat_transient.py -v
```

Expected: FAIL on missing turn/router symbols.

- [ ] **Step 4: Implement the provider-neutral turn loop**

Define:

```python
@dataclass(frozen=True)
class TurnRequest:
    thread_id: UUID
    active_board_id: UUID
    question: str
    provider: Provider
    hard: bool
    share_visible_data: bool
    selected_card_id: UUID | None = None

def run_turn(request: TurnRequest, *, client: LLMClient,
             mutation_planner: Callable | None = None) -> ChatTurnResponse: ...
```

Build system/context once per attempt. Retry only `LLMSchemaError` or semantic validation errors once with the deterministic reason. Re-raise `LLMRateLimited`; convert other `LLMError` to `refuse`. If a mutation arrives before `mutation_planner` is supplied, return a clear `refuse` action; do not apply it.

Call `client.ask(system, user, ChatModelResponse)` and dispatch `response.turn`; never pass the `Annotated` union alias directly to the provider seam.

- [ ] **Step 5: Dispatch read-only action variants**

- `answer`: resolve current rows, call `verify_turn`, store only safe prose/verified source descriptors.
- `run_query`: validate and render through the existing semantic pipeline; store a 15-minute cache envelope in `chat_transient_result`; return `Render` without its internal cache.
- `clarify` and `refuse`: persist their text with no effects.

For an unavailable metric, `refuse` must name the missing metric and include the original user request in a copyable `request_text` field. Do not append it to a file or database backlog.

Tag assistant and user messages `data_exposed=True` only when exact rows were admitted to the prompt. Store context notices, not context payloads.

- [ ] **Step 6: Add thread and read-only routes**

Implement:

```text
POST   /chat/threads
GET    /chat/threads/{thread_id}
DELETE /chat/threads/{thread_id}
POST   /chat/threads/{thread_id}/turns
POST   /chat/transient/{result_id}/rerun
```

Validate that `active_board_id` is visible. Compute `share_rows = settings.chat_sees_data and body.share_visible_data`; never trust the browser flag alone. Clear cancels pending plans/actions through the store seam, removes transcript/transient rows, and returns a new server-issued thread ID.

- [ ] **Step 7: Verify route behavior and existing query invariants**

Run:

```bash
docker compose exec -T backend pytest tests/test_chat_turn.py tests/test_chat_routes.py tests/test_chat_transient.py tests/test_llm.py tests/test_grants.py -v
```

Expected: PASS; stored messages contain no row dictionaries or compiled SQL context blobs.

- [ ] **Step 8: Commit read-only backend chat**

```bash
git add backend/app/chat backend/app/routes backend/app/store/chat.py backend/app/main.py backend/tests
git commit -m "feat: add read-only global chat turns"
```

### Task 8: Add the browser-global chat drawer and provider preference

**Files:**
- Create: `frontend/src/api/chat.ts`
- Create: `frontend/src/state/preferences.ts`
- Create: `frontend/src/components/ChatPanel.tsx`
- Create: `frontend/src/components/ChatMessage.tsx`
- Create: `frontend/src/components/ChatResult.tsx`
- Create: `frontend/src/components/ChatPanel.test.tsx`
- Modify: `frontend/src/App.tsx:1-124`
- Modify: `frontend/src/api/client.ts:1-103`
- Modify: `frontend/src/components/Board.tsx:139-520`
- Modify: `frontend/src/components/Card.tsx:20-227`
- Modify: `frontend/src/components/EmptyCard.tsx:1-92`
- Modify: `frontend/src/components/AskBar.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/components/Board.test.tsx`

**Interfaces:**
- Consumes: generated chat types and Task 7 endpoints.
- Produces: remembered global thread/provider/data consent/drawer state; provider-aware card creation/refinement; read-only chat UI.

- [ ] **Step 1: Write failing preference and drawer tests**

Assert a new browser starts closed and unconsented; opening, width, pinned state, provider, and consent survive remount; `Ctrl/Command+Shift+A` toggles; overlay mode does not change saved layout; pinned mode changes available width only; Clear rotates the thread ID; provider selection affects chat, empty-card questions, and refinement.

- [ ] **Step 2: Write failing data-gate and transient-result UI tests**

Assert the consent checkbox is disabled with server-disabled explanation, opted-in copy names the selected provider, context truncation notices appear, transient result renders restatement/row count/freshness/SQL, expired results show `Run again`, and no reload call automatically reruns it.

- [ ] **Step 3: Run tests to establish failures**

Run: `cd frontend && npm test -- src/components/ChatPanel.test.tsx src/components/Board.test.tsx`

Expected: FAIL because chat components/preferences do not exist.

- [ ] **Step 4: Implement typed chat API functions**

Expose:

```ts
export const chatApi = {
  createThread(): Promise<ChatThreadView>,
  getThread(threadId: string): Promise<ChatThreadView>,
  clearThread(threadId: string): Promise<ChatThreadView>,
  sendTurn(threadId: string, body: ChatTurnIn): Promise<ChatTurnResponse>,
  rerunTransient(resultId: string): Promise<TransientResultView>,
};
```

Reuse the base request/error behavior from `client.ts`; do not create a second base URL constant with different error handling.

- [ ] **Step 5: Implement safe local preference helpers**

Use namespaced keys:

```text
semantic-dashboard:chat-thread
semantic-dashboard:provider
semantic-dashboard:chat-open
semantic-dashboard:chat-width
semantic-dashboard:chat-pinned
semantic-dashboard:chat-share-data
```

Every getter catches blocked/private-storage errors and validates enums/numeric width before returning a default. Clamp drawer width to 320–640px.

- [ ] **Step 6: Lift provider ownership into `App.tsx`**

Remove provider state/radio buttons from `EmptyCard`. Keep one remembered provider in `App`, pass it through `Board` to every `Card`, and include it in every `api.ask`. Add a per-form stronger checkbox to new-card, refinement, and chat composer; reset only that form’s checkbox after submission. Disable it with explanatory text when default and strong model IDs match.

- [ ] **Step 7: Implement the closed-by-default global drawer**

Keep `ChatPanel` mounted at the `App` level across board switches. Its props are:

```ts
interface ChatPanelProps {
  threadId: string;
  activeBoardId: string;
  activeBoardTitle: string;
  boards: BoardSummary[];
  provider: Provider;
  providerCapabilities: ProviderCapabilities;
  shareVisibleData: boolean;
  selectedCardId: string | null;
  onProviderChange(provider: Provider): void;
  onConsentChange(value: boolean): void;
  onNavigate(boardId: string, cardId?: string): void;
}
```

Overlay with a fixed right drawer by default. Pinning changes the workspace’s available CSS width, invokes the board’s existing resize measurement, and never calls `saveLayout`. Add a visible masthead Chat button and documented keyboard shortcut.

- [ ] **Step 8: Render read-only messages and trust surfaces**

`ChatMessage` renders user text, safe assistant prose, verified claims, context notices, and dead/alive source chips. `ChatResult` reuses `VegaChart` and `SqlPanel` without creating a persisted card. Keep compiled SQL below the transient chart. Surface backend/provider failures in the message, not a disappearing toast.

- [ ] **Step 9: Verify and commit the read-only vertical slice**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: PASS; chat begins closed and card layouts are unchanged by pin/unpin.

```bash
git add frontend/src
git commit -m "feat: add global read-only chat drawer"
```

---

## Phase 4: Frozen Mutation Plans, Confirmation, and Undo

**Documentation references:** Pydantic discriminated unions and Psycopg transaction contexts in Phase 0; current query diff at `backend/app/semantic/diff.py`; current board drag resolver at `frontend/src/components/Board.tsx:21-130`.

**Phase verification:** model intent becomes one exact server-resolved preview; stale plans return 409; confirmation makes no planning-model call; structural effects are atomic; soft deletion and every chat action undo against their original targets after tab switches.

**Anti-pattern guards:** no model-authored final coordinates applied without resolution, no confirmation-time replanning, no mutation from the preview endpoint, no effects journal containing live connection objects or unbounded row caches.

### Task 9: Resolve model intent into an exact frozen plan

**Files:**
- Create: `backend/app/chat/layout.py`
- Create: `backend/app/chat/plan.py`
- Modify: `backend/app/chat/schema.py`
- Modify: `backend/app/chat/turn.py`
- Modify: `backend/app/routes/chat.py`
- Modify: `backend/app/store/chat.py`
- Create: `backend/tests/test_chat_layout.py`
- Create: `backend/tests/test_chat_plan.py`
- Modify: `backend/tests/test_chat_transient.py`

**Interfaces:**
- Consumes: strict mutating actions, visible board/card snapshots, board revisions, layer fingerprint.
- Produces: `resolve_layout`, `PlanBasis`, `ResolvedPlan`, exact `PendingPlanView`, and `plan_action` for `run_turn`.

- [ ] **Step 1: Write failing layout-clamp tests**

Cover negative/off-grid x/y, width above 12, width below 3, height below 6/above 30, direct collision, nearest left/right/up openings, directional tie-break, no bounded opening (append below), deterministic order, one-to-one swap, and unchanged existing layouts outside the affected set.

- [ ] **Step 2: Write failing plan-validation tests**

Cover every mutation variant, one-to-six card count, current-board requirement for data-dependent actions, allowed inactive structural actions, exact semantic diff for `edit_card`, final-dashboard delete refusal, one-pending-plan conflict, board/order revision basis, layer fingerprint, authoritative mini-grid preview, and zero store mutations before confirmation.

- [ ] **Step 3: Run the tests and establish failures**

Run: `docker compose exec -T backend pytest tests/test_chat_layout.py tests/test_chat_plan.py -v`

Expected: FAIL importing `app.chat.layout` and `app.chat.plan`.

- [ ] **Step 4: Implement shared layout values and clamping**

Define:

```python
COLS = 12
MIN_W = 3
MIN_H = 6
MAX_H = 30
DEFAULT_W = 6
DEFAULT_H = 10

class GridLayout(StrictModel):
    x: int
    y: int
    w: int
    h: int

def clamp_layout(raw: LayoutRequest | None, *, fallback: GridLayout) -> GridLayout: ...
def overlaps(a: GridLayout, b: GridLayout) -> bool: ...
def resolve_layout(*, fixed: dict[str, GridLayout],
                   requested: list[tuple[str, GridLayout]],
                   drag_vectors: dict[str, tuple[int, int]] | None = None
                   ) -> dict[str, GridLayout]: ...
```

Enumerate candidate cells across the current bounded board plus one card height. Score by Manhattan displacement, direction penalty, alignment, vertical displacement, y, then x. Move only requested items and their explicitly displaced occupants; preserve unrelated fixed cards.

- [ ] **Step 5: Compute stable plan bases**

Define:

```python
class PlanBasis(StrictModel):
    board_revisions: dict[UUID, int]
    board_order_sha256: str
    layer_sha256: str

def current_basis(board_ids: set[UUID], layer: Layer) -> PlanBasis: ...
```

Hash canonical JSON with sorted keys and compact separators. The layer hash must come from declared semantic entities/fields, not filesystem timestamps. Board-order hash includes visible IDs, positions, and titles.

- [ ] **Step 6: Resolve every mutation to effect descriptors and preview rows**

Create a strict effect union in `plan.py` containing `create_board`, `create_card_request`, `replace_card_query`, `save_layout`, `rename_board`, `reorder_boards`, `soft_delete_card`, and `soft_delete_board`. `ResolvedPlan` contains those effects, `PlanBasis`, affected board IDs, operation copy, and resolved layouts.

For `edit_card`, call `validate_query`, calculate `diff_queries(current, replacement, entity)`, and store the complete replacement query/hint in the effect. For transient “Add as card,” freeze the already validated semantic query instead of converting it back to a question. For generated cards, freeze `CardRequest` plus resolved layout; query generation remains the post-confirm worker operation disclosed in preview.

- [ ] **Step 7: Store one pending plan and return it from `run_turn`**

Implement:

```python
def plan_action(*, thread_id: UUID, action: MutatingAction,
                active_board_id: UUID, layer: Layer) -> PendingPlanView: ...
```

Within one app transaction, cancel no existing plan automatically. Return 409 `pending_plan_exists` until the old plan is confirmed/cancelled. Store full internal action/resolution/basis, but expose only exact operations and mini-grid positions.

Add `POST /chat/transient/{result_id}/add`. It validates the saved semantic query and active target board, then creates the same frozen `create_card` plan with exact query/hint/title and resolved layout. It does not create the card until confirmation. An expired transient may still be added if its semantic query remains valid, but its old rows/cache are never copied.

- [ ] **Step 8: Verify and commit planning**

Run:

```bash
docker compose exec -T backend pytest tests/test_chat_layout.py tests/test_chat_plan.py tests/test_chat_turn.py -v
```

Expected: PASS; fake model call count is exactly one per planning turn.

```bash
git add backend/app/chat backend/app/store/chat.py backend/tests/test_chat_layout.py backend/tests/test_chat_plan.py backend/tests/test_chat_turn.py
git commit -m "feat: freeze validated chat mutation plans"
```

### Task 10: Apply structural plans transactionally and support targeted Undo

**Files:**
- Create: `backend/app/chat/effects.py`
- Modify: `backend/app/routes/chat.py`
- Modify: `backend/app/store/chat.py`
- Modify: `backend/app/store/cards.py`
- Create: `backend/tests/test_chat_effects.py`
- Create: `backend/tests/test_chat_undo.py`
- Modify: `backend/tests/test_chat_routes.py`

**Interfaces:**
- Consumes: frozen `ResolvedPlan` and `PlanBasis` from Task 9.
- Produces: `confirm_plan(plan_id)`, `cancel_plan(plan_id)`, `undo_action(action_id)`, atomic effects journals, and confirmation/cancel/Undo routes.

- [ ] **Step 1: Write failing stale/atomicity tests**

Assert confirmation returns 409 after card query edit, layout move, board rename/order change, membership change, or layer fingerprint change. Inject a failure into the second of three effects and assert no first effect persists. Assert confirmation invokes no `LLMClient` method.

- [ ] **Step 2: Write failing deletion/Undo tests**

Cover soft-delete/restore card, active board delete selecting nearest neighbor in response metadata, last-board refusal, hard direct-menu deletion remaining unrestorable, action Undo after switching dashboards, exact layout/query/title restoration, double-Undo 409, and current-active-board “latest action” lookup.

- [ ] **Step 3: Run tests to establish failures**

Run: `docker compose exec -T backend pytest tests/test_chat_effects.py tests/test_chat_undo.py tests/test_chat_routes.py -v`

Expected: FAIL importing `app.chat.effects` and missing routes.

- [ ] **Step 4: Implement atomic plan confirmation**

Define:

```python
@dataclass(frozen=True)
class AppliedAction:
    action_id: UUID
    board_id: UUID | None
    effects: dict
    navigate_board_id: UUID | None

def confirm_plan(plan_id: UUID, *, layer: Layer,
                 provider: str, model: str) -> AppliedAction: ...
def cancel_plan(plan_id: UUID) -> None: ...
```

Open one `app_pool.connection()` and one transaction, lock the plan row `FOR UPDATE`, require status `pending`, recompute `PlanBasis`, apply all non-generation effects through cursor-aware store helpers, save before/after effect snapshots, create `chat_action`, and transition the plan to `confirmed`. Any exception rolls back everything.

- [ ] **Step 5: Apply exact card replacement without a model call**

Validate the frozen replacement again, render it before the app transaction so a warehouse failure changes nothing, then persist `semantic_query`, `chart_hint`, title, cache, state, and `vega_spec` inside the transaction while setting the existing per-card `previous` snapshot. Store the same previous snapshot in the chat effect journal for turn-level Undo.

- [ ] **Step 6: Implement reversible chat deletion and effect inversion**

`undo_action` locks the action, requires `status='completed'`, and applies inverse effects in reverse order. Restoring a soft-deleted dashboard also restores its chat-deleted cards; it must not recreate records that a later direct hard delete removed—in that conflict return 409 with no partial restore. Undo of a creation hard-deletes only records created by that action. Set `status='undone'` in the same transaction.

- [ ] **Step 7: Add confirmation, cancellation, and Undo routes**

Implement:

```text
POST /chat/plans/{plan_id}/confirm
POST /chat/plans/{plan_id}/cancel
POST /chat/actions/{action_id}/undo
POST /chat/boards/{board_id}/undo-latest
```

Return the updated affected board summaries/cards and `navigate_board_id`; do not make the frontend guess which tab survived deletion.

- [ ] **Step 8: Verify and commit structural mutation support**

Run:

```bash
docker compose exec -T backend pytest tests/test_chat_effects.py tests/test_chat_undo.py tests/test_chat_routes.py tests/test_boards.py tests/test_undo.py -v
```

Expected: PASS; direct and chat deletion retain their distinct semantics.

```bash
git add backend/app/chat/effects.py backend/app/routes/chat.py backend/app/store backend/tests
git commit -m "feat: confirm and undo chat dashboard actions"
```

### Task 11: Add exact previews, confirmation, source navigation, and action Undo to the UI

**Files:**
- Create: `frontend/src/components/ChatPlanPreview.tsx`
- Create: `frontend/src/components/ChatPlanPreview.test.tsx`
- Modify: `frontend/src/api/chat.ts`
- Modify: `frontend/src/components/ChatPanel.tsx`
- Modify: `frontend/src/components/ChatMessage.tsx`
- Modify: `frontend/src/components/ChatResult.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Board.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/components/ChatPanel.test.tsx`
- Test: `frontend/src/components/Board.test.tsx`

**Interfaces:**
- Consumes: `PendingPlanView`, confirm/cancel/Undo endpoints, `navigate_board_id`.
- Produces: authoritative mini-grid preview, accessible confirmation controls, stale-plan recovery, app-level source navigation/highlight, action-bound Undo.

- [ ] **Step 1: Write failing preview and confirmation tests**

Assert the preview shows every create/edit/move/rename/delete operation and resolved mini-grid coordinate; no API mutation occurs before Confirm; Confirm is single-submit disabled while busy; Cancel removes the pending plan; 409 stale shows `Regenerate plan`; and another mutating prompt is disabled while one plan remains pending.

- [ ] **Step 2: Write failing navigation/Undo tests**

Assert a source chip switches tabs, waits for board hydration, scrolls to the card, focuses it, and applies a temporary highlight. Assert an old message’s Undo calls its own `action_id` after multiple tab switches, while masthead Undo calls the active dashboard’s latest action.

- [ ] **Step 3: Run tests and establish failures**

Run:

```bash
cd frontend
npm test -- src/components/ChatPlanPreview.test.tsx src/components/ChatPanel.test.tsx src/components/Board.test.tsx
```

Expected: FAIL because preview/action navigation does not exist.

- [ ] **Step 4: Implement typed action API calls**

Add:

```ts
confirmPlan(planId: string): Promise<AppliedActionView>;
cancelPlan(planId: string): Promise<void>;
undoAction(actionId: string): Promise<AppliedActionView>;
undoLatest(boardId: string): Promise<AppliedActionView>;
addTransient(resultId: string, boardId: string): Promise<PendingPlanView>;
```

Preserve backend error codes/body so the UI distinguishes `stale_plan`, `pending_plan_exists`, and ordinary transport failure.

- [ ] **Step 5: Render the exact operation list and mini-grid**

Use a 12-column CSS grid scaled to a fixed preview width. Position cards from resolved integer layouts; labels remain readable through truncation plus `title`. Mark created, moved/resized, and deleted cards with shape/outline differences rather than color alone. Add `aria-describedby` text listing the same exact operations for nonvisual users.

- [ ] **Step 6: Apply confirmed responses through `App` callbacks**

Centralize `reloadBoards`, `select`, and a `{boardId, cardId, nonce}` navigation target in `App`. `Board` consumes the target after cards hydrate, calls `scrollIntoView`, focuses the article, and clears the target through a callback. Do not use `document.querySelector` from `ChatMessage`.

Wire `ChatResult`’s `Add as card` button to `addTransient`; it must open the same confirmation preview instead of creating a card immediately.

- [ ] **Step 7: Keep Undo bound to the completed action**

Store `action_id`, status, and affected board label in the assistant message. Disable only that button during the request. After success, mark it undone and reload the returned affected board; never reinterpret it as “latest global action.”

- [ ] **Step 8: Verify and commit confirmed-mutation UI**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: PASS; preview and application layouts match exactly.

```bash
git add frontend/src
git commit -m "feat: preview confirm and undo chat actions"
```

---

## Phase 5: Resumable Dashboard Generation

**Documentation references:** FastAPI `StreamingResponse` and WHATWG SSE/EventSource links in Phase 0; existing `query_step.ask()` retry/validation discipline; Python `asyncio.to_thread` and `Semaphore` from the Python 3.12 standard library.

**Phase verification:** confirmation creates stable placeholders; no more than two LLM card calls run at once; results stream and replay in order; collapsing chat does not cancel; Stop, reload, restart, retry, removal, partial success, and turn Undo behave predictably.

**Anti-pattern guards:** no worker lifecycle tied to one HTTP connection, no unpersisted event as the only record of progress, no third concurrent call, no automatic provider fallback, no re-planning a failed item.

### Task 12: Run persisted generation jobs and expose replayable SSE

**Files:**
- Create: `backend/app/chat/generate.py`
- Modify: `backend/app/chat/effects.py`
- Modify: `backend/app/routes/chat.py`
- Modify: `backend/app/store/chat.py`
- Modify: `backend/app/routes/boards.py`
- Modify: `backend/app/routes/cards.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_chat_generation.py`
- Create: `backend/tests/test_chat_sse.py`
- Modify: `backend/tests/test_chat_undo.py`

**Interfaces:**
- Consumes: confirmed `create_card_request` effects, frozen card requests/provider/model, chat action/item/event stores.
- Produces: `GenerationManager`, GET event stream, Stop/retry/remove endpoints, persisted placeholder status, generation-level Undo effects.

- [ ] **Step 1: Write failing concurrency and partial-success tests**

Use a blocking fake client that records active calls. Confirm one six-card plan and assert maximum observed concurrency equals 2, all card rows/placeholders are created before the first result completes, successful siblings persist when another refuses/fails, and final action status becomes `completed_with_errors`.

- [ ] **Step 2: Write failing Stop/restart/retry tests**

Assert closing an event subscriber changes no action status; Stop prevents queued items from starting, marks unfinished items cancelled, and preserves completed cards; a simulated manager restart resumes queued/running items; retry reuses the exact frozen request/provider/model; remove requires an explicit confirmation flag and deletes only the failed empty placeholder.

- [ ] **Step 3: Write failing SSE replay tests**

Assert headers contain `text/event-stream`, `Cache-Control: no-cache`, and `X-Accel-Buffering: no`; event frames contain increasing `id`, explicit `event`, one JSON `data` payload, and a blank terminator; `Last-Event-ID` excludes already seen events; a heartbeat comment appears after 15 seconds of inactivity; terminal streams end after `done`/`stopped` is delivered.

- [ ] **Step 4: Run tests and establish failures**

Run:

```bash
docker compose exec -T backend pytest tests/test_chat_generation.py tests/test_chat_sse.py tests/test_chat_undo.py -v
```

Expected: FAIL importing `app.chat.generate` and missing stream routes.

- [ ] **Step 5: Extend confirmation to create stable generation resources**

For `new_dashboard`, atomically create the dashboard, resolved-layout card placeholders, action, ordered action items, initial `plan` event, and creation effect journal; return `navigate_board_id` for the new tab. For `new_cards`, create placeholders on the active board with the same item/action structure. Only after commit call `GenerationManager.start(action_id)`.

- [ ] **Step 6: Implement the two-worker generation manager**

Define:

```python
class GenerationManager:
    def __init__(self, *, client_factory=make_client, max_concurrency: int = 2): ...
    async def resume_incomplete(self) -> None: ...
    async def start(self, action_id: UUID) -> None: ...
    async def stop(self, action_id: UUID) -> None: ...
    async def retry(self, action_id: UUID, item_id: UUID) -> None: ...
    async def close(self) -> None: ...
```

Use one manager-wide `asyncio.Semaphore(2)`. Claim a queued item transactionally before running it. Execute the synchronous provider/query/render work through `asyncio.to_thread`. Catch `LLMRateLimited` before `LLMError`; persist a human-readable item error. Recheck `cancel_requested` before launch and after the blocking call; a result returning after Stop is discarded rather than written to the card.

- [ ] **Step 7: Persist each state transition and event**

For each item, store `running`, then either persist the rendered card and `card` event, or store `failed` and `item_failed`. When no queued/running items remain, set action `completed`, `completed_with_errors`, or `stopped`, and append exactly one terminal event. Store no provider reasoning text.

- [ ] **Step 8: Resume incomplete work in the FastAPI lifespan**

Create the manager after pools open, assign it to `app.state.generation`, call `await resume_incomplete()`, and on shutdown call `await close()` before closing pools. Avoid a module-global task registry that survives neither test isolation nor lifespan ownership.

- [ ] **Step 9: Implement the documented SSE generator**

Add:

```python
async def stream_events(action_id: UUID, after_id: int) -> AsyncIterator[bytes]:
    # Fetch persisted events after `after_id`, yield SSE frames in order,
    # await between empty polls, emit `: keep-alive\n\n` every 15 seconds,
    # and exit only after the terminal event has been emitted.
```

Route:

```text
GET  /chat/actions/{action_id}/events
POST /chat/actions/{action_id}/stop
POST /chat/actions/{action_id}/items/{item_id}/retry
POST /chat/actions/{action_id}/items/{item_id}/remove
```

Read the standard `Last-Event-ID` header as an integer; reject invalid/negative values with 400. `remove` requires body `{confirm: true}` and only accepts failed/cancelled items whose card still has no semantic query.

- [ ] **Step 10: Expose persisted placeholder status on card reads**

When a card has an associated action item, add a generated `generation` object `{action_id,item_id,status,error}` to card/board responses. Do not add generation status columns to `app.card`; `chat_action_item` remains its source of truth. Remove the object after the item succeeds.

- [ ] **Step 11: Verify generation and Undo**

Run:

```bash
docker compose exec -T backend pytest tests/test_chat_generation.py tests/test_chat_sse.py tests/test_chat_undo.py tests/test_chat_routes.py -v
```

Expected: PASS; Undo of the generation hard-deletes only the board/cards created by that action and returns the original dashboard ID for navigation.

- [ ] **Step 12: Commit backend generation**

```bash
git add backend/app backend/tests
git commit -m "feat: stream resumable dashboard generation"
```

### Task 13: Stream generation progress and manage placeholders in the UI

**Files:**
- Create: `frontend/src/hooks/useActionEvents.ts`
- Create: `frontend/src/hooks/useActionEvents.test.tsx`
- Create: `frontend/src/components/GenerationProgress.tsx`
- Create: `frontend/src/components/GenerationProgress.test.tsx`
- Modify: `frontend/src/api/chat.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/ChatPanel.tsx`
- Modify: `frontend/src/components/ChatMessage.tsx`
- Modify: `frontend/src/components/Board.tsx`
- Modify: `frontend/src/components/Card.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/components/ChatPanel.test.tsx`

**Interfaces:**
- Consumes: persisted `ChatEvent` stream and `generation` card metadata.
- Produces: resilient EventSource subscription, live per-card state, Stop, deterministic retry, confirmed remove, and reload recovery.

- [ ] **Step 1: Write failing EventSource lifecycle tests**

Stub `EventSource`. Assert one connection per running action, event IDs de-duplicate updates, network error leaves persisted UI status intact, remount reconnects and accepts replay, terminal events close the source, component unmount closes it, and merely collapsing the still-mounted drawer does not.

- [ ] **Step 2: Write failing generation UX tests**

Assert the plan appears before results, six stable placeholders do not jump as cards finish, Stop preserves successful cards, failures show retry/remove, retry does not expose provider choice, remove asks inline confirmation, partial completion is labeled honestly, and generation Undo removes the whole created tab/action.

- [ ] **Step 3: Run tests to establish failures**

Run:

```bash
cd frontend
npm test -- src/hooks/useActionEvents.test.tsx src/components/GenerationProgress.test.tsx src/components/ChatPanel.test.tsx
```

Expected: FAIL because hooks/components are absent.

- [ ] **Step 4: Implement the EventSource hook with symmetrical cleanup**

Define:

```ts
export function useActionEvents(args: {
  actionId: string | null;
  terminal: boolean;
  onEvent(event: ChatEvent): void;
  onConnectionChange(connected: boolean): void;
}): void;
```

Create the source only for nonterminal action IDs, register explicit handlers for every event kind, validate parsed payloads by their generated discriminant, ignore IDs at or below the latest seen, and return cleanup that removes listeners and closes the source. Do not close based on drawer visibility.

- [ ] **Step 5: Render stable progress and persisted placeholder states**

`GenerationProgress` orders items by `ordinal`; status changes update labels/icons without reordering. `Card` renders queued/running/failed/cancelled presentation before its empty Ask form, with retry/remove actions linked to the action item. Keep failed card space in the grid until removal is confirmed.

- [ ] **Step 6: Wire Stop, retry, remove, and terminal reloads**

Add typed client methods. After every `card`, `item_failed`, `stopped`, or `done` event, coalesce board reloads through one scheduled callback so a burst does not refetch once per event. Stop disables after one click. Retry retains the item/card IDs. Removal requires the inline confirmation state before sending `{confirm:true}`.

- [ ] **Step 7: Verify and commit generation UI**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: PASS; no duplicate EventSource remains after unmount/reconnect tests.

```bash
git add frontend/src
git commit -m "feat: show resilient dashboard generation progress"
```

---

## Phase 6: Evaluation, Documentation, and Release Verification

**Documentation references:** existing eval runner at `backend/eval/run_eval.py`; provider configuration at `backend/app/config.py`; updated design spec.

**Phase verification:** chat behavior is scored independently from semantic query quality; documentation states exact data boundaries and exports; all automated/manual checks pass; chat can be enabled deliberately without paid-provider surprises.

**Anti-pattern guards:** no live API in unit tests, no automatic Anthropic/OpenAI eval fallback, no weakening old prompt/grant/confidence invariants to make new tests pass, no undocumented environment variable.

### Task 14: Add the free-provider chat eval suite and complete product documentation

**Files:**
- Create: `backend/eval/fixtures_chat.yaml`
- Modify: `backend/eval/run_eval.py:49-70`
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Modify: `Makefile`
- Modify: `docs/design.md`
- Create: `backend/tests/test_chat_eval_fixtures.py`
- Modify: `backend/tests/test_eval_metrics.py`

**Interfaces:**
- Consumes: fake/unit-tested chat pipeline and explicit live provider selection.
- Produces: `chat` eval suite with action/query/refusal/count metrics and `make eval-chat` free-provider command.

- [ ] **Step 1: Write fixture-shape tests before fixtures**

Require every fixture to have unique `id`, `utterance`, `active_board`, deterministic `board_state`, `expected_action`, and optional `expected_query`, `expected_card_count`, `expected_target`, or `expect_clarification`. Validate expected semantic queries with `SemanticQuery.model_validate`.

- [ ] **Step 2: Add at least 25 chat fixtures**

Cover:

- visible-value and structural answers;
- data-sharing-off refusal;
- ad-hoc query and Add as card;
- one/multiple cards on the active board;
- one-to-six-card new dashboard;
- exact card edit;
- model layout, rename, reorder, card delete, dashboard delete;
- inactive structural action and forbidden inactive data action;
- last-dashboard delete refusal;
- ambiguous post-switch reference clarification;
- out-of-grammar metric refusal;
- invented numeric claim rejection;
- context-summary disclosure.

- [ ] **Step 3: Run fixture tests and confirm runner lacks the suite**

Run: `docker compose exec -T backend pytest tests/test_chat_eval_fixtures.py tests/test_eval_metrics.py -v`

Expected: fixture validation may pass, but metric tests FAIL because `chat` is absent from `SUITES`.

- [ ] **Step 4: Add independent chat scoring**

Extend the runner with `ChatTally` fields:

```python
total: int
action_correct: int
query_correct: int
query_total: int
target_correct: int
target_total: int
card_count_correct: int
card_count_total: int
clarification_correct: int
refusal_correct: int
retries: int
failures: list[str]
```

Never apply mutation effects during eval. Score the model action and frozen resolved plan against fixture state using an in-memory/fake store boundary.

- [ ] **Step 5: Make free live-provider selection explicit**

For `--suite chat` with no `--provider`, choose configured NVIDIA/DeepSeek first, then configured Gemini. If neither key exists, exit nonzero with: `Configure NVIDIA_API_KEY or GOOGLE_API_KEY, or pass --provider explicitly.` Never choose Anthropic/OpenAI implicitly. Keep explicit `--provider anthropic|openai` available for an operator who intentionally requests it.

Add:

```make
eval-chat:
	docker compose exec -T backend python -m eval.run_eval --suite chat
```

- [ ] **Step 6: Update configuration and architecture documentation**

Document every chat environment variable, the two consent gates, browser-global history, active-only detailed rows, context ceilings, transcript/transient/tombstone retention, exact action union, confirmation/Undo flow, SSE endpoints, free eval behavior, and export JSON version 1. Replace the masthead’s absolute “no row data leaves the warehouse” copy with path-specific truthful copy: query translation sees no rows; chat sees visible rows only after consent.

- [ ] **Step 7: Verify and commit eval/docs**

Run:

```bash
docker compose exec -T backend pytest tests/test_chat_eval_fixtures.py tests/test_eval_metrics.py -v
docker compose exec -T backend python -m eval.run_eval --suite chat --help
```

Expected: tests PASS; help lists the suite/provider policy without making a live call.

```bash
git add backend/eval backend/app/config.py backend/tests Makefile .env.example docs/design.md
git commit -m "test: add free-provider chat evaluation"
```

### Task 15: Run full verification and release the feature flag deliberately

**Files:**
- Modify only if verification exposes a defect: files owned by the failing task.
- Review: `docs/superpowers/specs/2026-08-20-global-chat-dashboard-capabilities-design.md`
- Review: `docs/design.md`

**Interfaces:**
- Consumes: every prior task.
- Produces: a verified branch whose default deployment still has chat disabled and whose opt-in deployment is documented.

- [ ] **Step 1: Verify migration idempotence on the existing volume**

Run:

```bash
docker compose restart backend
docker compose restart backend
docker compose logs --tail=100 backend
```

Expected: first needed migration applies once; second startup reports no pending migration or checksum error.

- [ ] **Step 2: Run the entire backend suite**

Run: `docker compose exec -T backend pytest -v`

Expected: all existing and new tests PASS; no skips are introduced for prompt, grants, confidence gate, or refinement history invariants.

- [ ] **Step 3: Regenerate types and require a clean result**

Run:

```bash
make types
git diff --exit-code -- frontend/src/api/schema.json frontend/src/api/types.gen.ts
```

Expected: generated files are already current.

- [ ] **Step 4: Run complete frontend verification**

Run:

```bash
docker compose exec -T frontend npm test
docker compose exec -T frontend npm run build
```

Expected: all Vitest suites PASS and production build completes.

- [ ] **Step 5: Run static anti-pattern checks**

Run:

```bash
rg -n "CHAT_ENABLED|CHAT_SEES_DATA|CHAT_MAX_ROWS|CHAT_MAX_CONTEXT_CHARS|CHAT_HISTORY_TURNS" .env.example backend/app/config.py docs/design.md
rg -n "pg_advisory_lock\(" backend/app
rg -n "EventSource|Last-Event-ID|text/event-stream" frontend/src backend/app
rg -n "anthropic|openai" backend/eval/fixtures_chat.yaml
git diff --check
```

Expected: configuration is documented in all three places; migration code uses `pg_advisory_xact_lock` rather than session lock; SSE names occur in the intended files; chat fixtures contain no paid-provider dependency; diff check is clean.

- [ ] **Step 6: Perform privacy database inspection**

With chat enabled in a disposable local environment, send one data-on answer containing a unique row value. Query `app.chat_message`, `app.chat_plan`, and `app.chat_action` and assert that unique value is absent. Confirm it exists only in the source card cache or unexpired `app.chat_transient_result`, then clear/expire the transient and verify removal.

- [ ] **Step 7: Perform the desktop acceptance pass**

Verify manually:

1. Reorder three tabs by pointer and keyboard, reload, and confirm order.
2. Export card PNG/CSV and board PNG/JSON from dedicated right-side menus.
3. Open, resize, pin, unpin, close, and shortcut-toggle chat without changing saved card coordinates.
4. Ask a value question with each consent gate off, then both on; verify source-chip navigation.
5. Run a transient query, inspect SQL, let it expire, and use explicit Run again.
6. Preview/cancel one mutation; preview/confirm another; mutate the board separately and confirm a stale plan refuses.
7. Delete a nonfinal dashboard through chat, switch tabs, and Undo from its original message.
8. Generate six cards; observe no more than two active workers, close/reopen chat, Stop, retry one failed card, and Undo the whole generation.
9. Reload during generation and restart the backend; confirm persisted progress resumes.
10. Confirm all UI remains readable in the light theme at narrow, half-width, and full-width cards.

- [ ] **Step 8: Run a free live smoke eval only when configured**

Run: `make eval-chat`

Expected: it selects NVIDIA/DeepSeek or Gemini and writes the chat metrics. If neither free credential exists, the command stops with configuration instructions; do not substitute a paid provider.

- [ ] **Step 9: Review the final diff and commit verification fixes**

Run:

```bash
git status --short
git diff --stat
git diff --check
```

If verification required code changes, commit each fix with its owning task’s tests. Then create the final documentation commit:

```bash
git add docs/superpowers docs/design.md
git commit -m "docs: record global chat dashboard architecture"
```

The implementation is complete only when every automated command above passes and the manual acceptance pass has no unresolved trust, layout, consent, or recovery defect.
