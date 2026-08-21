/** Browser-local preferences.
 *
 *  Every getter has to survive storage being unavailable: private windows
 *  and blocked-cookie contexts throw on access rather than returning null,
 *  and a dashboard that will not load because it could not remember a
 *  drawer width is a worse failure than forgetting the width.
 *
 *  Values are validated on the way out, not trusted. Anything in
 *  localStorage was last written by some previous version of this app, or
 *  by hand.
 */

import type { Provider } from "../api/client";

const KEY = {
  thread: "semantic-dashboard:chat-thread",
  provider: "semantic-dashboard:provider",
  open: "semantic-dashboard:chat-open",
  width: "semantic-dashboard:chat-width",
  pinned: "semantic-dashboard:chat-pinned",
  shareData: "semantic-dashboard:chat-share-data",
} as const;

export const MIN_WIDTH = 320;
export const MAX_WIDTH = 640;
export const DEFAULT_WIDTH = 400;

const PROVIDERS: Provider[] = ["anthropic", "gemini", "openai", "nvidia"];

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Nothing to do and nothing worth telling the user about.
  }
}

function clear(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // As above.
  }
}

function readBool(key: string, fallback: boolean): boolean {
  const raw = read(key);
  return raw === null ? fallback : raw === "true";
}

export const prefs = {
  threadId: (): string | null => read(KEY.thread),
  setThreadId: (id: string | null) =>
    id ? write(KEY.thread, id) : clear(KEY.thread),

  provider: (fallback: Provider): Provider => {
    const raw = read(KEY.provider);
    return PROVIDERS.includes(raw as Provider) ? (raw as Provider) : fallback;
  },
  setProvider: (p: Provider) => write(KEY.provider, p),

  // Closed by default. A chat that opens itself on first load has decided
  // for the user that this is a chat application.
  open: (): boolean => readBool(KEY.open, false),
  setOpen: (v: boolean) => write(KEY.open, String(v)),

  pinned: (): boolean => readBool(KEY.pinned, false),
  setPinned: (v: boolean) => write(KEY.pinned, String(v)),

  // Consent is off until it is given, on this browser, every time it is
  // asked for. A remembered "yes" from a previous session is still a yes
  // the person gave.
  shareData: (): boolean => readBool(KEY.shareData, false),
  setShareData: (v: boolean) => write(KEY.shareData, String(v)),

  width: (): number => {
    const parsed = Number(read(KEY.width));
    if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_WIDTH;
    return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(parsed)));
  },
  setWidth: (px: number) => {
    const clamped = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(px)));
    write(KEY.width, String(clamped));
  },
};
