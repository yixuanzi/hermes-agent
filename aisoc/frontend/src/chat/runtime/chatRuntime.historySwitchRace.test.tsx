/**
 * @vitest-environment jsdom
 *
 * Diagnostic repro for a user-reported bug: click history conversation A,
 * then quickly click a different history conversation B, and B ends up
 * showing A's content. This test simulates the exact interleaving — switch
 * to A, switch to B *before* A's /detail fetch resolves, then let both
 * fetches resolve in various orders — to check whether hydrateConversation's
 * per-call closures (conversationId, sessionId, messageCountAtFetchStart)
 * actually stay isolated under a real race, or whether there's a crossover.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../../lib/api";
import type { Message } from "../types";
import { AisocChatProvider, useChatRuntime } from "./chatRuntime";

vi.mock("../../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

const fetchJSONMock = vi.mocked(fetchJSON);
const USER_ID = "aisoc-web";

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

let rootRef: Root | null = null;
let containerRef: HTMLElement | null = null;
let probeState: {
  conversations: { id: string; sessionId?: string; messages: Message[] }[];
} = { conversations: [] };
let runtimeRef: ReturnType<typeof useChatRuntime> | null = null;

function Probe() {
  const runtime = useChatRuntime();
  probeState.conversations = runtime.conversations;
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

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("chatRuntime history-switch race", () => {
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(window, "localStorage", { value: createMemoryStorage(), configurable: true });
    (globalThis as { WebSocket?: unknown }).WebSocket = FakeWebSocket;
    FakeWebSocket.instances = [];
    probeState = { conversations: [] };
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

  it("keeps A and B's history isolated when B is opened before A's /detail fetch resolves", async () => {
    const detailA = deferred<unknown>();
    const detailB = deferred<unknown>();

    fetchJSONMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/sessions?")) {
        return {
          sessions: [
            { id: "sess-A", title: "Conversation A", started_at: 1, last_active: 1 },
            { id: "sess-B", title: "Conversation B", started_at: 2, last_active: 2 },
          ],
          total: 2,
        };
      }
      if (path.includes("/sessions/sess-A/detail")) return detailA.promise;
      if (path.includes("/sessions/sess-B/detail")) return detailB.promise;
      throw new Error(`Unhandled fetchJSON path in test: ${path}`);
    });

    await mount();

    await waitForAssert(() => {
      expect(probeState.conversations.map((c) => c.id).sort()).toEqual(["sess-A", "sess-B"]);
    });

    // Click A.
    await act(async () => {
      runtimeRef!.setActiveConversation("sess-A");
    });
    // Click B *before* A's /detail fetch has resolved — this is the exact
    // interleaving the user reported.
    await act(async () => {
      runtimeRef!.setActiveConversation("sess-B");
    });

    // Now resolve B's fetch first, then A's — the order a real network
    // round-trip would most plausibly produce (B requested later can still
    // land first, or vice versa; test both orderings are safe by resolving
    // B first here).
    await act(async () => {
      detailB.resolve({
        session_id: "sess-B",
        messages: [{ role: "user", content: "B: what is the capital of France?", timestamp: 10 }],
      });
      await Promise.resolve();
    });
    await act(async () => {
      detailA.resolve({
        session_id: "sess-A",
        messages: [{ role: "user", content: "A: what time is it?", timestamp: 5 }],
      });
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const a = probeState.conversations.find((c) => c.id === "sess-A");
      const b = probeState.conversations.find((c) => c.id === "sess-B");
      expect(a?.messages.map((m) => m.text)).toEqual(["A: what time is it?"]);
      expect(b?.messages.map((m) => m.text)).toEqual(["B: what is the capital of France?"]);
    });
  });

  it("keeps A and B's history isolated when A's /detail fetch resolves after B's (reverse order)", async () => {
    const detailA = deferred<unknown>();
    const detailB = deferred<unknown>();

    fetchJSONMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/sessions?")) {
        return {
          sessions: [
            { id: "sess-A", title: "Conversation A", started_at: 1, last_active: 1 },
            { id: "sess-B", title: "Conversation B", started_at: 2, last_active: 2 },
          ],
          total: 2,
        };
      }
      if (path.includes("/sessions/sess-A/detail")) return detailA.promise;
      if (path.includes("/sessions/sess-B/detail")) return detailB.promise;
      throw new Error(`Unhandled fetchJSON path in test: ${path}`);
    });

    await mount();

    await waitForAssert(() => {
      expect(probeState.conversations.map((c) => c.id).sort()).toEqual(["sess-A", "sess-B"]);
    });

    await act(async () => {
      runtimeRef!.setActiveConversation("sess-A");
    });
    await act(async () => {
      runtimeRef!.setActiveConversation("sess-B");
    });

    // Reverse order this time: A resolves first, then B.
    await act(async () => {
      detailA.resolve({
        session_id: "sess-A",
        messages: [{ role: "user", content: "A: what time is it?", timestamp: 5 }],
      });
      await Promise.resolve();
    });
    await act(async () => {
      detailB.resolve({
        session_id: "sess-B",
        messages: [{ role: "user", content: "B: what is the capital of France?", timestamp: 10 }],
      });
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const a = probeState.conversations.find((c) => c.id === "sess-A");
      const b = probeState.conversations.find((c) => c.id === "sess-B");
      expect(a?.messages.map((m) => m.text)).toEqual(["A: what time is it?"]);
      expect(b?.messages.map((m) => m.text)).toEqual(["B: what is the capital of France?"]);
    });
  });
});
