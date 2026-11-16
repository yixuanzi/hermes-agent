import { useEffect, useRef, useState } from "react";

import { StateBlock } from "../components/StateBlock";
import { fetchJSON } from "../lib/api";
import { type SessionMessage, type SessionMessagesResponse, formatMessagePayload } from "../lib/messages";

type CronJob = {
  id?: string;
  name?: string;
  profile?: string;
  schedule?:
    | string
    | {
        kind?: string;
        expr?: string;
        display?: string;
      };
  paused?: boolean;
  enabled?: boolean;
  state?: string;
  model?: string;
  deliver?: string;
  last_run_at?: string | number | null;
  next_run_at?: string | number | null;
  repeat?: {
    times?: number | null;
    completed?: number;
  };
};

type CronJobsPagePayload = {
  items?: CronJob[];
  page?: number;
  page_size?: number;
  total?: number;
  total_pages?: number;
  has_prev?: boolean;
  has_next?: boolean;
};

type CronHistoryItem = {
  session_id: string;
  started_at?: string | number | null;
  ended_at?: string | number | null;
  duration_seconds?: number | null;
  messages?: number;
  tokens?: number;
  status?: string;
};

const CRON_EDITABLE_KEYS = [
  "name",
  "prompt",
  "schedule",
  "schedule_display",
  "enabled",
  "deliver",
  "profile",
  "skills",
  "skill",
  "enabled_toolsets",
  "model",
  "provider",
  "base_url",
  "script",
  "workdir",
  "no_agent",
] as const;

const DEFAULT_CRON_CREATE_PAYLOAD = {
  name: "daily-test-msg",
  prompt: "向当前对话发送一条测试消息，内容为：这是一条每日测试消息。（这是一个示例定时任务，创建后请根据需要修改名称、提示词、调度等信息，默认每天10:11）",
  schedule: "11 10 * * *",
  deliver: "slack",
  skills: [],
  skill: null,
  enabled_toolsets: null,
  model: "deepseek-v4-flash",
  provider: null,
  base_url: null,
  script: null,
  workdir: null,
  no_agent: false,
} as const;

function buildDefaultCreatePayloadEditor(): string {
  return JSON.stringify(DEFAULT_CRON_CREATE_PAYLOAD, null, 2);
}

const CRON_PAGE_SIZE = 12;

function renderSchedule(schedule: CronJob["schedule"]): string {
  if (!schedule) return "no schedule";
  if (typeof schedule === "string") return schedule;
  if (schedule.display) return schedule.display;
  if (schedule.expr) return schedule.expr;
  if (schedule.kind) return schedule.kind;
  return "no schedule";
}

