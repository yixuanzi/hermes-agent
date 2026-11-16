/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../lib/api";
import { SessionsPage } from "./SessionsPage";

const mockNavigate = vi.fn();

vi.mock("../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
};

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
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

async function mountSessionsPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/sessions"]}>
        <SessionsPage />
      </MemoryRouter>,
    );
  });

  return {
    container,
    root,
    cleanup: async () => {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    },
  };
}

function findRowByTitle(container: HTMLElement, title: string): HTMLLIElement {
  const row = Array.from(container.querySelectorAll<HTMLLIElement>('li[role="button"]')).find((candidate) =>
    candidate.textContent?.includes(title),
  );
  if (!row) throw new Error(`Session row not found for title: ${title}`);
  return row;
}

let rootRef: Root | null = null;
let containerRef: HTMLElement | null = null;
const fetchJSONMock = vi.mocked(fetchJSON);

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
});

afterEach(async () => {
  if (rootRef && containerRef) {
    await act(async () => {
      rootRef?.unmount();
    });
    containerRef.remove();
  }
  rootRef = null;
  containerRef = null;
});

describe("SessionsPage behavior", () => {
  it("keeps latest-selected session detail when responses resolve out of order", async () => {
    const aDeferred = createDeferred<{
      session_id: string;
      messages: Array<{ role: string; content: string }>;
    }>();
    const bDeferred = createDeferred<{
      session_id: string;
      messages: Array<{ role: string; content: string }>;
    }>();

    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/sessions?limit=20") {
        return Promise.resolve({
          sessions: [
            { id: "a", title: "Session A", model: "m-a", source: "src-a" },
            { id: "b", title: "Session B", model: "m-b", source: "src-b" },
          ],
        }) as Promise<unknown>;
      }
      if (url === "/api/sessions/a/messages") {
        return aDeferred.promise as Promise<unknown>;
      }
      if (url === "/api/sessions/b/messages") {
        return bDeferred.promise as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    const mounted = await mountSessionsPage();
    rootRef = mounted.root;
    containerRef = mounted.container;

    await waitForAssert(() => {
      expect(containerRef?.querySelectorAll('li[role="button"]').length).toBe(2);
    });

    const rowA = findRowByTitle(containerRef as HTMLElement, "Session A");
    const rowB = findRowByTitle(containerRef as HTMLElement, "Session B");

    await act(async () => {
      rowA.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await act(async () => {
      rowB.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await act(async () => {
      bDeferred.resolve({
        session_id: "b",
        messages: [
          { role: "assistant", content: "message-from-b" },
          { role: "system", content: "system-from-b" },
        ],
      });
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("b");
      expect(text).toContain("message-from-b");
      expect(text).not.toContain("system-from-b");
    });

    await act(async () => {
      aDeferred.resolve({
        session_id: "a",
        messages: [{ role: "assistant", content: "stale-message-from-a" }],
      });
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("b");
      expect(text).toContain("message-from-b");
      expect(text).not.toContain("stale-message-from-a");
    });
  });

  it("activates session selection via keyboard Enter and Space", async () => {
    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/sessions?limit=20") {
        return Promise.resolve({
          sessions: [
            { id: "a", title: "Session A", model: "m-a", source: "src-a" },
            { id: "b", title: "Session B", model: "m-b", source: "src-b" },
          ],
        }) as Promise<unknown>;
      }
      if (url === "/api/sessions/a/messages") {
        return Promise.resolve({
          session_id: "a",
          messages: [{ role: "assistant", content: "kbd-enter-a" }],
        }) as Promise<unknown>;
      }
      if (url === "/api/sessions/b/messages") {
        return Promise.resolve({
          session_id: "b",
          messages: [{ role: "assistant", content: "kbd-space-b" }],
        }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    const mounted = await mountSessionsPage();
    rootRef = mounted.root;
    containerRef = mounted.container;

    await waitForAssert(() => {
      expect(containerRef?.querySelectorAll('li[role="button"]').length).toBe(2);
    });

    const rowA = findRowByTitle(containerRef as HTMLElement, "Session A");
    const rowB = findRowByTitle(containerRef as HTMLElement, "Session B");

    await act(async () => {
      rowA.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("a");
      expect(text).toContain("kbd-enter-a");
    });

    await act(async () => {
      rowB.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("b");
      expect(text).toContain("kbd-space-b");
    });
  });

  it("renders assistant tool calls and supports expand/collapse for long tool messages", async () => {
    const longToolOutput = `${"A".repeat(125)}TAIL`;
    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/sessions?limit=20") {
        return Promise.resolve({
          sessions: [{ id: "s-tool", title: "Tool Session", model: "gpt-5.4", source: "slack" }],
        }) as Promise<unknown>;
      }
      if (url === "/api/sessions/s-tool/messages") {
        return Promise.resolve({
          session_id: "s-tool",
          messages: [
            {
              id: 1473,
              role: "assistant",
              content: "",
              tool_calls: [
                {
                  id: "call_P42Mr2kNlD4VOPPAEMTmDTvu",
                  type: "function",
                  function: {
                    name: "slack_block_kit",
                    arguments:
                      "{\"action\":\"send\",\"card\":{\"text\":\"测试交互卡片\",\"blocks\":[{\"type\":\"section\",\"text\":\"这是一个用于交互测试的 Block Card\"}]}}",
                  },
                },
              ],
            },
            { id: 1474, role: "tool", content: longToolOutput },
          ],
        }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    const mounted = await mountSessionsPage();
    rootRef = mounted.root;
    containerRef = mounted.container;

    await waitForAssert(() => {
      expect(containerRef?.querySelectorAll('li[role="button"]').length).toBe(1);
    });

    const row = findRowByTitle(containerRef as HTMLElement, "Tool Session");
    await act(async () => {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("slack_block_kit");
      expect(text).toContain("tool_calls");
      expect(text).toContain("Expand");
      expect(text).not.toContain(longToolOutput);
    });

    const expandButton = (containerRef as HTMLElement).querySelector(
      'button[aria-label="Expand tool message"]',
    ) as HTMLButtonElement | null;
    expect(expandButton).not.toBeNull();

    await act(async () => {
      expandButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain(longToolOutput);
      expect(text).toContain("Collapse");
    });
  });

  it("does not duplicate plain string user content", async () => {
    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/sessions?limit=20") {
        return Promise.resolve({
          sessions: [{ id: "s-user", title: "User Session", model: "gpt-5.4", source: "cli" }],
        }) as Promise<unknown>;
      }
      if (url === "/api/sessions/s-user/messages") {
        return Promise.resolve({
          session_id: "s-user",
          messages: [{ id: 1, role: "user", content: "hello world" }],
        }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    const mounted = await mountSessionsPage();
    rootRef = mounted.root;
    containerRef = mounted.container;

    await waitForAssert(() => {
      expect(containerRef?.querySelectorAll('li[role="button"]').length).toBe(1);
    });

    const row = findRowByTitle(containerRef as HTMLElement, "User Session");
    await act(async () => {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const pre = (containerRef as HTMLElement).querySelector(".sessions-message-stream .detail-message pre");
      expect(pre?.textContent).toBe("hello world");
    });
  });
});
