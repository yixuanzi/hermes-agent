/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchDocument, fetchTree } from "../lib/kb";
import { WikiPage } from "./WikiPage";

vi.mock("../lib/kb", () => ({
  fetchTree: vi.fn(),
  fetchDocument: vi.fn(),
}));

const fetchTreeMock = vi.mocked(fetchTree);
const fetchDocumentMock = vi.mocked(fetchDocument);

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
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
  }
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  fetchTreeMock.mockResolvedValue({
    root: "/tmp/wiki",
    cwd: "/",
    items: [{ name: "page.md", path: "page.md", type: "file", size: 1, modified: 1 }],
  });
});

afterEach(async () => {
  if (rootRef && containerRef) {
    await act(async () => rootRef?.unmount());
    containerRef.remove();
  }
  rootRef = null;
  containerRef = null;
});

describe("WikiPage behavior", () => {
  it("renders untrusted document HTML without scripts or event handlers", async () => {
    const content = [
      "# Ingested page",
      '<iframe srcdoc="<script>parent.document.title=1</script>"></iframe>',
      "<script>window.__wikiXss = 1</script>",
      '<img src="x" onerror="window.__wikiXss = 2">',
      '<a href="javascript:alert(1)">bad link</a>',
      "==<img src=x onerror=alert(3)>==",
      "Some ==highlighted== text",
      "> [!note] Callout body",
    ].join("\n\n");
    fetchDocumentMock.mockResolvedValue({ name: "page.md", path: "page.md", size: content.length, modified: 1, content });

    const container = document.createElement("div");
    document.body.appendChild(container);
    rootRef = createRoot(container);
    containerRef = container;
    await act(async () => {
      rootRef?.render(
        <MemoryRouter initialEntries={["/wiki"]}>
          <WikiPage />
        </MemoryRouter>,
      );
    });
    await waitForAssert(() => expect(container.querySelector("button.wiki-tree-file")).not.toBeNull());
    await act(async () => container.querySelector<HTMLButtonElement>("button.wiki-tree-file")?.click());
    await waitForAssert(() => expect(container.textContent).toContain("Ingested page"));

    const rendered = container.querySelector(".wiki-detail-content");
    if (!rendered) throw new Error("Wiki content not found");
    expect(rendered.querySelector("iframe, script")).toBeNull();
    expect(rendered.innerHTML).not.toMatch(/onerror|javascript:/i);
    expect(Array.from(rendered.querySelectorAll("mark"), (mark) => mark.textContent)).toContain("highlighted");
    expect(rendered.querySelector("blockquote.callout.callout-note")?.getAttribute("data-callout")).toBe("note");
  });
});
