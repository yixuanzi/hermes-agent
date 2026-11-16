import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { MemoryPage } from "./MemoryPage";

describe("MemoryPage structure", () => {
  it("renders editor-first workbench layout with index/context rail", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/memory"]}>
        <MemoryPage />
      </MemoryRouter>,
    );

    expect(html).toContain("memory-workbench-page");
    expect(html).toContain("memory-workbench");
    expect(html).toContain("memory-editor-pane");
    expect(html).toContain("memory-context-rail");
    expect(html).toContain("memory-index-panel");
    expect(html).toContain("Memory Files");
    expect(html).toContain("File Context");
    expect(html).toContain("Save");
  });
});
