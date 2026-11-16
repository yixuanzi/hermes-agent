import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { SkillsPage } from "./SkillsPage";

describe("SkillsPage structure", () => {
  it("renders categorized list and detail/appendix layout zones", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/skills"]}>
        <SkillsPage />
      </MemoryRouter>,
    );

    expect(html).toContain("skills-workbench-page");
    expect(html).toContain("skills-workbench");
    expect(html).toContain("skills-layout-revamp");
    expect(html).toContain("skills-list-pane");
    expect(html).toContain("skills-detail-pane");
    expect(html).toContain("skills-list-scroll");
    expect(html).toContain("skills-detail-main");
    expect(html).toContain("skills-appendix-rail");
    expect(html).toContain("Appendix Files");
  });
});
