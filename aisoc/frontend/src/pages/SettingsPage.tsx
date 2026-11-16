import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import { PageMissionHeader } from "../components/PageMissionHeader";
import { fetchJSON } from "../lib/api";
import { setStoredUser } from "../lib/auth";
import { useCurrentUser, useSetCurrentUser } from "../lib/authContext";
import type { AuthenticatedUser } from "../types";

const RESTART_CONFIRMATION_PHRASE = "RESTART AISOC";
const DEFAULT_RECOVERY_POLL_MS = 1_000;

interface RuntimeStatus {
  status?: string;
  model?: string;
  provider?: string;
  profile?: string;
  uptime_seconds?: number;
}

interface RestartResponse {
  accepted: boolean;
  already_requested: boolean;
  service: string;
  pid: number;
}

interface SettingsPageProps {
  reloadPage?: () => void;
  recoveryPollMs?: number;
}

function reloadWindow(): void {
  window.location.reload();
}

function displayValue(value: string | undefined): string {
  return value?.trim() || "Unavailable";
}

function formatUptime(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "Unavailable";
  const totalMinutes = Math.floor(seconds / 60);
  const days = Math.floor(totalMinutes / 1_440);
  const hours = Math.floor((totalMinutes % 1_440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function SettingsPage({
  reloadPage = reloadWindow,
  recoveryPollMs = DEFAULT_RECOVERY_POLL_MS,
}: SettingsPageProps) {
  const currentUser = useCurrentUser();
  const setCurrentUser = useSetCurrentUser();
  const isAdmin = Boolean(currentUser?.is_admin);
  const [displayName, setDisplayName] = useState(currentUser?.display_name || "");
  const [profileSubmitting, setProfileSubmitting] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [profileSaved, setProfileSaved] = useState(false);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [runtimeError, setRuntimeError] = useState("");
  const [confirmationStage, setConfirmationStage] = useState<"danger" | "phrase" | null>(null);
  const [confirmationPhrase, setConfirmationPhrase] = useState("");
  const [restartError, setRestartError] = useState("");
  const [restartSubmitting, setRestartSubmitting] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [restartPid, setRestartPid] = useState<number | null>(null);
  const restartButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const phraseInputRef = useRef<HTMLInputElement>(null);
  const dialogWasOpenRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function loadRuntime(): Promise<void> {
      try {
        const status = await fetchJSON<RuntimeStatus>("/api/overview/status");
        if (cancelled) return;
        setRuntime(status);
        setRuntimeError("");
      } catch {
        if (cancelled) return;
        setRuntimeError("Runtime status is currently unavailable.");
      }
    }

    void loadRuntime();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!restarting) return;

    let cancelled = false;
    let timer: number | undefined;
    let outageObserved = false;

    function scheduleNextCheck(): void {
      timer = window.setTimeout(() => void checkRecovery(), recoveryPollMs);
    }

    async function checkRecovery(): Promise<void> {
      try {
        const health = await fetchJSON<{ status: string; pid?: number }>("/health", {}, false);
        if (cancelled) return;
        if (restartPid !== null && typeof health.pid === "number") {
          if (health.pid !== restartPid) {
            reloadPage();
            return;
          }
          scheduleNextCheck();
          return;
        }
        if (outageObserved) {
          reloadPage();
          return;
        }
        scheduleNextCheck();
      } catch {
        if (!cancelled) {
          outageObserved = true;
          scheduleNextCheck();
        }
      }
    }

    scheduleNextCheck();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [recoveryPollMs, reloadPage, restartPid, restarting]);

  useEffect(() => {
    if (confirmationStage === null) {
      if (dialogWasOpenRef.current && !restarting) {
        restartButtonRef.current?.focus();
      }
      dialogWasOpenRef.current = false;
      return;
    }

    dialogWasOpenRef.current = true;
    if (confirmationStage === "phrase") {
      phraseInputRef.current?.focus();
    } else {
      dialogRef.current?.querySelector<HTMLElement>("[data-initial-focus]")?.focus();
    }
  }, [confirmationStage, restarting]);

  function closeConfirmation(): void {
    if (restartSubmitting || restarting) return;
    setConfirmationStage(null);
    setConfirmationPhrase("");
  }

  async function requestRestart(): Promise<void> {
    if (confirmationPhrase !== RESTART_CONFIRMATION_PHRASE || restartSubmitting || restarting) return;

    setRestartSubmitting(true);
    setRestartError("");
    try {
      const result = await fetchJSON<RestartResponse>("/api/system/restart", { method: "POST" });
      setRestartPid(result.pid);
      setRestarting(true);
      setConfirmationStage(null);
      setConfirmationPhrase("");
    } catch {
      setRestartError("Restart request failed. AISOC is still running.");
      setRestartSubmitting(false);
    }
  }

  async function saveProfile(): Promise<void> {
    const trimmed = displayName.trim();
    if (!trimmed || profileSubmitting) return;

    setProfileSubmitting(true);
    setProfileError("");
    try {
      const updated = await fetchJSON<AuthenticatedUser>("/api/auth/profile", {
        method: "PUT",
        body: JSON.stringify({ display_name: trimmed }),
      });
      setStoredUser(updated);
      setCurrentUser(updated);
      setDisplayName(updated.display_name);
      setProfileSaved(true);
      window.setTimeout(() => setProfileSaved(false), 2_000);
    } catch {
      setProfileError("Could not save nickname. Try again.");
    } finally {
      setProfileSubmitting(false);
    }
  }

  function handleDialogKeyDown(event: ReactKeyboardEvent<HTMLElement>): void {
    if (event.key === "Escape") {
      event.preventDefault();
      closeConfirmation();
      return;
    }
    if (event.key !== "Tab") return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !dialog.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <section className="settings-workbench-page">
      <PageMissionHeader
        title="Settings"
        subtitle="Inspect this AISOC runtime and perform guarded service operations."
        status={
          <span className={`status-badge ${restarting ? "status-restarting" : "status-live"}`}>
            {restarting ? "Restarting" : "Live"}
          </span>
        }
      />

      <article className="detail-panel settings-profile-panel" aria-labelledby="profile-title">
        <div className="settings-panel-heading">
          <div>
            <p className="brand-kicker">Identity</p>
            <h3 id="profile-title">Profile</h3>
          </div>
        </div>
        <label className="settings-confirm-label" htmlFor="profile-display-name">
          Nickname
          <input
            id="profile-display-name"
            type="text"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            maxLength={40}
            autoComplete="off"
            disabled={profileSubmitting}
          />
        </label>
        {profileError ? <p className="error-text" role="alert">{profileError}</p> : null}
        <div className="settings-confirm-actions">
          <button
            type="button"
            className="ghost-button"
            onClick={() => void saveProfile()}
            disabled={profileSubmitting || !displayName.trim()}
          >
            {profileSubmitting ? "Saving…" : profileSaved ? "Saved" : "Save nickname"}
          </button>
        </div>
      </article>

      <article className="detail-panel settings-runtime-panel" aria-labelledby="runtime-status-title">
        <div className="settings-panel-heading">
          <div>
            <p className="brand-kicker">System telemetry</p>
            <h3 id="runtime-status-title">Runtime Status</h3>
          </div>
          <span className="settings-panel-code">AISOC // LOCAL</span>
        </div>
        {runtimeError ? <p className="error-text">{runtimeError}</p> : null}
        {!runtime && !runtimeError ? <p className="subtle-copy">Loading runtime status…</p> : null}
        {runtime ? (
          <dl className="settings-runtime-grid">
            <div>
              <dt>Service</dt>
              <dd>{displayValue(runtime.status)}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{displayValue(runtime.model)}</dd>
            </div>
            <div>
              <dt>Provider</dt>
              <dd>{displayValue(runtime.provider)}</dd>
            </div>
            <div>
              <dt>Profile</dt>
              <dd>{displayValue(runtime.profile)}</dd>
            </div>
            <div>
              <dt>Uptime</dt>
              <dd>{formatUptime(runtime.uptime_seconds)}</dd>
            </div>
          </dl>
        ) : null}
      </article>

      {isAdmin ? (
        <article className="detail-panel settings-danger-panel" aria-labelledby="dangerous-actions-title">
          <div className="settings-danger-marker" aria-hidden="true">!</div>
          <div className="settings-danger-copy">
            <p className="brand-kicker">Restricted control</p>
            <h3 id="dangerous-actions-title">Dangerous Actions</h3>
            <p className="subtle-copy">
              Restarting temporarily disconnects this workbench and interrupts active operations.
            </p>
            {restartError ? <p className="error-text" role="alert">{restartError}</p> : null}
            {restarting ? (
              <p className="settings-restart-state" role="status">
                Restarting AISOC. Waiting for the public health check to recover…
              </p>
            ) : null}
          </div>
          <button
            ref={restartButtonRef}
            type="button"
            className="danger-button settings-restart-button"
            onClick={() => setConfirmationStage("danger")}
            disabled={restartSubmitting || restarting}
          >
            {restarting ? "Restarting…" : "Restart AISOC"}
          </button>
        </article>
      ) : null}

      {confirmationStage ? (
        <div className="settings-confirm-overlay" onMouseDown={(event) => {
          if (event.currentTarget === event.target) closeConfirmation();
        }}>
          <section
            ref={dialogRef}
            className="settings-confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="restart-dialog-title"
            aria-describedby="restart-dialog-description"
            onKeyDown={handleDialogKeyDown}
          >
            <p className="brand-kicker">Dangerous operation</p>
            <h3 id="restart-dialog-title">
              {confirmationStage === "danger" ? "Service interruption" : "Final confirmation"}
            </h3>
            {confirmationStage === "danger" ? (
              <>
                <p id="restart-dialog-description" className="subtle-copy">
                  AISOC will stop accepting requests while the process restarts. Active work may be interrupted.
                </p>
                <div className="settings-confirm-actions">
                  <button type="button" className="ghost-button" onClick={closeConfirmation} data-initial-focus>
                    Cancel
                  </button>
                  <button type="button" className="danger-button" onClick={() => setConfirmationStage("phrase")}>
                    Continue
                  </button>
                </div>
              </>
            ) : (
              <>
                <p id="restart-dialog-description" className="subtle-copy">
                  Type <code>{RESTART_CONFIRMATION_PHRASE}</code> exactly to authorize the restart.
                </p>
                <label className="settings-confirm-label">
                  Confirmation phrase
                  <input
                    ref={phraseInputRef}
                    type="text"
                    value={confirmationPhrase}
                    onChange={(event) => setConfirmationPhrase(event.target.value)}
                    aria-label="Restart confirmation phrase"
                    autoComplete="off"
                    spellCheck={false}
                    disabled={restartSubmitting}
                  />
                </label>
                <div className="settings-confirm-actions">
                  <button type="button" className="ghost-button" onClick={closeConfirmation} disabled={restartSubmitting}>
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="danger-button"
                    onClick={() => void requestRestart()}
                    disabled={confirmationPhrase !== RESTART_CONFIRMATION_PHRASE || restartSubmitting}
                  >
                    {restartSubmitting ? "Requesting…" : "Confirm restart"}
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      ) : null}
    </section>
  );
}
