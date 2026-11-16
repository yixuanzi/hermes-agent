import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import {
  OverviewPage,
  createCronHistoryModalOpenState,
  createKeywordModalOpenState,
  createSessionModalOpenState,
  formatDateTime,
  loadCronDistributionForPeriod,
  loadOverviewDataResilient,
  loadTrendForRange,
  openCronHistoryDrilldown,
  openKeywordSessionsDrilldown,
  openSessionDetailDrilldown,
  shouldLoadTrendRange,
  type OverviewLoaderDeps,
} from "./OverviewPage";

describe("OverviewPage", () => {
  it("renders loading state initially", () => {
    const html = renderToStaticMarkup(<OverviewPage />);

    expect(html).toContain("Loading overview dashboard...");
  });

  it("renders key overview sections when state data is ready", () => {
    const html = renderToStaticMarkup(
      <OverviewPage
        initialData={{
          status: {
            status: "running",
            model: "gpt-5",
            provider: "openai",
            profile: "default",
            uptime_seconds: 3600,
            last_activity: 1710000000,
          },
          stats: {
            total_sessions: 100,
            active_sessions: 5,
            today_tokens: 12345,
            today_input_tokens: 7000,
            today_output_tokens: 5345,
            today_cost_usd: 1.23,
            cron_jobs_total: 8,
            cron_jobs_enabled: 6,
            memory_used_chars: 10000,
            memory_total_chars: 20000,
            memory_percent: 50,
            memory_soul_chars: 6000,
            memory_soul_limit: 12000,
            memory_soul_percent: 50,
            memory_user_chars: 4000,
            memory_user_limit: 8000,
            memory_user_percent: 50,
            source_distribution: { cli: 3, telegram: 2 },
          },
          trend: [
            {
              date: "2026-05-27",
              input_tokens: 100,
              output_tokens: 200,
              total_tokens: 300,
              sessions: 2,
            },
          ],
          cronjobs: {
            page: 1,
            page_size: 8,
            total: 1,
            total_pages: 1,
            has_prev: false,
            has_next: false,
            items: [
              {
                id: "job-1",
                name: "Daily summary",
                enabled: true,
                schedule: "0 8 * * *",
                last_run: {
                  session_id: "sess-1",
                  started_at: 1710000100,
                  ended_at: 1710000200,
                  tokens: 200,
                  status: "success",
                },
                last_run_at: "2026-05-29T14:50:57+08:00",
                next_run_at: "2026-05-30T09:00:00+08:00",
                run_count: 21,
              },
            ],
          },
          events: [
            {
              session_id: "sess-2",
              type: "security",
              type_label: "Security Alert",
              icon: "⚠️",
              time: 1710000300,
              duration: 5,
              tokens: 120,
              status: "open",
              risk_level: "high",
              summary: "Suspicious command chain detected",
              entities: ["curl", "bash"],
              verdict: "investigate",
            },
          ],
        }}
      />,
    );

    expect(html).toContain("ADIC AISOC OVERVIEW DASHBOARD");
    expect(html).toContain("TOKEN USAGE TREND");
    expect(html).toContain("SECURITY KEYWORDS");
    expect(html).toContain("CRON TOKEN CONSUMPTION SHARE");
    expect(html).toContain("SECURITY EVENTS");
    expect(html).toContain("overview-cyber-wrap workbench-overview");
    expect(html).toContain("panel panel-trend");
  });
});

describe("loadOverviewDataResilient", () => {
  function createDeps(overrides: Partial<OverviewLoaderDeps> = {}): OverviewLoaderDeps {
    return {
      getOverviewStatus: async () => ({
        status: "running",
        model: "gpt-5",
        provider: "openai",
        profile: "default",
        uptime_seconds: 3600,
        last_activity: 1710000000,
      }),
      getOverviewStats: async () => ({
        total_sessions: 100,
        active_sessions: 5,
        today_tokens: 12345,
        today_input_tokens: 7000,
        today_output_tokens: 5345,
        today_cost_usd: 1.23,
        cron_jobs_total: 8,
        cron_jobs_enabled: 6,
        memory_used_chars: 10000,
        memory_total_chars: 20000,
        memory_percent: 50,
        memory_soul_chars: 6000,
        memory_soul_limit: 12000,
        memory_soul_percent: 50,
        memory_user_chars: 4000,
        memory_user_limit: 8000,
        memory_user_percent: 50,
        source_distribution: { cli: 3 },
      }),
      getOverviewTokenTrend: async () => [],
      getCronjobs: async () => ({
        page: 1,
        page_size: 8,
        total: 0,
        total_pages: 1,
        has_prev: false,
        has_next: false,
        items: [],
      }),
      listOverviewSecurityEvents: async () => [],
      ...overrides,
    };
  }

  it("keeps partial data when some endpoints fail", async () => {
    const result = await loadOverviewDataResilient(
      createDeps({
        getOverviewTokenTrend: async () => {
          throw new Error("trend failed");
        },
        getCronjobs: async () => {
          throw new Error("cron failed");
        },
      }),
    );

    expect(result.data?.status?.status).toBe("running");
    expect(result.data?.stats?.total_sessions).toBe(100);
    expect(result.data?.trend).toBeUndefined();
    expect(result.data?.cronjobs).toBeUndefined();
    expect(result.error).toContain("token trend");
    expect(result.error).toContain("cron jobs");
  });

  it("returns null data when all critical endpoints fail", async () => {
    const fail = async () => {
      throw new Error("failed");
    };
    const result = await loadOverviewDataResilient(
      createDeps({
        getOverviewStatus: fail,
        getOverviewStats: fail,
        getOverviewTokenTrend: fail,
        getCronjobs: fail,
        listOverviewSecurityEvents: fail,
      }),
    );

    expect(result.data).toBeNull();
    expect(result.error).toContain("Failed to load overview data.");
  });
});

