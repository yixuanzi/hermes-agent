/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../lib/api";
import { MemoryPage } from "./MemoryPage";

vi.mock("../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

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

function findButtonByLabel(container: HTMLElement, label: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((candidate) =>
    candidate.textContent?.includes(label),
  );
  if (!button) throw new Error(`Button not found: ${label}`);
  return button;
}

function getEditor(container: HTMLElement): HTMLTextAreaElement {
  const textarea = container.querySelector<HTMLTextAreaElement>("#memory-editor-textarea");
  if (!textarea) throw new Error("Memory editor textarea not found");
  return textarea;
}

function setEditorValue(editor: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  if (!setter) throw new Error("Unable to set textarea value in test");
  setter.call(editor, value);
  editor.dispatchEvent(new Event("input", { bubbles: true }));
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

async function mountMemoryPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/memory"]}>
        <MemoryPage />
      </MemoryRouter>,
    );
  });

  rootRef = root;
  containerRef = container;

  return { container, root };
}

describe("MemoryPage behavior", () => {
  it("keeps latest selected file content when load responses resolve out of order", async () => {
    const alphaDeferred = createDeferred<{ name: string; content: string }>();
    const betaDeferred = createDeferred<{ name: string; content: string }>();

    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/memory") {
        return Promise.resolve({
          soul: { name: "SOUL.md" },
          user_preferences: { name: "USER.md" },
          memory_files: [{ name: "alpha.md" }, { name: "beta.md" }],
        }) as Promise<unknown>;
      }
      if (url === "/api/memory/soul") {
        return Promise.resolve({ name: "SOUL.md", content: "soul-content" }) as Promise<unknown>;
      }
      if (url === "/api/memory/files/alpha.md") {
        return alphaDeferred.promise as Promise<unknown>;
      }
      if (url === "/api/memory/files/beta.md") {
        return betaDeferred.promise as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountMemoryPage();

    await waitForAssert(() => {
      expect(containerRef?.textContent).toContain("Memory Files");
      expect(getEditor(containerRef as HTMLElement).value).toBe("soul-content");
    });

    const alphaButton = findButtonByLabel(containerRef as HTMLElement, "alpha.md");
    const betaButton = findButtonByLabel(containerRef as HTMLElement, "beta.md");

    await act(async () => {
      alphaButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await act(async () => {
      betaButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await act(async () => {
      betaDeferred.resolve({ name: "beta.md", content: "beta-content" });
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Current selection: beta.md");
      expect(getEditor(containerRef as HTMLElement).value).toBe("beta-content");
    });

    await act(async () => {
      alphaDeferred.resolve({ name: "alpha.md", content: "stale-alpha-content" });
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Current selection: beta.md");
      expect(getEditor(containerRef as HTMLElement).value).toBe("beta-content");
      expect(text).not.toContain("stale-alpha-content");
    });
  });

  it("guards against switching files when unsaved edits exist", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");

    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/memory") {
        return Promise.resolve({
          soul: { name: "SOUL.md" },
          user_preferences: { name: "USER.md" },
          memory_files: [{ name: "alpha.md" }],
        }) as Promise<unknown>;
      }
      if (url === "/api/memory/soul") {
        return Promise.resolve({ name: "SOUL.md", content: "soul-original" }) as Promise<unknown>;
      }
      if (url === "/api/memory/files/alpha.md") {
        return Promise.resolve({ name: "alpha.md", content: "alpha-content" }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountMemoryPage();

    await waitForAssert(() => {
      expect(getEditor(containerRef as HTMLElement).value).toBe("soul-original");
    });

    await act(async () => {
      const editor = getEditor(containerRef as HTMLElement);
      setEditorValue(editor, "edited-content");
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect(getEditor(containerRef as HTMLElement).value).toBe("edited-content");
    });

    const alphaButton = findButtonByLabel(containerRef as HTMLElement, "alpha.md");

    confirmSpy.mockReturnValueOnce(false);
    await act(async () => {
      alphaButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(confirmSpy).toHaveBeenCalledTimes(1);

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Current selection: Agent Soul");
      expect(getEditor(containerRef as HTMLElement).value).toBe("edited-content");
      const alphaCalls = fetchJSONMock.mock.calls.filter(([calledUrl]) => calledUrl === "/api/memory/files/alpha.md");
      expect(alphaCalls.length).toBe(0);
    });

    confirmSpy.mockReturnValueOnce(true);
    await act(async () => {
      alphaButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Current selection: alpha.md");
      expect(getEditor(containerRef as HTMLElement).value).toBe("alpha-content");
      const alphaCalls = fetchJSONMock.mock.calls.filter(([calledUrl]) => calledUrl === "/api/memory/files/alpha.md");
      expect(alphaCalls.length).toBe(1);
    });
  });

  it("encodes file names for memory file GET and PUT routes", async () => {
    const fileName = "ops/run book.md";
    const encodedFileName = encodeURIComponent(fileName);

    fetchJSONMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/memory") {
        return Promise.resolve({
          soul: { name: "SOUL.md" },
          user_preferences: { name: "USER.md" },
          memory_files: [{ name: fileName }],
        }) as Promise<unknown>;
      }
      if (url === "/api/memory/soul") {
        return Promise.resolve({ name: "SOUL.md", content: "soul-content" }) as Promise<unknown>;
      }
      if (url === `/api/memory/files/${encodedFileName}` && (!init || !init.method)) {
        return Promise.resolve({ name: fileName, content: "encoded-file-content" }) as Promise<unknown>;
      }
      if (url === `/api/memory/files/${encodedFileName}` && init?.method === "PUT") {
        return Promise.resolve({ success: true }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountMemoryPage();

    await waitForAssert(() => {
      expect(getEditor(containerRef as HTMLElement).value).toBe("soul-content");
    });

    const fileButton = findButtonByLabel(containerRef as HTMLElement, fileName);

    await act(async () => {
      fileButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect(getEditor(containerRef as HTMLElement).value).toBe("encoded-file-content");
    });

    await act(async () => {
      const editor = getEditor(containerRef as HTMLElement);
      setEditorValue(editor, "updated-content");
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect(getEditor(containerRef as HTMLElement).value).toBe("updated-content");
    });

    const saveButton = findButtonByLabel(containerRef as HTMLElement, "Save");
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const getCalls = fetchJSONMock.mock.calls.filter(([calledUrl, init]) =>
        calledUrl === `/api/memory/files/${encodedFileName}` && (!init || !(init as RequestInit).method),
      );
      const putCalls = fetchJSONMock.mock.calls.filter(([calledUrl, init]) =>
        calledUrl === `/api/memory/files/${encodedFileName}` && (init as RequestInit | undefined)?.method === "PUT",
      );

      expect(getCalls.length).toBeGreaterThanOrEqual(1);
      expect(putCalls.length).toBe(1);

      const putInit = putCalls[0]?.[1] as RequestInit;
      expect(putInit.method).toBe("PUT");
      expect(putInit.body).toBe(JSON.stringify({ content: "updated-content" }));
    });
  });
});
