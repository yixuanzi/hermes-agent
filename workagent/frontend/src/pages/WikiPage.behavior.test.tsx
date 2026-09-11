/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchDocument,
  fetchTree,
  saveDocument,
  type DocumentResponse,
  type TreeResponse,
} from "../lib/kb";
import { ApiError } from "../lib/api";
import { WikiPage } from "./WikiPage";

vi.mock("../lib/kb", () => ({
  fetchTree: vi.fn(),
  fetchDocument: vi.fn(),
  saveDocument: vi.fn(),
}));

const fetchTreeMock = vi.mocked(fetchTree);
const fetchDocumentMock = vi.mocked(fetchDocument);
const saveDocumentMock = vi.mocked(saveDocument);

let rootRef: Root | null = null;
let containerRef: HTMLElement | null = null;

function treeResponse(): TreeResponse {
  return {
    root: "/tmp/wiki",
    cwd: "/",
    items: [
      { name: "guide.md", path: "guide.md", type: "file", size: 12, modified: 1 },
      { name: "notes.md", path: "notes.md", type: "file", size: 13, modified: 1 },
    ],
  };
}

function documentResponse(name: string, content: string): DocumentResponse {
  return { name, path: name, size: content.length, modified: 1, content };
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

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const result = container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
  if (!result) throw new Error(`Button not found: ${label}`);
  return result;
}

function fileButton(container: HTMLElement, name: string): HTMLButtonElement {
  const result = Array.from(container.querySelectorAll<HTMLButtonElement>("button.wiki-tree-file")).find(
    (candidate) => candidate.textContent?.includes(name),
  );
  if (!result) throw new Error(`File button not found: ${name}`);
  return result;
}

function setEditorValue(editor: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  if (!setter) throw new Error("Unable to set textarea value in test");
  setter.call(editor, value);
  editor.dispatchEvent(new Event("input", { bubbles: true }));
}

async function mountWikiPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/wiki"]}>
        <WikiPage />
      </MemoryRouter>,
    );
  });
  rootRef = root;
  containerRef = container;
  return container;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  fetchTreeMock.mockResolvedValue(treeResponse());
  fetchDocumentMock.mockImplementation(async (path) => documentResponse(path, "# Original"));
  saveDocumentMock.mockResolvedValue({ ok: true, path: "guide.md", size: 16, modified: 2 });
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
  it("previews unsaved Markdown when switching back to browse mode", async () => {
    const container = await mountWikiPage();
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fileButton(container, "guide.md").click();
      await Promise.resolve();
    });
    await waitForAssert(() => expect(container.textContent).toContain("Original"));

    await act(async () => button(container, "Code mode").click());
    const editor = container.querySelector<HTMLTextAreaElement>("#wiki-document-editor");
    if (!editor) throw new Error("Wiki editor not found");
    await act(async () => setEditorValue(editor, "# Draft heading\n\nDraft body"));

    await act(async () => button(container, "Browse mode").click());
    await waitForAssert(() => {
      expect(container.textContent).toContain("Draft heading");
      expect(container.textContent).toContain("Draft body");
      expect(container.textContent).toContain("Previewing unsaved changes");
    });
    expect(saveDocumentMock).not.toHaveBeenCalled();
  });

  it("saves the selected document and clears dirty state", async () => {
    const container = await mountWikiPage();
    await act(async () => {
      fileButton(container, "guide.md").click();
      await Promise.resolve();
    });
    await waitForAssert(() => expect(container.textContent).toContain("Original"));

    await act(async () => button(container, "Code mode").click());
    const editor = container.querySelector<HTMLTextAreaElement>("#wiki-document-editor");
    if (!editor) throw new Error("Wiki editor not found");
    await act(async () => setEditorValue(editor, "# Saved"));

    const saveButton = container.querySelector<HTMLButtonElement>(".wiki-header-save-button");
    if (!(saveButton instanceof HTMLButtonElement)) throw new Error("Save button not found");
    await act(async () => {
      saveButton.click();
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect(saveDocumentMock).toHaveBeenCalledWith("guide.md", "# Saved");
      expect(container.textContent).toContain("Saved");
      expect(container.textContent).not.toContain("Unsaved changes");
    });
  });

  it("guards file switching when the current draft is dirty", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const container = await mountWikiPage();
    await act(async () => {
      fileButton(container, "guide.md").click();
      await Promise.resolve();
    });
    await waitForAssert(() => expect(container.textContent).toContain("Original"));

    await act(async () => button(container, "Code mode").click());
    const editor = container.querySelector<HTMLTextAreaElement>("#wiki-document-editor");
    if (!editor) throw new Error("Wiki editor not found");
    await act(async () => setEditorValue(editor, "# Keep this draft"));

    confirmSpy.mockReturnValueOnce(false);
    await act(async () => fileButton(container, "notes.md").click());
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(fetchDocumentMock).toHaveBeenCalledTimes(1);

    confirmSpy.mockReturnValueOnce(true);
    await act(async () => {
      fileButton(container, "notes.md").click();
      await Promise.resolve();
    });
    await waitForAssert(() => expect(container.textContent).toContain("notes.md"));
    expect(fetchDocumentMock).toHaveBeenCalledWith("notes.md");
  });

  it("keeps the draft and shows the backend error when saving fails", async () => {
    saveDocumentMock.mockRejectedValueOnce(
      new ApiError(JSON.stringify({ detail: "Permission denied: guide.md" }), 403),
    );
    const container = await mountWikiPage();
    await act(async () => {
      fileButton(container, "guide.md").click();
      await Promise.resolve();
    });
    await waitForAssert(() => expect(container.textContent).toContain("Original"));

    await act(async () => button(container, "Code mode").click());
    const editor = container.querySelector<HTMLTextAreaElement>("#wiki-document-editor");
    if (!editor) throw new Error("Wiki editor not found");
    await act(async () => setEditorValue(editor, "# Failed save draft"));

    const saveButton = container.querySelector<HTMLButtonElement>(".wiki-header-save-button");
    if (!(saveButton instanceof HTMLButtonElement)) throw new Error("Save button not found");
    await act(async () => {
      saveButton.click();
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect(container.textContent).toContain("Permission denied: guide.md");
      expect(container.textContent).toContain("Unsaved changes");
      expect(editor.value).toBe("# Failed save draft");
    });
  });

  it("ignores stale document responses when file selection changes quickly", async () => {
    let resolveGuide!: (value: DocumentResponse) => void;
    let resolveNotes!: (value: DocumentResponse) => void;
    const guidePromise = new Promise<DocumentResponse>((resolve) => {
      resolveGuide = resolve;
    });
    const notesPromise = new Promise<DocumentResponse>((resolve) => {
      resolveNotes = resolve;
    });
    fetchDocumentMock.mockImplementation((path) =>
      path === "guide.md" ? guidePromise : notesPromise,
    );

    const container = await mountWikiPage();
    await act(async () => {
      fileButton(container, "guide.md").click();
      fileButton(container, "notes.md").click();
      await Promise.resolve();
    });

    await act(async () => {
      resolveNotes(documentResponse("notes.md", "# Notes content"));
      await Promise.resolve();
    });
    await waitForAssert(() => {
      expect(container.textContent).toContain("Notes content");
      expect(container.textContent).toContain("notes.md");
    });

    await act(async () => {
      resolveGuide(documentResponse("guide.md", "# Stale guide content"));
      await Promise.resolve();
    });
    await waitForAssert(() => {
      expect(container.textContent).toContain("Notes content");
      expect(container.textContent).not.toContain("Stale guide content");
    });
  });
});
