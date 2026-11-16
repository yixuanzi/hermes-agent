/**
 * @vitest-environment jsdom
 *
 * Regression tests for the drawer-tab persistence bugs fixed on this branch:
 *  1. the "save" effect used to wipe localStorage on mount before the
 *     "restore" effect ever got to read it (conversations still empty at
 *     that point) — every refresh silently deleted the cached tab.
 *  2. the cache key must survive a refresh: a live conversation's `id` is a
 *     client-generated local id that never changes for that tab, but after a
 *     refresh conversations are rebuilt from /api/sessions where `id` is set
 *     to the backend `sessionId` instead. Caching by `id` breaks across a
 *     refresh; caching by `sessionId` (falling back to `id` pre-bind) does not.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "../chat/types";
import {
  getDrawerTabsStorageKey,
  loadCachedDrawerTabs,
  saveCachedDrawerTabs,
} from "../chat/lib/chatDrawerTabs";

const runtime = vi.hoisted(() => ({
  state: {
    activeConvId: "",
    activeConversation: undefined as Conversation | undefined,
    conversations: [] as Conversation[],
    pendingHtmlPreviews: {} as Record<string, { conversationId: string; messageId: string; path: string }>,
    consumePendingHtmlPreview: vi.fn(),
    openSession: vi.fn(),
  },
}));

vi.mock("../chat/runtime/chatRuntime", () => ({
  useChatRuntime: () => runtime.state,
}));

vi.mock("../chat/components", () => ({
  ChatLayout: (props: { drawerOpen?: boolean; drawer?: React.ReactNode; children?: React.ReactNode }) => (
    <div data-testid="chat-layout" data-drawer-open={String(Boolean(props.drawerOpen))}>
      {props.drawerOpen ? props.drawer : null}
      {props.children}
    </div>
  ),
  ChatDrawer: (props: { tabs: Array<{ path: string }>; activeTab: string }) => (
    <div
      data-testid="chat-drawer"
      data-active-tab={props.activeTab}
      data-tab-paths={props.tabs.map((tab) => tab.path).join(",")}
    />
  ),
  Composer: () => null,
  MessageStream: () => null,
  RunStateIndicator: () => null,
  SessionListPane: () => null,
}));

const { ChatPage } = await import("./ChatPage");

const USER_ID = "aisoc-web";

// The repo's jsdom localStorage is unreliable in this suite (see
// chatDrawerTabs.test.ts) — swap in a working in-memory Storage so
// ChatPage's internal window.localStorage.* calls behave like a real browser.
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
Object.defineProperty(window, "localStorage", { value: createMemoryStorage(), configurable: true });

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
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );
  });
  rootRef = root;
  containerRef = container;
  return container;
}

async function rerender(): Promise<void> {
  await act(async () => {
    rootRef!.render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );
  });
}

function conversation(overrides: Partial<Conversation> & { id: string }): Conversation {
  return {
    title: "Untitled",
    messages: [],
    timestamp: "Earlier",
    ...overrides,
  };
}

describe("ChatPage drawer-tab persistence", () => {
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    window.localStorage.clear();
    runtime.state.activeConvId = "";
    runtime.state.activeConversation = undefined;
    runtime.state.conversations = [];
    runtime.state.pendingHtmlPreviews = {};
    runtime.state.consumePendingHtmlPreview = vi.fn();
    runtime.state.openSession = vi.fn();
  });

  afterEach(() => {
    if (rootRef) {
      act(() => rootRef!.unmount());
      rootRef = null;
    }
    containerRef?.remove();
    containerRef = null;
    window.localStorage.clear();
  });

  it("does not wipe a previously-cached tab while conversations are still loading, and auto-opens the drawer once they arrive", async () => {
    // Seed storage as if a previous browser session had this file open —
    // keyed by the durable session id, exactly as conversations look post-refresh.
    saveCachedDrawerTabs(
      USER_ID,
      {
        "sess-1": {
          activeTab: "file:%2Ftmp%2Freport.html" as never,
          tabs: [{ id: "file:%2Ftmp%2Freport.html" as never, path: "/tmp/report.html", title: "report.html" }],
        },
      },
      ["sess-1"],
    );
    expect(window.localStorage.getItem(getDrawerTabsStorageKey(USER_ID)!)).not.toBeNull();

    // Initial mount: mirrors real app boot — conversations haven't loaded yet.
    await mount();

    // The bug under test: the persist effect used to run right here, with
    // drawerTabsByConversation still {} and conversations still [], and call
    // storage.removeItem() before the fetch below ever resolved.
    await waitForAssert(() => {
      expect(window.localStorage.getItem(getDrawerTabsStorageKey(USER_ID)!)).not.toBeNull();
    });

    // The async /api/sessions fetch "resolves": conversations populate with
    // the same session id used above, and activeConvId gets restored to it.
    const conv = conversation({ id: "sess-1", sessionId: "sess-1", title: "Existing session" });
    runtime.state.conversations = [conv];
    runtime.state.activeConvId = "sess-1";
    runtime.state.activeConversation = conv;
    await rerender();

    // Drawer should auto-open with the restored tab, and storage must still
    // hold it afterwards (the persist effect re-saving it is fine; deleting
    // it is the bug).
    await waitForAssert(() => {
      const layout = containerRef!.querySelector('[data-testid="chat-layout"]');
      expect(layout?.getAttribute("data-drawer-open")).toBe("true");
      const drawer = containerRef!.querySelector('[data-testid="chat-drawer"]');
      expect(drawer?.getAttribute("data-tab-paths")).toBe("/tmp/report.html");
    });

    const restored = loadCachedDrawerTabs(USER_ID, ["sess-1"]);
    expect(restored["sess-1"]?.tabs.map((tab) => tab.path)).toEqual(["/tmp/report.html"]);
  });

  it("caches a newly-generated file under the durable sessionId, not the transient local conversation id", async () => {
    // Mid-session shape: id is still the client-generated local id, but the
    // socket has already bound and attached the backend sessionId — exactly
    // like right after a tool run finishes and pendingHtmlPreviews fires.
    const conv = conversation({ id: "local-abc", sessionId: "sess-9", title: "Live session" });
    runtime.state.conversations = [conv];
    runtime.state.activeConvId = "local-abc";
    runtime.state.activeConversation = conv;
    runtime.state.pendingHtmlPreviews = {
      "local-abc": { conversationId: "local-abc", messageId: "m1", path: "/tmp/generated.html" },
    };

    await mount();

    await waitForAssert(() => {
      expect(runtime.state.consumePendingHtmlPreview).toHaveBeenCalledWith("local-abc", "m1");
    });

    const bySessionId = loadCachedDrawerTabs(USER_ID, ["sess-9"]);
    const byLocalId = loadCachedDrawerTabs(USER_ID, ["local-abc"]);
    expect(bySessionId["sess-9"]?.tabs.map((tab) => tab.path)).toEqual(["/tmp/generated.html"]);
    expect(byLocalId["local-abc"]).toBeUndefined();
  });
});
