import { useCallback, useEffect, useRef, useState } from "react";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "./styles.css";
import Board from "./components/Board";
import ChatPanel from "./components/ChatPanel";
import TabBar from "./components/TabBar";
import type { BoardSummary, ChatGates, Provider, Providers } from "./api/client";
import { api } from "./api/client";
import { chatApi } from "./api/chat";
import { prefs } from "./state/preferences";

// Which tab you were last on is view state, not data. It belongs to this
// browser, not to the boards everyone shares.
const LAST_BOARD = "semantic-dashboard:last-board";

export default function App() {
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [boardId, setBoardId] = useState<string | null>(null);
  const [examples, setExamples] = useState<string[]>([]);
  const [providers, setProviders] = useState<Providers>({
    default: "anthropic",
    available: [],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // One provider for the whole session, remembered. Asked once per blank
  // card it was the same question four times on a four-card board.
  const [provider, setProviderState] = useState<Provider>("anthropic");
  const [gates, setGates] = useState<ChatGates>({
    enabled: false, data_sharing_permitted: false,
  });
  const [threadId, setThreadId] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [pinned, setPinned] = useState(prefs.pinned());
  const [chatWidth, setChatWidth] = useState(prefs.width());
  const [shareData, setShareData] = useState(prefs.shareData());
  // React state does not update synchronously enough to be a lock. This ref
  // prevents a second board mutation entering before the busy render lands.
  const mutationInFlight = useRef(false);

  const select = useCallback((id: string) => {
    setBoardId(id);
    try {
      localStorage.setItem(LAST_BOARD, id);
    } catch {
      // Private browsing and storage-blocked contexts: the tab still works,
      // it just will not be remembered.
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [listed, layer] = await Promise.all([api.listBoards(), api.layer()]);
        setExamples(layer.examples);
        setProviders(layer.providers);
        setGates(layer.chat ?? { enabled: false, data_sharing_permitted: false });
        setProviderState(prefs.provider(layer.providers.default));

        const known = listed.length ? listed : [await api.createBoard("Operations")];
        setBoards(known);

        let remembered: string | null = null;
        try {
          remembered = localStorage.getItem(LAST_BOARD);
        } catch {
          remembered = null;
        }
        // A remembered board may since have been deleted, so fall back
        // rather than showing an empty screen.
        const opening = known.find((b) => b.id === remembered) ?? known[0];
        select(opening.id);

        if (layer.chat?.enabled) {
          // The thread outlives the tab and the reload. A remembered one
          // that the server no longer has is replaced rather than surfaced
          // as an error nobody can act on.
          const remembered_thread = prefs.threadId();
          let id = remembered_thread;
          if (id) {
            try {
              await chatApi.getThread(id);
            } catch {
              id = null;
            }
          }
          if (!id) id = (await chatApi.createThread()).id;
          prefs.setThreadId(id);
          setThreadId(id);
          setChatOpen(prefs.open());
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [select]);

  const setProvider = (p: Provider) => {
    setProviderState(p);
    prefs.setProvider(p);
  };

  const toggleChat = useCallback(() => {
    setChatOpen((wasOpen) => {
      prefs.setOpen(!wasOpen);
      return !wasOpen;
    });
  }, []);

  // Documented on the button's title so the shortcut is discoverable
  // rather than folklore.
  useEffect(() => {
    if (!gates.enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.shiftKey && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a") {
        e.preventDefault();
        toggleChat();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [gates.enabled, toggleChat]);

  const guard = async (work: () => Promise<void>) => {
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  };

  const create = () =>
    guard(async () => {
      const board = await api.createBoard("New dashboard");
      setBoards((prev) => [...prev, board]);
      select(board.id);
    });

  const rename = (id: string, title: string) =>
    guard(async () => {
      const updated = await api.updateBoard(id, { title });
      setBoards((prev) => prev.map((b) => (b.id === id ? updated : b)));
    });

  const remove = (id: string) =>
    guard(async () => {
      const board = boards.find((b) => b.id === id);
      const cards = board ? await api.getBoard(id) : null;
      const count = cards?.cards.length ?? 0;
      const warning = count
        ? `Delete "${board?.title}" and its ${count} card${count === 1 ? "" : "s"}? This cannot be undone.`
        : `Delete "${board?.title}"?`;
      if (!window.confirm(warning)) return;

      await api.deleteBoard(id);
      const left = boards.filter((b) => b.id !== id);
      setBoards(left);
      if (boardId === id && left.length) select(left[0].id);
    });

  const reorder = (order: string[]) =>
    guard(async () => {
      const before = boards;
      const previousPositions = new Map(before.map((board) => [board.id, board.position]));
      const next = order.map((id, position) => ({
        ...before.find((board) => board.id === id)!,
        position,
      }));
      setBoards(next);
      try {
        await api.reorderBoards(order);
      } catch (reorderError) {
        // Revert only ordering. Replacing the whole snapshot could erase a
        // title or other board field refreshed while the request was open.
        setBoards((current) => current
          .map((board) => ({
            ...board,
            position: previousPositions.get(board.id) ?? board.position,
          }))
          .sort((left, right) => left.position - right.position));
        throw reorderError;
      }
    });

  return (
    <>
      <header className="masthead">
        <div id="board-primary-action" className="masthead-action" />
        <h1>Semantic Dashboard</h1>
        <span className="eyebrow">grounded in a curated layer · no row data leaves the warehouse</span>
        <span className="spacer" />
        <div id="board-export-action" className="masthead-action" />
        {gates.enabled && (
          <button
            type="button"
            className="chat-toggle"
            aria-pressed={chatOpen}
            title="Show or hide the assistant (Ctrl/Cmd + Shift + A)"
            onClick={toggleChat}
          >
            Chat
          </button>
        )}
      </header>
      <TabBar
        boards={boards}
        activeId={boardId}
        busy={busy}
        onSelect={select}
        onCreate={create}
        onRename={rename}
        onDelete={remove}
        onReorder={reorder}
      />
      {error && <p className="notice broken" style={{ margin: "1rem 1.25rem" }}>{error}</p>}
      {/* Pinning narrows the workspace with CSS only. It must never reach
          saveLayout: a drawer is this browser's furniture, not the board's. */}
      <div
        className="workspace"
        style={chatOpen && pinned ? { marginRight: chatWidth } : undefined}
      >
        {boardId && (
          <Board
            key={boardId}
            boardId={boardId}
            examples={examples}
            provider={provider}
            strongAvailable={
              providers.capabilities?.[provider]?.strong_available ?? true
            }
          />
        )}
      </div>
      {gates.enabled && (
        <ChatPanel
          threadId={threadId}
          open={chatOpen}
          pinned={pinned}
          width={chatWidth}
          activeBoardId={boardId}
          activeBoardTitle={boards.find((b) => b.id === boardId)?.title ?? ""}
          boards={boards}
          provider={provider}
          providers={providers.available}
          capabilities={providers.capabilities ?? {}}
          shareVisibleData={shareData}
          dataSharingPermitted={gates.data_sharing_permitted}
          selectedCardId={null}
          onClose={toggleChat}
          onPinnedChange={(v) => { setPinned(v); prefs.setPinned(v); }}
          onWidthChange={(px) => { setChatWidth(px); prefs.setWidth(px); }}
          onProviderChange={setProvider}
          onConsentChange={(v) => { setShareData(v); prefs.setShareData(v); }}
          onThreadChange={(id) => { setThreadId(id); prefs.setThreadId(id); }}
          onNavigate={(targetBoard) => select(targetBoard)}
        />
      )}
    </>
  );
}
