import { fetchJSON } from "./api";

export type OverviewStatus = {
  status: string;
  model: string;
  provider: string;
  profile: string;
  uptime_seconds: number;
  last_activity: number;
};

export type OverviewStats = {
  total_sessions: number;
  active_sessions: number;
  today_tokens: number;
  today_input_tokens: number;
  today_output_tokens: number;
  today_cost_usd: number;
  cron_jobs_total: number;
  cron_jobs_enabled: number;
  memory_used_chars: number;
  memory_total_chars: number;
  memory_percent: number;
  memory_soul_chars: number;
  memory_soul_limit: number;
  memory_soul_percent: number;
  memory_user_chars: number;
  memory_user_limit: number;
  memory_user_percent: number;
  source_distribution: Record<string, number>;
};

export type TokenTrendPoint = {
  date: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  sessions: number;
};

export type SecurityEvent = {
  session_id: string;
  type: string;
  type_label: string;
  icon: string;
  time: number | null;
  duration: number | null;
  tokens: number;
  status: string;
  risk_level: string;
  summary: string;
  entities: string[];
  verdict: string;
};

export type OverviewKeyword = {
  word: string;
  count: number;
  lang: "en" | "zh";
};

export type KeywordSession = {
  session_id: string;
  title: string;
  source: string;
  started_at: number | null;
  messages: number;
  tokens: number;
};

export type CronTokenPeriod = "today" | "7d" | "30d";

export type CronTokenDistributionJob = {
  job_id: string;
  name: string;
  runs: number;
  input_tokens: number;
  output_tokens: number;
  io_tokens: number;
  cache_read: number;
  cache_write: number;
  percent_of_cron: number;
  percent_of_total: number;
};

export type CronTokenDistribution = {
  period: CronTokenPeriod;
  total_cron_tokens: number;
  non_cron_tokens: number;
  grand_total: number;
  cron_percent: number;
  jobs: CronTokenDistributionJob[];
};

export type ModelUsageItem = {
  model: string;
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  percent_of_total: number;
};

export type ModelUsageDistribution = {
  period: CronTokenPeriod;
  total_tokens: number;
  total_cost_usd: number;
  models: ModelUsageItem[];
};

export type CronjobLastRun = {
  session_id: string;
  started_at: number | null;
  ended_at: number | null;
  tokens: number;
  status: string;
};

export type Cronjob = {
  id: string;
  name: string;
  enabled: boolean;
  schedule:
    | string
    | {
        kind?: string;
        expr?: string;
        display?: string;
      };
  last_run: CronjobLastRun | null;
  last_run_at: string | null;
  next_run_at: string | null;
  run_count: number;
  repeat?: {
    times: number | null;
    completed: number;
  };
  state?: string;
};

export type PaginatedCronjobs = {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
  items: Cronjob[];
};

export type CronjobHistoryItem = {
  session_id: string;
  started_at: number | null;
  ended_at: number | null;
  duration_seconds: number | null;
  messages: number;
  tokens: number;
  status: string;
};

export type SessionDetailMessage = {
  role: string;
  content: string;
  tool_name?: string | null;
  timestamp?: number | null;
};

export type SessionDetail = {
  session_id: string;
  source: string;
  model: string;
  started_at: number | null;
  ended_at: number | null;
  message_count: number;
  tokens: number;
  messages: SessionDetailMessage[];
};

export type PaginatedSecurityEvents = {
  page: number;
  page_size: number;
  fetched_count: number;
  items: SecurityEvent[];
  has_prev: boolean;
  has_next: boolean;
};

export function getOverviewStatus(): Promise<OverviewStatus> {
  return fetchJSON<OverviewStatus>("/api/overview/status");
}

export function getOverviewStats(): Promise<OverviewStats> {
  return fetchJSON<OverviewStats>("/api/overview/stats");
}

export function getOverviewTokenTrend(days: 7 | 30): Promise<TokenTrendPoint[]> {
  return fetchJSON<TokenTrendPoint[]>(`/api/overview/token-trend?days=${days}`);
}

export function listOverviewSecurityEvents(limit: number = 15): Promise<SecurityEvent[]> {
  return fetchJSON<SecurityEvent[]>(`/api/overview/security-events?limit=${limit}`);
}

export async function listOverviewSecurityEventsPage(
  page: number,
  pageSize: number = 5,
): Promise<PaginatedSecurityEvents> {
  const safePage = Number.isFinite(page) ? Math.max(1, Math.trunc(page)) : 1;
  const safePageSize = Number.isFinite(pageSize) ? Math.min(25, Math.max(1, Math.trunc(pageSize))) : 5;
  // Fetch exactly what the requested page needs (+1 sentinel) so has_next stays correct.
  const limit = safePage * safePageSize + 1;
  const events = await listOverviewSecurityEvents(limit);
  const start = (safePage - 1) * safePageSize;
  const end = start + safePageSize;
  return {
    page: safePage,
    page_size: safePageSize,
    fetched_count: events.length,
    items: events.slice(start, end),
    has_prev: safePage > 1,
    has_next: events.length > end,
  };
}

export function listOverviewKeywords(): Promise<OverviewKeyword[]> {
  return fetchJSON<OverviewKeyword[]>("/api/overview/keywords");
}

export function getKeywordSessions(keyword: string): Promise<KeywordSession[]> {
  const encodedKeyword = encodeURIComponent(keyword);
  return fetchJSON<KeywordSession[]>(`/api/overview/keywords/${encodedKeyword}/sessions`);
}

export function getKeywordSessionsDrilldown(keyword: string): Promise<KeywordSession[]> {
  return getKeywordSessions(keyword);
}

export function getCronTokenDistribution(
  period: CronTokenPeriod = "today",
): Promise<CronTokenDistribution> {
  return fetchJSON<CronTokenDistribution>(`/api/overview/cron-token-dist?period=${period}`);
}

export function getOverviewModelUsage(
  period: CronTokenPeriod = "7d",
): Promise<ModelUsageDistribution> {
  return fetchJSON<ModelUsageDistribution>(`/api/overview/model-usage?period=${period}`);
}

export function getCronjobs(page: number = 1, pageSize: number = 8): Promise<PaginatedCronjobs> {
  const safePage = Number.isFinite(page) ? Math.max(1, Math.trunc(page)) : 1;
  const safePageSize = Number.isFinite(pageSize) ? Math.min(25, Math.max(1, Math.trunc(pageSize))) : 8;
  return fetchJSON<PaginatedCronjobs>(`/api/cron/jobs?page=${safePage}&page_size=${safePageSize}`);
}

export function getCronjobHistory(jobId: string): Promise<CronjobHistoryItem[]> {
  const encodedJobId = encodeURIComponent(jobId);
  return fetchJSON<CronjobHistoryItem[]>(`/api/cron/jobs/${encodedJobId}/history`);
}

export function getCronjobHistoryDrilldown(jobId: string): Promise<CronjobHistoryItem[]> {
  return getCronjobHistory(jobId);
}

export function getSessionDetail(sessionId: string): Promise<SessionDetail> {
  const encodedSessionId = encodeURIComponent(sessionId);
  return fetchJSON<SessionDetail>(`/api/sessions/${encodedSessionId}/detail`);
}

export function getSessionDetailDrilldown(sessionId: string): Promise<SessionDetail> {
  return getSessionDetail(sessionId);
}
