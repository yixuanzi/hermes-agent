import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "./api";
import {
  getCronTokenDistribution,
  getCronjobHistory,
  getCronjobs,
  getKeywordSessions,
  getOverviewStats,
  getOverviewStatus,
  getOverviewTokenTrend,
  getSessionDetail,
  listOverviewKeywords,
  listOverviewSecurityEvents,
} from "./overview";

vi.mock("./api", () => ({
  fetchJSON: vi.fn(),
}));

const mockedFetchJSON = vi.mocked(fetchJSON);

describe("overview api helpers", () => {
  beforeEach(() => {
    mockedFetchJSON.mockReset();
    mockedFetchJSON.mockResolvedValue({});
  });

  it("requests status endpoint", async () => {
    await getOverviewStatus();

    expect(mockedFetchJSON).toHaveBeenCalledWith("/api/overview/status");
  });

  it("requests stats endpoint", async () => {
    await getOverviewStats();

    expect(mockedFetchJSON).toHaveBeenCalledWith("/api/overview/stats");
  });

  it("passes token trend days query param", async () => {
    await getOverviewTokenTrend(30);

    expect(mockedFetchJSON).toHaveBeenCalledWith("/api/overview/token-trend?days=30");
  });

  it("passes security event limit query param", async () => {
    await listOverviewSecurityEvents(20);

    expect(mockedFetchJSON).toHaveBeenCalledWith("/api/overview/security-events?limit=20");
  });

  it("requests keywords endpoint", async () => {
    await listOverviewKeywords();

    expect(mockedFetchJSON).toHaveBeenCalledWith("/api/overview/keywords");
  });

  it("escapes keyword when requesting matching sessions", async () => {
    await getKeywordSessions("cve/critical");

    expect(mockedFetchJSON).toHaveBeenCalledWith(
      "/api/overview/keywords/cve%2Fcritical/sessions",
    );
  });

  it("passes period query param for cron token distribution", async () => {
    await getCronTokenDistribution("7d");

    expect(mockedFetchJSON).toHaveBeenCalledWith("/api/overview/cron-token-dist?period=7d");
  });

  it("requests paginated cron jobs endpoint", async () => {
    await getCronjobs();

    expect(mockedFetchJSON).toHaveBeenCalledWith("/api/cron/jobs?page=1&page_size=8");
  });

  it("escapes job id when requesting cron history", async () => {
    await getCronjobHistory("job/123");

    expect(mockedFetchJSON).toHaveBeenCalledWith("/api/cron/jobs/job%2F123/history");
  });

  it("escapes session id when requesting session detail", async () => {
    await getSessionDetail("session/123");

    expect(mockedFetchJSON).toHaveBeenCalledWith(
      "/api/sessions/session%2F123/detail",
    );
  });
});
