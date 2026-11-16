import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { CronPage, isCronActivationKey, isLatestCronDetailRequest } from "./CronPage";

describe("CronPage structure", () => {
  it("renders stacked cron layout and modal scaffolding", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/cron"]}>
        <CronPage />
      </MemoryRouter>,
    );

    expect(html).toContain("cron-workbench-page");
    expect(html).toContain("cron-stack-layout");
    expect(html).toContain("cron-top-pane");
    expect(html).toContain("cron-history-pane");
    expect(html).toContain("cron-jobs-grid");
    expect(html).toContain("New");
    expect(html).toContain("Create Job");
    expect(html).not.toContain("Edit JSON payload for");
    expect(html).not.toContain("Selected Job:");
    expect(html).toContain("Jobs");
    expect(html).toContain("Run History");
  });
});

describe("CronPage helpers", () => {
  it("validates latest-request guard helper", () => {
    expect(isLatestCronDetailRequest(2, 2)).toBe(true);
    expect(isLatestCronDetailRequest(1, 2)).toBe(false);
  });

  it("validates keyboard activation helper", () => {
    expect(isCronActivationKey("Enter")).toBe(true);
    expect(isCronActivationKey(" ")).toBe(true);
    expect(isCronActivationKey("Escape")).toBe(false);
  });
});
