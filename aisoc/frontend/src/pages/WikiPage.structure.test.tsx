import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { WikiPage } from "./WikiPage";

vi.mock("../lib/kb", () => ({
  fetchTree: vi.fn().mockResolvedValue({
    root: "/test/wiki",
    cwd: "/",
    items: [
      { name: "guide.md", path: "guide.md", type: "file", size: 128, modified: 1700000000 },
      { name: "notes", path: "notes", type: "dir", size: 0, modified: 1700000000 },
    ],
  }),
  fetchDocument: vi.fn().mockResolvedValue({
    name: "guide.md",
    path: "guide.md",
    size: 128,
    modified: 1700000000,
    content: "# Hello\n\nThis is a **test** document.",
  }),
}));

describe("WikiPage structure", () => {
  it("renders tree pane and detail pane layout zones", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/wiki"]}>
        <WikiPage />
      </MemoryRouter>,
    );
    expect(html).toContain("LLMWiki");
    expect(html).toContain("wiki-tree-pane");
    expect(html).toContain("wiki-detail-pane");
    expect(html).toContain("wiki-tree-scroll");
    expect(html).toContain("wiki-detail-head");
  });
});
