/**
 * @vitest-environment jsdom
 *
 * Regression tests for persisting the full Conversation.messages array
 * (including inline main-tools/delegate-tools tool-call bubbles) across a
 * refresh — see chat/lib/messageCache.ts and the persist effect in
 * chatRuntime.tsx. Mirrors chatRuntime.workflowTracePersistence.test.tsx's
 * proven failure mode:
 *  1. the persist effect must not wipe a previously-cached message list
 *     while `conversations` is still empty (mount, before /api/sessions
 *     resolves).
 *  2. the cache key must survive a refresh — sessionId, not the transient
 *     client-generated local id.
 *
 * A cache hit is intentionally NOT trusted as final: it's shown immediately
 * (so a returning conversation isn't blank while the network round-trips),
 * but hydrateConversation() still fetches /detail and reconciles — replacing
 * whatever was there before the fetch with the backend's authoritative
 * transcript, while preserving anything that arrived live *during* the
 * fetch. This deliberately trades away the local cache's richer tool-call
 * bubbles on reopen (the plain-text /detail endpoint can't reconstruct
 * those) for a guarantee that a conversation can never get permanently stuck
 * showing the wrong content — see hydrateConversation's messageCountAtFetchStart
 * slicing.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../../lib/api";
import type { Message } from "../types";
import {
  getMessageCacheStorageKey,
  loadCachedMessages,
  saveCachedMessages,
} from "../lib/messageCache";
import { loadCachedWorkflowTrace } from "../lib/workflowTraceCache";
import { AisocChatProvider, useChatRuntime } from "./chatRuntime";

vi.mock("../../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

const fetchJSONMock = vi.mocked(fetchJSON);
const USER_ID = "aisoc-web";

// The repo's jsdom localStorage is unreliable in this suite (see
// chatDrawerTabs.test.ts) — swap in a working in-memory Storage.
function createMemoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => (data.has(key) ? data.get(key)! : null),
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key);
    },
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  };
}

class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: FakeWebSocket[] = [];
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}

function message(overrides: Partial<Message> & { id: string }): Message {
  return {
    sender: "user",
    text: "hi",
    timestamp: "10:00",
    ...overrides,
  };
}

let rootRef: Root | null = null;
let containerRef: HTMLElement | null = null;
let probeState: { activeConversation?: { id: string; sessionId?: string; messages: Message[] } } = {
  activeConversation: undefined,
};
let runtimeRef: ReturnType<typeof useChatRuntime> | null = null;

function Probe() {
  const runtime = useChatRuntime();
  probeState.activeConversation = runtime.activeConversation;
  runtimeRef = runtime;
  return null;
}

async function waitForAssert(assertion: () => void, timeoutMs = 2000): Promise<void> {
  const start = Date.now();
  while (true) {
    try {
      assertion();
      return;
    } catch (error) {
      if (Date.now() - start >= timeoutMs) throw error;
      await act(async () => {
        await Promise.resolve();
      });
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }
}

async function mount(): Promise<void> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <AisocChatProvider isChatVisible={true}>
        <Probe />
      </AisocChatProvider>,
    );
  });
  rootRef = root;
  containerRef = container;
}

describe("chatRuntime message-cache persistence", () => {
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(window, "localStorage", { value: createMemoryStorage(), configurable: true });
    (globalThis as { WebSocket?: unknown }).WebSocket = FakeWebSocket;
    FakeWebSocket.instances = [];
    probeState = { activeConversation: undefined };
    fetchJSONMock.mockReset();
  });

  afterEach(() => {
    if (rootRef) {
      act(() => rootRef!.unmount());
      rootRef = null;
    }
    containerRef?.remove();
    containerRef = null;
  });

  it("restores cached messages on mount without wiping them, then reconciles against the backend's authoritative /detail transcript", async () => {
    const cachedMessages = [
      message({ id: "m1", sender: "user", text: "run the scan" }),
      message({
        id: "m2",
        sender: "assistant",
        text: "",
        kind: "main-tools",
        turnId: "t1",
        source: "main",
        chainSteps: [
          { agentName: "run_shell", type: "vip_tool", status: "Completed", message: "done", timestamp: "10:01" },
        ],
      }),
      message({ id: "m3", sender: "assistant", text: "Scan complete." }),
    ];
    saveCachedMessages(USER_ID, { "sess-1": cachedMessages }, ["sess-1"]);
    expect(window.localStorage.getItem(getMessageCacheStorageKey(USER_ID)!)).not.toBeNull();

    fetchJSONMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/sessions?")) {
        return {
          sessions: [{ id: "sess-1", title: "Existing session", started_at: 1, last_active: 1 }],
          total: 1,
        };
      }
      // This is the backend's authoritative transcript for sess-1 — distinct
      // from the cached messages above so the assertions below can tell
      // whether the final state came from the (possibly stale/wrong) cache
      // or from this fetch.
      if (path.includes("/detail")) {
        return {
          session_id: "sess-1",
          messages: [
            { role: "user", content: "run the scan", timestamp: 1 },
            { role: "assistant", content: "Scan complete.", timestamp: 2 },
          ],
        };
      }
      throw new Error(`Unhandled fetchJSON path in test: ${path}`);
    });

    await mount();

    // The persist effect must not wipe the cache before it's ever restored
    // (e.g. by running with `conversations` still [] right at mount).
    await waitForAssert(() => {
      expect(window.localStorage.getItem(getMessageCacheStorageKey(USER_ID)!)).not.toBeNull();
    });

    // Final state reflects the backend's /detail transcript (reconciled),
    // not the stale cache — this is what lets a wrong/stale cache entry
    // self-correct instead of sticking forever.
    await waitForAssert(() => {
      expect(probeState.activeConversation?.id).toBe("sess-1");
      expect(probeState.activeConversation?.messages.map((m) => m.id)).toEqual([
        "history-sess-1-0",
        "history-sess-1-1",
      ]);
    });

    const detailCalls = fetchJSONMock.mock.calls.filter(([path]) => String(path).includes("/detail"));
    expect(detailCalls).toHaveLength(1);

    // The reconciled (not the stale) transcript is what ends up persisted.
    await waitForAssert(() => {
      const stillCached = loadCachedMessages(USER_ID, ["sess-1"]);
      expect(stillCached["sess-1"]?.map((m) => m.id)).toEqual(["history-sess-1-0", "history-sess-1-1"]);
    });
  });

  it("persists live messages (including tool-call bubbles) under the durable sessionId, not the transient local conversation id", async () => {
    fetchJSONMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/sessions?")) {
        return { sessions: [], total: 0 };
      }
      throw new Error(`Unhandled fetchJSON path in test: ${path}`);
    });
    window.localStorage.setItem("aisoc.accessToken", "test-token");

    await mount();

    await act(async () => {
      runtimeRef!.submitInput("hello");
    });
    const localId = probeState.activeConversation?.id;
    expect(localId).toBeTruthy();

    const socket = FakeWebSocket.instances.at(-1);
    await act(async () => {
      socket!.readyState = FakeWebSocket.OPEN;
      socket!.onopen?.();
      socket!.onmessage?.({
        data: JSON.stringify({ type: "session.bound", session_id: "sess-9", resumed: false }),
      });
    });
    await waitForAssert(() => {
      expect(probeState.activeConversation?.sessionId).toBe("sess-9");
    });

    await waitForAssert(() => {
      const bySessionId = loadCachedMessages(USER_ID, ["sess-9"]);
      // The user message from submitInput() above is already enough to prove
      // the persist effect wrote under the sessionId key.
      expect(bySessionId["sess-9"]?.length).toBeGreaterThan(0);
    });
    const byLocalId = loadCachedMessages(USER_ID, [localId!]);
    expect(byLocalId[localId!]).toBeUndefined();

    // Sanity: the parallel workflowTrace cache (already covered by its own
    // test file) isn't disturbed by this — both caches share the same key
    // helper but are independent storage entries.
    expect(loadCachedWorkflowTrace(USER_ID, ["sess-9"])).toEqual({});
  });
});
