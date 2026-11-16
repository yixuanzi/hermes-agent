// workagent/frontend/src/lib/useAgentChat.ts
import { useCallback, useEffect, useRef, useState } from "react";
import { AgentRpc } from "./agent-rpc";
import { getStoredToken } from "./auth";

// Legacy keys (used during migration only)
export const WIDGET_SESSION_KEY = "workagent.widget.sessionId";
const WIDGET_DB_SESSION_KEY = "workagent.widget.dbSessionId";
const WIDGET_MESSAGES_KEY = "workagent.widget.messages";

// Multi-tab keys (default prefix; can be overridden per-consumer via options)
const DEFAULT_TABS_KEY = "workagent.widget.tabs";
const DEFAULT_ACTIVE_TAB_KEY = "workagent.widget.activeTabDbId";

/** Config for {@link useAgentChat}. All optional — omitting yields the
 * FloatingChat behavior (multi-tab widget backed by ``workagent.widget.*``
 * localStorage keys, no ``session.create`` overrides). Consumers that want a
 * single fixed-purpose session (e.g. Ontology Chat) should pass:
 *
 *   {
 *     storagePrefix: "workagent.ontology-chat",   // isolate localStorage
 *     sessionCreateParams: { title: "Ontology Consultant", source: "…" },
 *     seedMessages: [{ role: "system", content: SYSTEM_INSTRUCTIONS }],
 *   }
 *
 * A seed ``title`` naturally survives the auto-derive step because the effect
 * only rewrites titles equal to ``"New Chat"``; any non-default seed is left
 * alone. No explicit "freeze" flag is needed. */
export interface UseAgentChatOptions {
  /** Extra params passed verbatim into every ``session.create`` call. Common
   * uses: ``profile`` (bind session to a specific hermes profile), ``title``
   * (stable, non-derived tab title), ``source``. */
  sessionCreateParams?: Record<string, unknown>;
  /** Seed messages inserted into ``session.create.messages``. Roles allowed:
   * ``system`` / ``user`` / ``assistant``. Used to pin a system prompt on
   * fresh sessions. */
  seedMessages?: Array<{ role: "system" | "user" | "assistant"; content: string }>;
  /** Prefix for the two localStorage keys (tabs list + active tab id). Different
   * consumers MUST use different prefixes so their session caches don't collide. */
  storagePrefix?: string;
}

const SCROLLBACK_LIMIT = 200;
const MAX_CACHED_TABS = 20;
const MAX_MESSAGES_PER_TAB = 50;

// ── Types ──────────────────────────────────────────────────────────

export type ChatMessage =
  | { role: "user"; id: string; text: string }
  | { role: "agent"; id: string; text: string; done: boolean }
  | { role: "tool"; id: string; name: string; status: "running" | "done"; context?: string; duration_s?: number; summary?: string };

export type SessionTab = {
  dbId: string;
  tuiId: string | null;
  title: string;
  messages: ChatMessage[];
};

export type ApprovalRequest = {
  request_id: string;
  tool_name?: string;
  command?: string;
};

export type ClarifyRequest = {
  request_id: string;
  question: string;
  choices: string[];
};

export type ChatPhase = "disconnected" | "connecting" | "idle" | "streaming";

export type ChatState = {
  phase: ChatPhase;
  sessionId: string | null;
  messages: ChatMessage[];
  activeApproval: ApprovalRequest | null;
  activeClarify: ClarifyRequest | null;
  error: string | null;
};

// ── Helpers ────────────────────────────────────────────────────────

type AgentMessage = Extract<ChatMessage, { role: "agent" }>;

function isAgentMsg(m: ChatMessage): m is AgentMessage {
  return m.role === "agent";
}

function trimMessages(msgs: ChatMessage[]): ChatMessage[] {
  if (msgs.length <= SCROLLBACK_LIMIT) return msgs;
  return msgs.slice(msgs.length - SCROLLBACK_LIMIT);
}

function hasLocalStorage(): boolean {
  return typeof localStorage !== "undefined";
}

// ── Legacy storage (used by migration) ─────────────────────────────

