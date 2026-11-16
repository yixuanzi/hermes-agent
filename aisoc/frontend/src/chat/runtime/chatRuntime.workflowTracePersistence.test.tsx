/**
 * @vitest-environment jsdom
 *
 * Regression tests for persisting Conversation.workflowTrace across a
 * refresh (see chat/lib/workflowTraceCache.ts and the two effects added to
 * AisocChatProvider in chatRuntime.tsx). These mirror the two bugs already
 * proven and fixed for the drawer-tab cache in ChatPage.tsx:
 *  1. the persist effect must not wipe a previously-cached trace while
 *     `conversations` is still empty (mount, before /api/sessions resolves).
 *  2. the cache key must survive a refresh — sessionId, not the transient
 *     client-generated local id.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../../lib/api";
import type { WorkflowTraceEvent } from "../types";
import {
  getWorkflowTraceStorageKey,
  loadCachedWorkflowTrace,
  saveCachedWorkflowTrace,
} from "../lib/workflowTraceCache";
import { AisocChatProvider, useChatRuntime } from "./chatRuntime";

vi.mock("../../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

const fetchJSONMock = vi.mocked(fetchJSON);
const USER_ID = "aisoc-web";

// The repo's jsdom localStorage is unreliable in this suite (see
// chatDrawerTabs.test.ts) — swap in a working in-memory Storage. This also
// covers chatRuntime's own localStorage.* calls (auth token, active conv id).
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

function event(overrides: Partial<WorkflowTraceEvent> & { id: string; timestamp: number }): WorkflowTraceEvent {
  return {
    type: "tool.completed",
    source: "main",
    ...overrides,
  };
}

let rootRef: Root | null = null;
let containerRef: HTMLElement | null = null;
let probeState: { activeConversation?: { id: string; sessionId?: string; workflowTrace?: WorkflowTraceEvent[] } } = {};
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

describe("chatRuntime workflowTrace persistence", () => {
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(window, "localStorage", { value: createMemoryStorage(), configurable: true });
    (globalThis as { WebSocket?: unknown }).WebSocket = FakeWebSocket;
    FakeWebSocket.instances = [];
    probeState = {};
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

  it("restores a cached workflowTrace on mount without wiping it first, keyed by sessionId", async () => {
    // Fixture: this session already had a trace cached from a previous tab
    // load, keyed by its durable session id — exactly how conversations look
    // once /api/sessions resolves (id === sessionId there).
    saveCachedWorkflowTrace(
      USER_ID,
      { "sess-1": [event({ id: "e1", timestamp: 1 }), event({ id: "e2", timestamp: 2 })] },
      ["sess-1"],
    );
    expect(window.localStorage.getItem(getWorkflowTraceStorageKey(USER_ID)!)).not.toBeNull();

    fetchJSONMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/sessions?")) {
        return {
          sessions: [{ id: "sess-1", title: "Existing session", started_at: 1, last_active: 1 }],
          total: 1,
        };
      }
      if (path.includes("/detail")) {
        return { session_id: "sess-1", messages: [] };
      }
      throw new Error(`Unhandled fetchJSON path in test: ${path}`);
    });

    await mount();

    // The bug under test: if the persist effect ran with conversations still
    // [] (right at mount, before the /api/sessions fetch above resolves) and
    // didn't guard against it, it would call storage.removeItem() here and
    // the restore below would find nothing.
    await waitForAssert(() => {
      expect(window.localStorage.getItem(getWorkflowTraceStorageKey(USER_ID)!)).not.toBeNull();
    });

    await waitForAssert(() => {
      expect(probeState.activeConversation?.id).toBe("sess-1");
      expect(probeState.activeConversation?.workflowTrace?.map((e) => e.id)).toEqual(["e1", "e2"]);
    });

    const stillCached = loadCachedWorkflowTrace(USER_ID, ["sess-1"]);
    expect(stillCached["sess-1"]?.map((e) => e.id)).toEqual(["e1", "e2"]);
  });

  it("persists live workflowTrace events under the durable sessionId, not the transient local conversation id", async () => {
    fetchJSONMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/sessions?")) {
        return { sessions: [], total: 0 };
      }
      throw new Error(`Unhandled fetchJSON path in test: ${path}`);
    });
    window.localStorage.setItem("aisoc.accessToken", "test-token");

    await mount();

    // No sessions yet: sending a message creates a brand-new LOCAL
    // conversation (id = client-generated local id, no sessionId).
    await act(async () => {
      runtimeRef!.submitInput("hello");
    });

    const localId = probeState.activeConversation?.id;
    expect(localId).toBeTruthy();
    expect(probeState.activeConversation?.sessionId).toBeFalsy();

    const socket = FakeWebSocket.instances.at(-1);
    expect(socket).toBeTruthy();

    // Simulate the backend accepting the bind and attaching its own session id.
    await act(async () => {
      socket!.readyState = FakeWebSocket.OPEN;
      socket!.onopen?.();
      socket!.onmessage?.({
        data: JSON.stringify({ type: "session.bound", session_id: "sess-9", resumed: false }),
      });
    });
    await waitForAssert(() => {
      expect(probeState.activeConversation?.sessionId).toBe("sess-9");
      expect(probeState.activeConversation?.id).toBe(localId);
    });

    // A live tool-call event arrives — this is what populates workflowTrace.
    await act(async () => {
      socket!.onmessage?.({
        data: JSON.stringify({
          type: "tool.completed",
          turn_id: "t1",
          source: "main",
          tool_name: "run_shell",
          tool_call_id: "c1",
          ts: 1,
        }),
      });
    });

    await waitForAssert(() => {
      const bySessionId = loadCachedWorkflowTrace(USER_ID, ["sess-9"]);
      expect(bySessionId["sess-9"]?.length).toBeGreaterThan(0);
    });
    const byLocalId = loadCachedWorkflowTrace(USER_ID, [localId!]);
    expect(byLocalId[localId!]).toBeUndefined();
  });
});