function toDateFromUnknown(value: unknown): Date | null {
  if (value === null || value === undefined || value === "") return null;

  if (typeof value === "number" && Number.isFinite(value)) {
    const milliseconds = value > 1_000_000_000_000 ? value : value * 1000;
    const date = new Date(milliseconds);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    if (/^\d+(\.\d+)?$/.test(trimmed)) {
      const numeric = Number(trimmed);
      if (Number.isFinite(numeric)) {
        const milliseconds = numeric > 1_000_000_000_000 ? numeric : numeric * 1000;
        const date = new Date(milliseconds);
        return Number.isNaN(date.getTime()) ? null : date;
      }
    }
    const date = new Date(trimmed);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  return null;
}

function formatDateTime(value: unknown): string {
  const date = toDateFromUnknown(value);
  if (!date) return "--";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function formatRepeat(job: CronJob): string {
  const completed = asNumber(job.repeat?.completed, 0);
  const rawTimes = job.repeat?.times;
  const times = rawTimes === null || rawTimes === undefined ? null : asNumber(rawTimes, 0);
  return `${completed} / ${times === null || times <= 0 ? "∞" : times}`;
}

function isJobPaused(job: CronJob): boolean {
  if (typeof job.paused === "boolean") return job.paused;
  if (typeof job.enabled === "boolean") return !job.enabled;
  return String(job.state || "").toLowerCase() === "paused";
}

function buildEditableCronUpdates(detail: Record<string, unknown> | null): Record<string, unknown> {
  if (!detail) return {};
  const updates: Record<string, unknown> = {};
  for (const key of CRON_EDITABLE_KEYS) {
    if (Object.prototype.hasOwnProperty.call(detail, key)) {
      updates[key] = detail[key];
    }
  }
  return updates;
}

export function isLatestCronDetailRequest(requestId: number, latestRequestId: number): boolean {
  return requestId === latestRequestId;
}

export function isCronActivationKey(key: string): boolean {
  return key === "Enter" || key === " ";
}

export function CronPage() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [jobsPage, setJobsPage] = useState(1);
  const [jobsTotalPages, setJobsTotalPages] = useState(1);
  const [jobsTotal, setJobsTotal] = useState(0);
  const [createEditor, setCreateEditor] = useState(buildDefaultCreatePayloadEditor);
  const [createPending, setCreatePending] = useState(false);
  const [createError, setCreateError] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [history, setHistory] = useState<CronHistoryItem[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailEditor, setDetailEditor] = useState("{}");
  const [detailDirty, setDetailDirty] = useState(false);
  const [savePending, setSavePending] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveSuccess, setSaveSuccess] = useState("");
  const [rawDetailEditor, setRawDetailEditor] = useState("{}");
  const [rawDetailDirty, setRawDetailDirty] = useState(false);
  const [rawSavePending, setRawSavePending] = useState(false);
  const [rawSaveError, setRawSaveError] = useState("");
  const [rawSaveSuccess, setRawSaveSuccess] = useState("");
  const [actionError, setActionError] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [jobDetailModalOpen, setJobDetailModalOpen] = useState(false);
  const [rawDetailModalOpen, setRawDetailModalOpen] = useState(false);
  const [sessionModalOpen, setSessionModalOpen] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const [sessionMessages, setSessionMessages] = useState<SessionMessage[]>([]);
  const [expandedMessages, setExpandedMessages] = useState<Record<string, boolean>>({});
  const detailRequestIdRef = useRef(0);
  const historyRequestIdRef = useRef(0);
  const selectedJobIdRef = useRef("");
  const jobsPageRef = useRef(1);

  const selectedJob = jobs.find((job) => String(job.id || job.name || "") === selectedJobId);

  useEffect(() => {
    selectedJobIdRef.current = selectedJobId;
  }, [selectedJobId]);

  useEffect(() => {
    jobsPageRef.current = jobsPage;
  }, [jobsPage]);

  function syncDetailEditors(payload: Record<string, unknown>) {
    setDetail(payload);
    setDetailEditor(JSON.stringify(buildEditableCronUpdates(payload), null, 2));
    setRawDetailEditor(JSON.stringify(payload, null, 2));
    setDetailDirty(false);
    setRawDetailDirty(false);
  }

  function handleRawEditorValueChange(nextValue: string) {
    setRawDetailEditor(nextValue);
    setRawDetailDirty(true);
    setRawSaveError("");
    setRawSaveSuccess("");
  }

  async function loadJobs(page: number = jobsPageRef.current) {
    setLoading(true);
    setError("");
    try {
      const safePage = Number.isFinite(page) ? Math.max(1, Math.trunc(page)) : 1;
      const payload = await fetchJSON<CronJobsPagePayload>(
        `/api/cron/jobs?page=${safePage}&page_size=${CRON_PAGE_SIZE}`,
      );
      const nextJobs = payload.items || [];
      const currentPage = payload.page || safePage;
      const totalPages = payload.total_pages || 1;
      const total = payload.total || 0;

      setJobsPage(currentPage);
      jobsPageRef.current = currentPage;
      setJobsTotalPages(Math.max(1, totalPages));
      setJobsTotal(Math.max(0, total));
      setJobs(nextJobs);

      const selected = selectedJobIdRef.current;
      if (!selected) return;
      const stillExists = nextJobs.some((job) => String(job.id || job.name || "") === selected);
      if (!stillExists) {
        selectedJobIdRef.current = "";
        setSelectedJobId("");
        setHistory([]);
        setHistoryError("");
        setDetail(null);
        setJobDetailModalOpen(false);
        setRawDetailModalOpen(false);
      }
    } catch {
      setError("Failed to load cron jobs.");
    } finally {
      setLoading(false);
    }
  }

  async function createJob() {
    setCreatePending(true);
    setCreateError("");
    try {
      const parsed = JSON.parse(createEditor) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setCreateError("Create payload must be a JSON object.");
        return;
      }
      await fetchJSON("/api/cron/jobs", {
        method: "POST",
        body: JSON.stringify(parsed),
      });
      setCreateModalOpen(false);
      setCreateEditor(buildDefaultCreatePayloadEditor());
      await loadJobs();
    } catch (error) {
      if (error instanceof SyntaxError) {
        setCreateError(`Invalid JSON: ${error.message}`);
      } else {
        setCreateError("Failed to create cron job.");
      }
    } finally {
      setCreatePending(false);
    }
  }

  async function loadJobHistory(rawId: string) {
    const requestId = ++historyRequestIdRef.current;
    setHistoryLoading(true);
    setHistoryError("");
    setHistory([]);
    try {
      const payload = await fetchJSON<CronHistoryItem[]>(
        `/api/cron/jobs/${encodeURIComponent(rawId)}/history`,
      );
      if (!isLatestCronDetailRequest(requestId, historyRequestIdRef.current)) return;
      setHistory(payload || []);
    } catch {
      if (!isLatestCronDetailRequest(requestId, historyRequestIdRef.current)) return;
      setHistory([]);
      setHistoryError("Failed to load run history.");
    } finally {
      if (!isLatestCronDetailRequest(requestId, historyRequestIdRef.current)) return;
      setHistoryLoading(false);
    }
  }

  async function loadJobDetail(rawId: string, options?: { closeRawModal?: boolean }) {
    const requestId = ++detailRequestIdRef.current;
    setDetailLoading(true);
    setDetailError("");
    setDetail(null);
    if (options?.closeRawModal !== false) {
      setRawDetailModalOpen(false);
    }
    setSaveError("");
    setSaveSuccess("");
    setRawSaveError("");
    setRawSaveSuccess("");
    setDetailDirty(false);
    setRawDetailDirty(false);
    try {
      const payload = await fetchJSON<Record<string, unknown>>(
        `/api/cron/jobs/${encodeURIComponent(rawId)}`,
      );
      if (!isLatestCronDetailRequest(requestId, detailRequestIdRef.current)) return;
      syncDetailEditors(payload);
    } catch {
      if (!isLatestCronDetailRequest(requestId, detailRequestIdRef.current)) return;
      setDetail(null);
      setDetailError("Failed to load cron job details.");
    } finally {
      if (!isLatestCronDetailRequest(requestId, detailRequestIdRef.current)) return;
      setDetailLoading(false);
    }
  }

  async function action(jobId: string, verb: "pause" | "resume" | "trigger") {
    const actionKey = `${jobId}:${verb}`;
    setActionError("");
    setPendingAction(actionKey);
    try {
      await fetchJSON(`/api/cron/jobs/${encodeURIComponent(jobId)}/${verb}`, { method: "POST" });
      await loadJobs();
      if (selectedJobIdRef.current !== jobId) return;
      await loadJobHistory(jobId);
      if (jobDetailModalOpen) {
        await loadJobDetail(jobId);
      }
    } catch {
      setActionError(`Failed to ${verb} cron job.`);
    } finally {
      setPendingAction("");
    }
  }

  async function removeJob(jobId: string, jobLabel: string) {
    const confirmed = window.confirm(`Delete cron job \"${jobLabel}\"?`);
    if (!confirmed) return;

    const actionKey = `${jobId}:delete`;
    setActionError("");
    setPendingAction(actionKey);
    try {
      await fetchJSON(`/api/cron/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
      await loadJobs();

      if (selectedJobIdRef.current === jobId) {
        selectedJobIdRef.current = "";
        setSelectedJobId("");
        setHistory([]);
        setHistoryError("");
        setDetail(null);
        setJobDetailModalOpen(false);
        setRawDetailModalOpen(false);
      }
    } catch {
      setActionError("Failed to delete cron job.");
    } finally {
      setPendingAction("");
    }
  }

  async function selectJob(rawId: string) {
    if (!rawId) return;
    selectedJobIdRef.current = rawId;
    setSelectedJobId(rawId);
    setActionError("");
    await loadJobHistory(rawId);
    if (jobDetailModalOpen) {
      await loadJobDetail(rawId);
    }
  }

  async function openDetail(rawId: string) {
    if (!rawId) return;
    if (selectedJobIdRef.current !== rawId) {
      selectedJobIdRef.current = rawId;
      setSelectedJobId(rawId);
      void loadJobHistory(rawId);
    }
    setJobDetailModalOpen(true);
    await loadJobDetail(rawId);
  }

  async function saveDetailUpdates() {
    if (!selectedJobId) return;
    setSavePending(true);
    setSaveError("");
    setSaveSuccess("");
    try {
      const parsed = JSON.parse(detailEditor) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setSaveError("Updates must be a JSON object.");
        return;
      }
      await fetchJSON(`/api/cron/jobs/${encodeURIComponent(selectedJobId)}`, {
        method: "PUT",
        body: JSON.stringify({ updates: parsed }),
      });
      await loadJobs();
      if (selectedJobIdRef.current === selectedJobId) {
        await Promise.all([loadJobHistory(selectedJobId), loadJobDetail(selectedJobId)]);
      }
      setSaveSuccess("Cron job updated.");
    } catch (error) {
      if (error instanceof SyntaxError) {
        setSaveError(`Invalid JSON: ${error.message}`);
      } else {
        setSaveError("Failed to update cron job.");
      }
    } finally {
      setSavePending(false);
    }
  }

  async function saveRawDetail() {
    if (!selectedJobId) return;
    setRawSavePending(true);
    setRawSaveError("");
    setRawSaveSuccess("");
    try {
      const parsed = JSON.parse(rawDetailEditor) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setRawSaveError("Raw detail payload must be a JSON object.");
        return;
      }
      const confirmed = window.confirm(
        "Save raw cron detail JSON? This directly edits the underlying job record and may affect scheduling, delivery targets, and runtime behavior.",
      );
      if (!confirmed) return;
      await fetchJSON(`/api/cron/jobs/${encodeURIComponent(selectedJobId)}/raw`, {
        method: "PUT",
        body: JSON.stringify({ job: parsed }),
      });
      await loadJobs();
      if (selectedJobIdRef.current === selectedJobId) {
        await Promise.all([
          loadJobHistory(selectedJobId),
          loadJobDetail(selectedJobId, { closeRawModal: false }),
        ]);
      }
      setRawSaveSuccess("Cron job raw detail updated.");
    } catch (error) {
      if (error instanceof SyntaxError) {
        setRawSaveError(`Invalid JSON: ${error.message}`);
      } else {
        setRawSaveError("Failed to update raw cron job detail.");
      }
    } finally {
      setRawSavePending(false);
    }
  }

  async function openSessionMessages(sessionId: string) {
    if (!sessionId) return;
    setSessionModalOpen(true);
    setSessionLoading(true);
    setSessionError("");
    setSessionMessages([]);
    setExpandedMessages({});
    try {
      const payload = await fetchJSON<SessionMessagesResponse>(
        `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
      );
      setSessionMessages(payload.messages || []);
    } catch {
      setSessionError("Failed to load session messages.");
    } finally {
      setSessionLoading(false);
    }
  }

  useEffect(() => {
    void loadJobs(1);
  }, []);

  useEffect(() => {
    if (
      !createModalOpen
      && !jobDetailModalOpen
      && !rawDetailModalOpen
      && !sessionModalOpen
    ) {
      return;
    }
    const handleKeydown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (createModalOpen) {
        setCreateModalOpen(false);
        return;
      }
      if (rawDetailModalOpen) {
        setRawDetailModalOpen(false);
        return;
      }
      if (sessionModalOpen) {
        setSessionModalOpen(false);
        return;
      }
      if (jobDetailModalOpen) {
        setJobDetailModalOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [createModalOpen, jobDetailModalOpen, rawDetailModalOpen, sessionModalOpen]);

  return (
    <section className="cron-workbench-page">
      {error ? (
        <StateBlock kind="error" title="Cron Load Failed" message={error} />
      ) : null}
      <div className="cron-stack-layout">
        <article className="detail-panel cron-top-pane">
          <div className="cron-pane-head">
            <h3>Jobs</h3>
            <div className="cron-pane-actions">
              <span className="status-badge">{jobsTotal} total</span>
              <button
                type="button"
                className="ghost-button cron-new-button"
                onClick={() => {
                  setCreateModalOpen(true);
                  setCreateError("");
                }}
              >
                New
              </button>
            </div>
          </div>
          {loading ? (
            <StateBlock kind="loading" title="Loading Cron Jobs" message="Fetching scheduler inventory from /api/cron/jobs." />
          ) : null}
          {!loading && jobs.length === 0 ? (
            <StateBlock kind="empty" title="No Cron Jobs Found" message="No jobs are currently registered for this profile." />
          ) : null}
          <div className="cron-jobs-scroll">
            <ul className="list-grid cron-jobs-grid">
              {jobs.map((job) => {
                const jobId = String(job.id || job.name || "");
                const paused = isJobPaused(job);
                const pauseVerb = paused ? "resume" : "pause";
                const pauseActionLabel = paused ? "Resume job" : "Pause job";
                return (
                  <li
                    key={jobId}
                    className={selectedJobId === jobId ? "clickable-card cron-job-card active" : "clickable-card cron-job-card"}
                    role="button"
                    tabIndex={0}
                    onClick={() => void selectJob(jobId)}
                    onKeyDown={(event) => {
                      if (!isCronActivationKey(event.key)) return;
                      event.preventDefault();
                      void selectJob(jobId);
                    }}
                  >
                    <div className="cron-job-card-head">
                      <strong>{job.name || job.id}</strong>
                      <span className={paused ? "status-badge cron-status paused" : "status-badge cron-status running"}>
                        {paused ? "Paused" : "Running"}
                      </span>
                    </div>
                    <div className="cron-job-info-grid">
                      <p>
                        <span>Schedule</span>
                        <strong>{renderSchedule(job.schedule)}</strong>
                      </p>
                      <p>
                        <span>Model</span>
                        <strong>{job.model || "--"}</strong>
                      </p>
                      <p>
                        <span>Last Run</span>
                        <strong>{formatDateTime(job.last_run_at)}</strong>
                      </p>
                      <p>
                        <span>Next Run</span>
                        <strong>{formatDateTime(job.next_run_at)}</strong>
                      </p>
                      <p>
                        <span>Deliver</span>
                        <strong>{job.deliver || "--"}</strong>
                      </p>
                      <p>
                        <span>Repeat</span>
                        <strong>{formatRepeat(job)}</strong>
                      </p>
                    </div>
                    <div className="cron-card-actions">
                      <button
                        type="button"
                        className="ghost-button cron-icon-button"
                        aria-label="Run now"
                        disabled={pendingAction === `${jobId}:trigger`}
                        onClick={(event) => {
                          event.stopPropagation();
                          void action(jobId, "trigger");
                        }}
                      >
                        {pendingAction === `${jobId}:trigger` ? "…" : "▶"}
                      </button>
                      <button
                        type="button"
                        className="ghost-button cron-icon-button"
                        aria-label={pauseActionLabel}
                        disabled={pendingAction === `${jobId}:${pauseVerb}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          void action(jobId, pauseVerb);
                        }}
                      >
                        {pendingAction === `${jobId}:${pauseVerb}` ? "…" : paused ? "⏵" : "⏸"}
                      </button>
                      <button
                        type="button"
                        className="ghost-button cron-icon-button cron-icon-button-detail"
                        aria-label="Open detail"
                        onClick={(event) => {
                          event.stopPropagation();
                          void openDetail(jobId);
                        }}
                      >
                        Detail
                      </button>
                      <button
                        type="button"
                        className="ghost-button cron-icon-button"
                        aria-label="Delete job"
                        disabled={pendingAction === `${jobId}:delete`}
                        onClick={(event) => {
                          event.stopPropagation();
                          void removeJob(jobId, String(job.name || job.id || jobId));
                        }}
                      >
                        {pendingAction === `${jobId}:delete` ? "…" : "✕"}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
          {actionError ? <p className="error-text">{actionError}</p> : null}
          <div className="cron-pagination-row">
            <button
              type="button"
              className="ghost-button cron-page-button"
              disabled={loading || jobsPage <= 1}
              onClick={() => void loadJobs(jobsPage - 1)}
            >
              Prev
            </button>
            <span className="status-badge">Page {jobsPage} / {jobsTotalPages}</span>
            <button
              type="button"
              className="ghost-button cron-page-button"
              disabled={loading || jobsPage >= jobsTotalPages}
              onClick={() => void loadJobs(jobsPage + 1)}
            >
              Next
            </button>
          </div>
        </article>

        <article className="detail-panel cron-history-pane">
          <div className="cron-pane-head">
            <h3>Run History</h3>
            <span className="status-badge">{history.length} items</span>
          </div>
          {!selectedJobId ? (
            <StateBlock kind="empty" title="No Job Selected" message="Select a job card above to inspect run sessions." />
          ) : null}
          {selectedJobId && historyLoading ? (
            <StateBlock kind="loading" title="Loading Run History" message="Fetching /api/cron/jobs/{job_id}/history." />
          ) : null}
          {selectedJobId && historyError ? (
            <StateBlock kind="error" title="History Unavailable" message={historyError} />
          ) : null}
          {selectedJobId && !historyLoading && !historyError && history.length === 0 ? (
            <StateBlock kind="empty" title="No History Yet" message="This job has no recorded sessions." />
          ) : null}
          {!historyLoading && !historyError && history.length > 0 ? (
            <div className="cron-history-scroll">
              <ul className="list-grid cron-history-list">
                {history.map((item) => (
                  <li key={`${item.session_id}-${String(item.started_at || "none")}`}>
                    <button
                      type="button"
                      className="cron-history-row"
                      onClick={() => void openSessionMessages(item.session_id)}
                    >
                      <span className="cron-history-arrow">›</span>
                      <span className="cron-history-main">
                        <span className="cron-history-title">
                          {selectedJob?.name || selectedJobId} — {formatDateTime(item.started_at)}
                        </span>
                        <span className="cron-history-session">{item.session_id}</span>
                      </span>
                      <span className="cron-history-meta">
                        <span>{item.messages ?? 0} msg</span>
                        <span>{item.tokens ?? 0} tok</span>
                        <span>{item.status || "completed"}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </article>
      </div>

      <div
        className={createModalOpen ? "cron-create-modal-overlay active" : "cron-create-modal-overlay"}
        onClick={() => setCreateModalOpen(false)}
        aria-hidden={!createModalOpen}
      >
        <div
          className="cron-detail-modal"
          role="dialog"
          aria-modal="true"
          aria-label="Create cron job"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="cron-detail-modal-header">
            <h4>Create Job</h4>
            <button
              type="button"
              className="ghost-button cron-raw-modal-close"
              onClick={() => setCreateModalOpen(false)}
              aria-label="Close create job modal"
            >
              ×
            </button>
          </div>
          <div className="cron-detail-modal-body">
            <div className="cron-detail-editor-wrap">
              <label className="memory-editor-label" htmlFor="cron-create-editor">
                Job Create JSON
              </label>
              <textarea
                id="cron-create-editor"
                className="cron-create-editor"
                value={createEditor}
                onChange={(event) => {
                  setCreateEditor(event.target.value);
                  setCreateError("");
                }}
                rows={12}
              />
              <div className="button-row cron-action-zone">
                <button
                  type="button"
                  onClick={() => void createJob()}
                  disabled={createPending}
                >
                  {createPending ? "Creating..." : "Create Job"}
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  disabled={createPending}
                  onClick={() => {
                    setCreateEditor(buildDefaultCreatePayloadEditor());
                    setCreateError("");
                  }}
                >
                  Reset Template
                </button>
              </div>
              {createError ? <p className="error-text">{createError}</p> : null}
            </div>
          </div>
        </div>
      </div>

      <div
        className={jobDetailModalOpen ? "cron-detail-modal-overlay active" : "cron-detail-modal-overlay"}
        onClick={() => setJobDetailModalOpen(false)}
        aria-hidden={!jobDetailModalOpen}
      >
        <div
          className="cron-detail-modal"
          role="dialog"
          aria-modal="true"
          aria-label="Cron job detail"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="cron-detail-modal-header">
            <h4>Cron Job Detail</h4>
            <button
              type="button"
              className="ghost-button cron-raw-modal-close"
              onClick={() => setJobDetailModalOpen(false)}
              aria-label="Close cron detail modal"
            >
              ×
            </button>
          </div>
          <div className="cron-detail-modal-body">
            {!selectedJobId ? (
              <StateBlock kind="empty" title="No Job Selected" message="Select a job card first." />
            ) : null}
            {detailLoading ? (
              <StateBlock kind="loading" title="Loading Detail" message="Fetching selected job payload." />
            ) : null}
            {detailError ? (
              <StateBlock kind="error" title="Detail Unavailable" message={detailError} />
            ) : null}
            {detail ? (
              <div className="cron-detail-editor-wrap">
                <label className="memory-editor-label" htmlFor="cron-detail-editor">
                  Editable Updates JSON
                </label>
                <textarea
                  id="cron-detail-editor"
                  className="cron-detail-editor"
                  value={detailEditor}
                  onChange={(event) => {
                    setDetailEditor(event.target.value);
                    setDetailDirty(true);
                    setSaveError("");
                    setSaveSuccess("");
                  }}
                  rows={18}
                />
                <div className="button-row cron-action-zone">
                  <button
                    type="button"
                    onClick={() => void saveDetailUpdates()}
                    disabled={savePending || !detailDirty}
                  >
                    {savePending ? "Saving..." : "Update"}
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    disabled={savePending}
                    onClick={() => {
                      const editable = buildEditableCronUpdates(detail);
                      setDetailEditor(JSON.stringify(editable, null, 2));
                      setDetailDirty(false);
                      setSaveError("");
                      setSaveSuccess("");
                    }}
                  >
                    Reset
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => setRawDetailModalOpen(true)}
                  >
                    Raw JSON
                  </button>
                </div>
                {saveError ? <p className="error-text">{saveError}</p> : null}
                {saveSuccess ? <p>{saveSuccess}</p> : null}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {detail ? (
        <div
          className={rawDetailModalOpen ? "cron-raw-modal-overlay active" : "cron-raw-modal-overlay"}
          onClick={() => setRawDetailModalOpen(false)}
          aria-hidden={!rawDetailModalOpen}
        >
          <div
            className="cron-raw-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Raw cron detail json"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="cron-raw-modal-header">
              <h4>Raw Detail JSON</h4>
              <button
                type="button"
                className="ghost-button cron-raw-modal-close"
                onClick={() => setRawDetailModalOpen(false)}
                aria-label="Close raw detail modal"
              >
                ×
              </button>
            </div>
            <div className="cron-raw-modal-body">
              <div className="cron-detail-editor-wrap">
                <p className="subtle-copy">
                  Directly editing the raw job record can change scheduling, delivery targets, and runtime behavior.
                </p>
                <textarea
                  id="cron-raw-editor"
                  className="cron-detail-editor cron-raw-editor"
                  value={rawDetailEditor}
                  onChange={(event) => handleRawEditorValueChange(event.target.value)}
                  onInput={(event) => handleRawEditorValueChange((event.target as HTMLTextAreaElement).value)}
                  rows={20}
                />
                <div className="button-row cron-action-zone">
                  <button
                    type="button"
                    onClick={() => void saveRawDetail()}
                    disabled={rawSavePending || !rawDetailDirty}
                  >
                    {rawSavePending ? "Saving..." : "Save Raw JSON"}
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    disabled={rawSavePending}
                    onClick={() => {
                      if (!detail) return;
                      setRawDetailEditor(JSON.stringify(detail, null, 2));
                      setRawDetailDirty(false);
                      setRawSaveError("");
                      setRawSaveSuccess("");
                    }}
                  >
                    Reset
                  </button>
                </div>
                {rawSaveError ? <p className="error-text">{rawSaveError}</p> : null}
                {rawSaveSuccess ? <p>{rawSaveSuccess}</p> : null}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      <div
        className={sessionModalOpen ? "cron-session-modal-overlay active" : "cron-session-modal-overlay"}
        onClick={() => setSessionModalOpen(false)}
        aria-hidden={!sessionModalOpen}
      >
        <div
          className="cron-session-modal"
          role="dialog"
          aria-modal="true"
          aria-label="Session messages"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="cron-detail-modal-header">
            <h4>Session Messages</h4>
            <button
              type="button"
              className="ghost-button cron-raw-modal-close"
              onClick={() => setSessionModalOpen(false)}
              aria-label="Close session modal"
            >
              ×
            </button>
          </div>
          <div className="cron-session-modal-body">
            {sessionLoading ? (
              <StateBlock kind="loading" title="Loading Session Messages" message="Fetching session messages..." />
            ) : null}
            {sessionError ? (
              <StateBlock kind="error" title="Session Detail Unavailable" message={sessionError} />
            ) : null}
            {!sessionLoading && !sessionError && (() => {
              const visible = sessionMessages.filter((msg) => (msg.role || "").toLowerCase() !== "system");
              if (visible.length === 0) {
                return <StateBlock kind="empty" title="No Messages" message="No messages were returned for this session." />;
              }
              const rendered = visible.map((msg) => ({ msg, rendered: formatMessagePayload(msg) }));
              return (
                <div className="detail-messages sessions-message-stream">
                  {rendered.map(({ msg, rendered }, index) => (
                    <div
                      key={`${msg.id || "msg"}-${msg.timestamp || "t"}-${index}`}
                      className="detail-message"
                    >
                      <p>
                        <strong>{msg.role || "unknown"}</strong>
                      </p>
                      {(() => {
                        const role = String(msg.role || "").toLowerCase();
                        const messageKey = `session:${msg.id || index}:${msg.timestamp || ""}`;
                        const collapseLimit = role === "tool" ? 120 : role === "user" ? 500 : 0;
                        const shouldCollapse = collapseLimit > 0 && rendered.length > collapseLimit;
                        const isExpanded = Boolean(expandedMessages[messageKey]);
                        const preview = `${rendered.slice(0, collapseLimit)}...`;
                        return (
                          <>
                            <pre>{shouldCollapse && !isExpanded ? preview : rendered}</pre>
                            {shouldCollapse ? (
                              <button
                                type="button"
                                className="ghost-button tool-message-toggle"
                                onClick={() =>
                                  setExpandedMessages((current) => ({
                                    ...current,
                                    [messageKey]: !current[messageKey],
                                  }))
                                }
                                aria-label={isExpanded ? "Collapse message" : "Expand message"}
                              >
                                {isExpanded ? "Collapse" : "Expand"}
                              </button>
                            ) : null}
                          </>
                        );
                      })()}
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
        </div>
      </div>
    </section>
  );
}