describe("Overview interactions", () => {
  it("computes trend switch behavior for tabs", () => {
    expect(shouldLoadTrendRange(7, 30, true)).toBe(true);
    expect(shouldLoadTrendRange(7, 7, false)).toBe(true);
    expect(shouldLoadTrendRange(7, 7, true)).toBe(false);
  });

  it("switches trend range between 7D and 30D via loader", async () => {
    const calledDays: number[] = [];

    const result7 = await loadTrendForRange(7, {
      getOverviewTokenTrend: async (days) => {
        calledDays.push(days);
        return [];
      },
    });
    const result30 = await loadTrendForRange(30, {
      getOverviewTokenTrend: async (days) => {
        calledDays.push(days);
        return [];
      },
    });

    expect(calledDays).toEqual([7, 30]);
    expect(result7.error).toBe("");
    expect(result30.error).toBe("");
  });

  it("loads cron period distribution for today/7d/30d", async () => {
    const calledPeriods: string[] = [];
    const mockCronDistribution = async (period?: "today" | "7d" | "30d") => {
      const safePeriod = period ?? "today";
      calledPeriods.push(safePeriod);
      return {
        period: safePeriod,
        total_cron_tokens: 1,
        non_cron_tokens: 1,
        grand_total: 2,
        cron_percent: 50,
        jobs: [],
      };
    };

    await loadCronDistributionForPeriod("today", {
      getCronTokenDistribution: mockCronDistribution,
    });
    await loadCronDistributionForPeriod("7d", {
      getCronTokenDistribution: mockCronDistribution,
    });
    await loadCronDistributionForPeriod("30d", {
      getCronTokenDistribution: mockCronDistribution,
    });

    expect(calledPeriods).toEqual(["today", "7d", "30d"]);
  });

  it("opens drilldowns for cron history/session detail/keyword sessions", async () => {
    const calls: string[] = [];

    const cron = await openCronHistoryDrilldown("job-1", {
      getCronjobHistoryDrilldown: async (jobId) => {
        calls.push(`cron:${jobId}`);
        return [
          {
            session_id: "sess-1",
            started_at: 1710000100,
            ended_at: 1710000200,
            duration_seconds: 100,
            messages: 5,
            tokens: 200,
            status: "success",
          },
        ];
      },
    });

    const session = await openSessionDetailDrilldown("sess-1", {
      getSessionDetailDrilldown: async (sessionId) => {
        calls.push(`session:${sessionId}`);
        return {
          session_id: sessionId,
          source: "cron",
          model: "gpt-5",
          started_at: 1710000100,
          ended_at: 1710000200,
          message_count: 5,
          tokens: 200,
          messages: [],
        };
      },
    });

    const keyword = await openKeywordSessionsDrilldown("security", {
      getKeywordSessionsDrilldown: async (word) => {
        calls.push(`keyword:${word}`);
        return [
          {
            session_id: "sess-2",
            title: "Security review",
            source: "cli",
            started_at: 1710000100,
            messages: 3,
            tokens: 120,
          },
        ];
      },
    });

    expect(calls).toEqual(["cron:job-1", "session:sess-1", "keyword:security"]);
    expect(cron.error).toBe("");
    expect(session.error).toBe("");
    expect(keyword.error).toBe("");
    expect(cron.payload?.[0]?.session_id).toBe("sess-1");
    expect(session.payload?.session_id).toBe("sess-1");
    expect(keyword.payload?.[0]?.session_id).toBe("sess-2");
  });

  it("creates modal open states for cron/session/keyword drilldowns", () => {
    const cronState = createCronHistoryModalOpenState({
      id: "job-1",
      name: "Daily summary",
      enabled: true,
      schedule: "0 8 * * *",
      last_run: null,
      last_run_at: null,
      next_run_at: null,
      run_count: 0,
    });
    const sessionState = createSessionModalOpenState("sess-1");
    const keywordState = createKeywordModalOpenState("security");

    expect(cronState.open).toBe(true);
    expect(cronState.jobId).toBe("job-1");
    expect(cronState.loading).toBe(true);
    expect(sessionState.open).toBe(true);
    expect(sessionState.sessionId).toBe("sess-1");
    expect(sessionState.detail).toBeNull();
    expect(keywordState.open).toBe(true);
    expect(keywordState.keyword).toBe("security");
    expect(keywordState.items).toEqual([]);
  });
});

describe("formatDateTime", () => {
  it("treats 0 as a valid timestamp", () => {
    expect(formatDateTime(0)).not.toBe("-");
  });
});
