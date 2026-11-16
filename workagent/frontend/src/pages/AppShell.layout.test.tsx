import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { PageMissionHeader } from "../components/PageMissionHeader";
import { StateBlock } from "../components/StateBlock";

describe("AppShell layout", () => {
  it("renders shell navigation and main scaffold", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/overview"]}>
        <Routes>
          <Route path="/" element={<AppShell />}>
            <Route path="overview" element={<div>Overview content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(html).toContain("app-shell");
    expect(html).toContain("side-nav");
    expect(html).toContain("side-nav-workbench");
    expect(html).toContain("side-nav-header");
    expect(html).toContain("side-nav-group");
    expect(html).toContain("side-nav-toggle");
    expect(html).toContain("main-panel");
    expect(html).toContain("workbench-main");
    expect(html).toContain("Workbench navigation");
    expect(html).toContain("aria-current=\"page\"");
    expect(html).toContain("Overview");
    expect(html).toContain('href="/settings"');
    expect(html).toContain("Settings");
    expect(html).toContain("Sign Out");
    expect(html).toContain("Overview content");
  });
});

describe("PageMissionHeader", () => {
  it("renders title, subtitle, status, and actions slots", () => {
    const html = renderToStaticMarkup(
      <PageMissionHeader
        title="Sessions"
        subtitle="Browse active and historical sessions"
        status={<span>Connected</span>}
        actions={<button type="button">Refresh</button>}
      />,
    );

    expect(html).toContain("page-mission-header");
    expect(html).toContain("Sessions");
    expect(html).toContain("Browse active and historical sessions");
    expect(html).toContain("Connected");
    expect(html).toContain("Refresh");
  });
});

describe("StateBlock", () => {
  it("renders loading, empty, error, and success variants", () => {
    const loading = renderToStaticMarkup(<StateBlock kind="loading" title="Loading" message="Please wait" />);
    const empty = renderToStaticMarkup(<StateBlock kind="empty" title="No Results" message="Try a new filter" />);
    const error = renderToStaticMarkup(<StateBlock kind="error" title="Request Failed" message="Network issue" />);
    const success = renderToStaticMarkup(<StateBlock kind="success" title="Complete" message="Finished successfully" />);

    expect(loading).toContain("state-block");
    expect(loading).toContain("state-block-loading");
    expect(empty).toContain("state-block-empty");
    expect(error).toContain("state-block-error");
    expect(success).toContain("state-block-success");
    expect(success).toContain("Complete");
    expect(success).toContain("Finished successfully");
  });
});
