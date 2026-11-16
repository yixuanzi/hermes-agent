/**
 * @vitest-environment jsdom
 *
 * Faithful repro attempt for a user-reported bug: click history conversation
 * A in the real SessionListPane UI, then click a different history
 * conversation B, and B's rendered transcript shows A's content. Unlike
 * chatRuntime.historySwitchRace.test.tsx (which drives setActiveConversation
 * directly through the hook), this renders the ACTUAL SessionListPane +
 * MessageStream components and dispatches real DOM clicks, to exercise
 * React's list reconciliation/key handling — not just the state layer.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../../lib/api";
import { AisocChatProvider } from "../runtime/chatRuntime";
import { MessageStream } from "./MessageStream";
import { SessionListPane } from "./SessionListPane";

vi.mock("../../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

const fetchJSONMock = vi.mocked(fetchJSON);

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
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send() {}
  close() {}
}

let rootRef: Root | null = null;
let containerRef: HTMLElement | null = null;

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

async function mount(): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <AisocChatProvider isChatVisible={true}>
        <SessionListPane />
        <MessageStream />
      </AisocChatProvider>,
    );
  });
  rootRef = root;
  containerRef = container;
  return container;
}

function findRowByTitle(container: HTMLElement, title: string): HTMLElement {
  const rows = Array.from(container.querySelectorAll('[role="button"]'));
  const row = rows.find((el) => el.textContent?.includes(title));
  if (!row) throw new Error(`No row found for title "${title}"`);
  return row as HTMLElement;
}

describe("SessionListPane real click history switch", () => {
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(window, "localStorage", { value: createMemoryStorage(), configurable: true });
    (globalThis as { WebSocket?: unknown }).WebSocket = FakeWebSocket;
    Element.prototype.scrollIntoView = vi.fn();
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

  it("shows B's own transcript after clicking A then B, via real DOM clicks", async () => {
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
      if (path.includes("/sessions/sess-A/detail")) {
        return {
          session_id: "sess-A",
          messages: [{ role: "user", content: "A: what time is it?", timestamp: 5 }],
        };
      }
      if (path.includes("/sessions/sess-B/detail")) {
        return {
          session_id: "sess-B",
          messages: [{ role: "user", content: "B: what is the capital of France?", timestamp: 10 }],
        };
      }
      throw new Error(`Unhandled fetchJSON path in test: ${path}`);
    });

    const container = await mount();

    await waitForAssert(() => {
      findRowByTitle(container, "Conversation A");
      findRowByTitle(container, "Conversation B");
    });

    // Click A, wait for its transcript to actually render.
    await act(async () => {
      findRowByTitle(container, "Conversation A").click();
    });
    await waitForAssert(() => {
      expect(container.textContent).toContain("A: what time is it?");
    });

    // Click B — the exact user-reported trigger.
    await act(async () => {
      findRowByTitle(container, "Conversation B").click();
    });

    await waitForAssert(() => {
      expect(container.textContent).toContain("B: what is the capital of France?");
    });
    // The actual bug report: A's content leaking into B's rendered view.
    expect(container.textContent).not.toContain("A: what time is it?");
  });
});