function loadCachedMessages(): ChatMessage[] {
  try {
    if (!hasLocalStorage()) return [];
    const raw = localStorage.getItem(WIDGET_MESSAGES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function deriveInitialTitle(messages: ChatMessage[]): string {
  const first = messages.find(m => m.role === "user");
  if (!first) return "Chat";
  return first.text.slice(0, 30) + (first.text.length > 30 ? "..." : "");
}

// ── Multi-tab storage ──────────────────────────────────────────────

function loadTabsFromStorage(tabsKey: string): SessionTab[] {
  try {
    if (!hasLocalStorage()) return [];
    const raw = localStorage.getItem(tabsKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function persistTabs(tabsKey: string, tabs: SessionTab[]): void {
  try {
    if (!hasLocalStorage()) return;
    localStorage.setItem(tabsKey, JSON.stringify(tabs));
  } catch {
    // localStorage full or unavailable — silently skip
  }
}

function loadActiveTabDbId(activeKey: string): string | null {
  try {
    if (!hasLocalStorage()) return null;
    return localStorage.getItem(activeKey) || null;
  } catch {
    return null;
  }
}

function persistActiveTabDbId(activeKey: string, dbId: string | null): void {
  try {
    if (!hasLocalStorage()) return;
    if (dbId) localStorage.setItem(activeKey, dbId);
    else localStorage.removeItem(activeKey);
  } catch {
    // silently skip
  }
}

// ── Migration ──────────────────────────────────────────────────────

function migrateLegacyStorage(): void {
  if (!hasLocalStorage()) return;
  if (localStorage.getItem(DEFAULT_TABS_KEY)) return;

  const legacyDbId = localStorage.getItem(WIDGET_DB_SESSION_KEY);
  const legacyMessages = loadCachedMessages();

  if (legacyDbId) {
    const tabs: SessionTab[] = [{
      dbId: legacyDbId,
      tuiId: localStorage.getItem(WIDGET_SESSION_KEY),
      title: deriveInitialTitle(legacyMessages),
      messages: legacyMessages.slice(-MAX_MESSAGES_PER_TAB),
    }];
    localStorage.setItem(DEFAULT_TABS_KEY, JSON.stringify(tabs));
    localStorage.setItem(DEFAULT_ACTIVE_TAB_KEY, legacyDbId);
  }

  localStorage.removeItem(WIDGET_SESSION_KEY);
  localStorage.removeItem(WIDGET_DB_SESSION_KEY);
  localStorage.removeItem(WIDGET_MESSAGES_KEY);
}

// ── Utility ────────────────────────────────────────────────────────

function wsBaseUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}`;
}

function generateChannelId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `widget-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

function buildWsUrl(): string {
  const qs = new URLSearchParams({
    token: getStoredToken(),
    channel: generateChannelId(),
  });
  return `${wsBaseUrl()}/api/chat/ws?${qs.toString()}`;
}

let _msgSeq = 0;
function nextMsgId(): string {
  return `m-${++_msgSeq}`;
}

function getIdleTimeoutMs(): number {
  const raw = import.meta.env.VITE_WORKAGENT_WIDGET_IDLE_MINUTES;
  const mins = Math.max(1, Math.round(Number(raw) || 10));
  return mins * 60_000;
}

/** Helper to format tool duration for display */
export function formatToolDuration(seconds: number | undefined): string {
  if (seconds == null) return "";
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return secs ? `${mins}m ${secs}s` : `${mins}m`;
}

// ── Hook ───────────────────────────────────────────────────────────

export function useAgentChat(options: UseAgentChatOptions = {}): {
  state: ChatState;
  tabs: SessionTab[];
  activeTabDbId: string | null;
  send: (text: string) => void;
  respondApproval: (accept: boolean) => void;
  respondClarify: (choice: string) => void;
  startNewSession: () => void;
  switchToTab: (dbId: string) => void;
  closeTab: (dbId: string) => void;
  connect: () => void;
  disconnect: () => void;
  interrupt: () => void;
} {
  const storagePrefix = options.storagePrefix ?? "workagent.widget";
  const tabsKey = `${storagePrefix}.tabs`;
  const activeKey = `${storagePrefix}.activeTabDbId`;
  const isDefaultPrefix = storagePrefix === "workagent.widget";

  // Extra params/messages come from options; wrap in refs so hook identity is
  // stable even if callers pass a fresh object literal each render.
  const sessionCreateParamsRef = useRef(options.sessionCreateParams);
  sessionCreateParamsRef.current = options.sessionCreateParams;
  const seedMessagesRef = useRef(options.seedMessages);
  seedMessagesRef.current = options.seedMessages;

  const buildCreateParams = useCallback((): Record<string, unknown> => {
    const extra = sessionCreateParamsRef.current || {};
    const seeds = seedMessagesRef.current;
    return {
      cols: 80,
      ...extra,
      ...(seeds && seeds.length > 0 ? { messages: seeds } : {}),
    };
  }, []);

  if (isDefaultPrefix) migrateLegacyStorage();

  const [tabs, setTabs] = useState<SessionTab[]>(() => loadTabsFromStorage(tabsKey));
  const tabsRef = useRef<SessionTab[]>(tabs);
  tabsRef.current = tabs;
  const activeTabDbIdRef = useRef<string | null>(loadActiveTabDbId(activeKey));

  const initialTab = tabs.find(t => t.dbId === activeTabDbIdRef.current);
  const [state, setState] = useState<ChatState>({
    phase: initialTab ? "connecting" : "disconnected",
    sessionId: initialTab?.tuiId ?? null,
    messages: initialTab?.messages ?? [],
    activeApproval: null,
    activeClarify: null,
    error: null,
  });

  const stateRef = useRef(state);
  stateRef.current = state;

  // Snapshot current live state into the matching tab entry
  const snapshotCurrentTab = useCallback((prevTabs: SessionTab[]): SessionTab[] => {
    const dbId = activeTabDbIdRef.current;
    if (!dbId) return prevTabs;
    const msgs = stateRef.current.messages;
    return prevTabs.map(t =>
      t.dbId === dbId
        ? { ...t, messages: msgs.slice(-MAX_MESSAGES_PER_TAB) }
        : t
    );
  }, []);

  // Persist tab state on message changes
  useEffect(() => {
    if (!activeTabDbIdRef.current) return;
    setTabs(prev => {
      const updated = snapshotCurrentTab(prev);
      persistTabs(tabsKey, updated);
      return updated;
    });
  }, [state.messages, snapshotCurrentTab]);

  // Derive tab title from first user message (runs after render, so state is current)
  useEffect(() => {
    const dbId = activeTabDbIdRef.current;
    if (!dbId) return;
    setTabs(prev => {
      const tab = prev.find(t => t.dbId === dbId);
      // Only overwrite the default "New Chat" placeholder. A seed title from
      // sessionCreateParams.title survives untouched.
      if (!tab || tab.title !== "New Chat") return prev;
      const firstUserMsg = state.messages.find(m => m.role === "user");
      if (!firstUserMsg) return prev;
      const newTitle = firstUserMsg.text.slice(0, 30) + (firstUserMsg.text.length > 30 ? "..." : "");
      const updated = prev.map(t => t.dbId === dbId ? { ...t, title: newTitle } : t);
      persistTabs(tabsKey, updated);
      return updated;
    });
  }, [state.messages, tabsKey]);

  const rpcRef = useRef<AgentRpc | null>(null);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unsubsRef = useRef<(() => void)[]>([]);
  const sessionIdRef = useRef<string | null>(null);
  const activeApprovalRef = useRef<ApprovalRequest | null>(null);
  const activeClarifyRef = useRef<ClarifyRequest | null>(null);
  const skipResumeRef = useRef(false);
  const skipResumeMessagesRef = useRef(false);
  // When true, send() must reconnect (resume) before submitting the prompt.
  // Set by switchToTab — cleared after successful resume in connect().
  const needsReconnectRef = useRef(false);

  sessionIdRef.current = state.sessionId;
  activeApprovalRef.current = state.activeApproval;
  activeClarifyRef.current = state.activeClarify;

  const disconnectRef = useRef<() => void>(() => {});
  disconnectRef.current = () => {
    for (const u of unsubsRef.current) u();
    unsubsRef.current = [];
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    rpcRef.current?.disconnect();
    rpcRef.current = null;
    sessionIdRef.current = null;
    setState((s) => ({ ...s, phase: "disconnected" }));
  };

  const resetIdleTimer = useCallback(() => {
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    idleTimerRef.current = setTimeout(() => {
      setState((s) => {
        if (s.phase === "idle") {
          setTimeout(() => disconnectRef.current(), 0);
        }
        return s;
      });
    }, getIdleTimeoutMs());
  }, []);

  const subscribe = useCallback((rpc: AgentRpc) => {
    const unsub = (event: string, handler: (p: any) => void) => {
      const u = rpc.on(event, handler);
      unsubsRef.current.push(u);
      return u;
    };

    unsub("message.start", () => {
      resetIdleTimer();
      const msg: ChatMessage = { role: "agent", id: nextMsgId(), text: "", done: false };
      setState((s) => ({ ...s, phase: "streaming", messages: trimMessages([...s.messages, msg]) }));
    });

    unsub("message.delta", (params) => {
      resetIdleTimer();
      const delta = params.payload?.text || "";
      if (!delta) return;
      setState((s) => {
        const msgs = [...s.messages];
        let idx = -1;
        for (let i = msgs.length - 1; i >= 0; i--) {
          const m = msgs[i];
          if (isAgentMsg(m) && !m.done) { idx = i; break; }
        }
        if (idx >= 0) {
          const prev = msgs[idx] as AgentMessage;
          msgs[idx] = { ...prev, text: prev.text + delta };
        }
        return { ...s, messages: msgs };
      });
    });

    unsub("message.complete", (params) => {
      resetIdleTimer();
      const fullText: string | undefined = params.payload?.text;
      setState((s) => {
        const msgs = [...s.messages];
        let idx = -1;
        for (let i = msgs.length - 1; i >= 0; i--) {
          const m = msgs[i];
          if (isAgentMsg(m) && !m.done) { idx = i; break; }
        }
        if (idx >= 0) {
          const prev = msgs[idx] as AgentMessage;
          msgs[idx] = { ...prev, text: fullText != null ? fullText : prev.text, done: true };
        }
        return { ...s, phase: "idle", messages: msgs };
      });
    });

    unsub("thinking.delta", () => { resetIdleTimer(); });
    unsub("reasoning.delta", () => { resetIdleTimer(); });

    unsub("tool.start", (params) => {
      resetIdleTimer();
      const msg: ChatMessage = {
        role: "tool",
        id: params.payload?.tool_id || nextMsgId(),
        name: params.payload?.name || "tool",
        status: "running",
        context: params.payload?.context || "",
      };
      setState((s) => ({ ...s, messages: trimMessages([...s.messages, msg]) }));
    });

    unsub("tool.complete", (params) => {
      resetIdleTimer();
      const toolId = params.payload?.tool_id;
      const duration_s = params.payload?.duration_s;
      setState((s) => ({
        ...s,
        messages: s.messages.map((m) =>
          m.role === "tool" && m.id === toolId
            ? { ...m, status: "done", summary: params.payload?.summary, duration_s }
            : m,
        ),
      }));
    });

    unsub("approval.request", (params) => {
      resetIdleTimer();
      setState((s) => ({ ...s, activeApproval: params.payload as ApprovalRequest }));
    });

    unsub("clarify.request", (params) => {
      resetIdleTimer();
      setState((s) => ({ ...s, activeClarify: params.payload as ClarifyRequest }));
    });

    unsub("error", (params) => {
      resetIdleTimer();
      const errMsg = params.payload?.message || "Unknown error";
      setState((s) => ({
        ...s,
        phase: "idle",
        messages: trimMessages([...s.messages, { role: "agent", id: nextMsgId(), text: `Error: ${errMsg}`, done: true }]),
      }));
    });
  }, [resetIdleTimer]);

  const persistDbId = async (rpc: AgentRpc, tuiSid: string): Promise<string | null> => {
    try {
      const info = await rpc.call("session.title", { session_id: tuiSid });
      if (info?.session_key) return info.session_key;
    } catch {}
    return null;
  };

  const connect = useCallback(() => {
    setState((s) => ({ ...s, phase: "connecting", error: null }));
    const rpc = new AgentRpc();
    rpcRef.current = rpc;
    const url = buildWsUrl();
    const wantNew = skipResumeRef.current;
    skipResumeRef.current = false;

    const attemptConnect = (retriesLeft: number) => {
      rpc.connect(url).then(async () => {
        subscribe(rpc);

        if (!wantNew) {
          const cachedDbId = activeTabDbIdRef.current;
          if (cachedDbId) {
            try {
              const res = await rpc.call("session.resume", { session_id: cachedDbId, cols: 80 });
              const tuiSid = res.session_id;
              sessionIdRef.current = tuiSid;
              // Update tab with new tuiId
              const resumedDbId = res.resumed || cachedDbId;
              // Snapshot cached messages BEFORE tab update reshapes the tabs array
              const cachedTab = tabsRef.current.find(t => t.dbId === cachedDbId);
              const cachedMsgs = cachedTab?.messages ?? [];
              setTabs(prev => {
                const updated = prev.map(t =>
                  t.dbId === cachedDbId
                    ? { ...t, tuiId: tuiSid, dbId: resumedDbId }
                    : t
                );
                if (resumedDbId !== cachedDbId) {
                  activeTabDbIdRef.current = resumedDbId;
                  persistActiveTabDbId(activeKey, resumedDbId);
                }
                persistTabs(tabsKey, updated);
                return updated;
              });
              const serverMsgs: ChatMessage[] = (res.messages || []).map((m: any) => ({
                role: m.role === "assistant" ? "agent" : m.role,
                id: nextMsgId(),
                text: m.text || "",
                done: true,
              }));
              // Prefer the local cache: it preserves streaming-only artifacts
              // (tool.start / tool.complete bubbles, thinking, etc.) that the
              // server-side session store doesn't persist. Only fall back to
              // server history when we have nothing cached (e.g. cross-device
              // login), or when the local cache is clearly stale relative to
              // the server (fewer user turns than the server saw).
              const cachedUserTurns = cachedMsgs.filter(m => m.role === "user").length;
              const serverUserTurns = serverMsgs.filter(m => m.role === "user").length;
              const preferCache = cachedMsgs.length > 0 && cachedUserTurns >= serverUserTurns;
              if (skipResumeMessagesRef.current) {
                skipResumeMessagesRef.current = false;
                setState((s) => ({ ...s, phase: "idle", sessionId: tuiSid }));
              } else if (preferCache) {
                setState({ phase: "idle", sessionId: tuiSid, messages: trimMessages(cachedMsgs), activeApproval: null, activeClarify: null, error: null });
              } else {
                setState({ phase: "idle", sessionId: tuiSid, messages: trimMessages(serverMsgs), activeApproval: null, activeClarify: null, error: null });
              }
              needsReconnectRef.current = false;
              resetIdleTimer();
              return;
            } catch {
              // Resume failed — don't delete the tab, fall through to create
              // The tab's cache stays intact for future resume attempts.
            }
          }
        }

        const res = await rpc.call("session.create", buildCreateParams());
        const tuiSid = res.session_id;
        sessionIdRef.current = tuiSid;

        // Create new tab entry (dbId will be filled asynchronously)
        const tempDbId = `pending-${tuiSid}`;
        activeTabDbIdRef.current = tempDbId;
        persistActiveTabDbId(activeKey, tempDbId);

        setTabs(prev => {
          const seedTitle = typeof sessionCreateParamsRef.current?.title === "string"
            ? String(sessionCreateParamsRef.current.title)
            : "New Chat";
          const newTab: SessionTab = { dbId: tempDbId, tuiId: tuiSid, title: seedTitle, messages: [] };
          const updated = [...prev, newTab];
          // Cap at MAX_CACHED_TABS, dropping oldest
          const capped = updated.length > MAX_CACHED_TABS ? updated.slice(-MAX_CACHED_TABS) : updated;
          persistTabs(tabsKey, capped);
          return capped;
        });

        setState({ phase: "idle", sessionId: tuiSid, messages: [], activeApproval: null, activeClarify: null, error: null });
        needsReconnectRef.current = false;
        resetIdleTimer();

        // Fetch real DB ID and update the tab
        const realDbId = await persistDbId(rpc, tuiSid);
        if (realDbId) {
          setTabs(prev => {
            const updated = prev.map(t =>
              t.dbId === tempDbId ? { ...t, dbId: realDbId } : t
            );
            persistTabs(tabsKey, updated);
            return updated;
          });
          activeTabDbIdRef.current = realDbId;
          persistActiveTabDbId(activeKey, realDbId);
        }
      }).catch((err) => {
        if (retriesLeft > 0) {
          setTimeout(() => attemptConnect(retriesLeft - 1), 2000);
        } else {
          setState((s) => ({ ...s, phase: "disconnected", error: err.message || "Connection failed" }));
        }
      });
    };

    attemptConnect(1);
  }, [subscribe, resetIdleTimer]);

  const disconnect = useCallback(() => disconnectRef.current(), []);

  const send = useCallback((text: string) => {
    if (!text.trim()) return;

    const doSubmit = () => {
      const sid = sessionIdRef.current;
      rpcRef.current?.call("prompt.submit", { session_id: sid, text }).catch((err) => {
        setState((s) => ({
          ...s,
          phase: "idle",
          messages: trimMessages([...s.messages, { role: "agent", id: nextMsgId(), text: `Error: ${err.message || "send failed"}`, done: true }]),
        }));
      });
    };

    if (needsReconnectRef.current) {
      needsReconnectRef.current = false;
      const userMsg: ChatMessage = { role: "user", id: nextMsgId(), text };
      setState((s) => ({ ...s, messages: trimMessages([...s.messages, userMsg]), phase: "streaming" }));
      resetIdleTimer();
      // Disconnect old, reconnect and resume target session, then submit
      disconnectRef.current();
      skipResumeMessagesRef.current = true;
      skipResumeRef.current = false;
      setState((s) => ({ ...s, phase: "connecting", error: null }));
      const rpc = new AgentRpc();
      rpcRef.current = rpc;
      const url = buildWsUrl();
      rpc.connect(url).then(async () => {
        subscribe(rpc);
        const cachedDbId = activeTabDbIdRef.current;
        if (cachedDbId) {
          try {
            const res = await rpc.call("session.resume", { session_id: cachedDbId, cols: 80 });
            const tuiSid = res.session_id;
            sessionIdRef.current = tuiSid;
            const resumedDbId = res.resumed || cachedDbId;
            setTabs(prev => {
              const updated = prev.map(t =>
                t.dbId === cachedDbId ? { ...t, tuiId: tuiSid, dbId: resumedDbId } : t
              );
              if (resumedDbId !== cachedDbId) {
                activeTabDbIdRef.current = resumedDbId;
                persistActiveTabDbId(activeKey, resumedDbId);
              }
              persistTabs(tabsKey, updated);
              return updated;
            });
            setState((s) => ({ ...s, phase: "idle", sessionId: tuiSid }));
            resetIdleTimer();
            doSubmit();
            return;
          } catch {
            // Resume failed — fall through to create
          }
        }
        const res = await rpc.call("session.create", buildCreateParams());
        const tuiSid = res.session_id;
        sessionIdRef.current = tuiSid;
        setState({ phase: "idle", sessionId: tuiSid, messages: [], activeApproval: null, activeClarify: null, error: null });
        resetIdleTimer();
        const realDbId = await persistDbId(rpc, tuiSid);
        if (realDbId) {
          setTabs(prev => {
            const updated = prev.map(t =>
              t.dbId === cachedDbId ? { ...t, dbId: realDbId, tuiId: tuiSid } : t
            );
            persistTabs(tabsKey, updated);
            return updated;
          });
          activeTabDbIdRef.current = realDbId;
          persistActiveTabDbId(activeKey, realDbId);
        }
        doSubmit();
      }).catch((err) => {
        setState((s) => ({ ...s, phase: "disconnected", error: err.message || "Connection failed" }));
      });
      return;
    }

    const userMsg: ChatMessage = { role: "user", id: nextMsgId(), text };
    setState((s) => ({ ...s, messages: trimMessages([...s.messages, userMsg]), phase: "streaming" }));
    resetIdleTimer();
    doSubmit();
  }, [resetIdleTimer, subscribe]);

  const respondApproval = useCallback((accept: boolean) => {
    const approval = activeApprovalRef.current;
    if (!approval) return;
    const sid = sessionIdRef.current;
    rpcRef.current?.call("approval.respond", {
      session_id: sid,
      request_id: approval.request_id,
      choice: accept ? "allow" : "deny",
    });
    setState((s) => ({ ...s, activeApproval: null }));
  }, []);

  const respondClarify = useCallback((choice: string) => {
    const clarify = activeClarifyRef.current;
    if (!clarify) return;
    rpcRef.current?.call("clarify.respond", {
      request_id: clarify.request_id,
      answer: choice,
    });
    setState((s) => ({ ...s, activeClarify: null }));
  }, []);

  const interrupt = useCallback(() => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    rpcRef.current?.call("session.interrupt", { session_id: sid });
  }, []);

  const startNewSession = useCallback(() => {
    // Snapshot current tab before leaving
    setTabs(prev => {
      const updated = snapshotCurrentTab(prev);
      persistTabs(tabsKey, updated);
      return updated;
    });
    disconnectRef.current();
    activeTabDbIdRef.current = null;
    skipResumeRef.current = true;
    needsReconnectRef.current = false;
    setState({ phase: "disconnected", sessionId: null, messages: [], activeApproval: null, activeClarify: null, error: null });
    setTimeout(() => connect(), 100);
  }, [connect, snapshotCurrentTab]);

  const switchToTab = useCallback((dbId: string) => {
    if (activeTabDbIdRef.current === dbId) return;

    // Snapshot current tab before leaving
    setTabs(prev => {
      const updated = snapshotCurrentTab(prev);
      persistTabs(tabsKey, updated);
      return updated;
    });

    const targetTab = tabsRef.current.find(t => t.dbId === dbId);
    if (!targetTab) return;

    activeTabDbIdRef.current = dbId;
    persistActiveTabDbId(activeKey, dbId);

    // Load cached messages immediately, mark as needing reconnect on next send
    setState({
      phase: "idle",
      sessionId: targetTab.tuiId,
      messages: targetTab.messages,
      activeApproval: null,
      activeClarify: null,
      error: null,
    });
    needsReconnectRef.current = true;
  }, [snapshotCurrentTab]);

  const closeTab = useCallback((dbId: string) => {
    setTabs(prev => {
      const updated = prev.filter(t => t.dbId !== dbId);
      persistTabs(tabsKey, updated);

      if (activeTabDbIdRef.current === dbId) {
        if (updated.length > 0) {
          // Lazy switch to most recent remaining tab — no reconnect yet
          const newActive = updated[updated.length - 1];
          activeTabDbIdRef.current = newActive.dbId;
          persistActiveTabDbId(activeKey, newActive.dbId);
          setState({
            phase: "idle",
            sessionId: newActive.tuiId,
            messages: newActive.messages,
            activeApproval: null,
            activeClarify: null,
            error: null,
          });
          needsReconnectRef.current = true;
        } else {
          // Last tab removed — disconnect and clear
          activeTabDbIdRef.current = null;
          persistActiveTabDbId(activeKey, null);
          disconnectRef.current();
          setState({ phase: "disconnected", sessionId: null, messages: [], activeApproval: null, activeClarify: null, error: null });
        }
      }

      return updated;
    });
  }, []);

  return {
    state,
    tabs,
    activeTabDbId: activeTabDbIdRef.current,
    send,
    respondApproval,
    respondClarify,
    startNewSession,
    switchToTab,
    closeTab,
    connect,
    disconnect,
    interrupt,
  };
}
