import { FitAddon } from "@xterm/addon-fit";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { WebglAddon } from "@xterm/addon-webgl";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { fetchJSON } from "../lib/api";
import {
  buildEventsUrl,
  buildGatewayUrl,
  buildPtyUrl,
  CHAT_SESSION_CHANGED_EVENT,
  clearStoredChatResumeSession,
  generateChannelId,
  getStoredChatResumeSession,
  setStoredChatResumeSession,
} from "../lib/chat";

type ChatStatus = {
  embedded_chat: boolean;
  ready: boolean;
};

type LatestDescendant = {
  session_id: string;
};

type ChatEventPayload = {
  method?: string;
  params?: {
    payload?: { session_id?: string };
    session_id?: string;
    type?: string;
  };
  payload?: { session_id?: string };
  session_id?: string;
};

export function resolveInitialResumeSession(requestedResume: string, cachedResume: string): string | null {
  const requested = requestedResume.trim();
  if (requested) return requested;
  const cached = cachedResume.trim();
  return cached || null;
}

export function extractSessionIdFromChatEvent(raw: string): string {
  try {
    const payload = JSON.parse(raw) as ChatEventPayload;
    const rpcNested =
      payload.params && payload.params.payload && typeof payload.params.payload.session_id === "string"
        ? payload.params.payload.session_id.trim()
        : "";
    if (rpcNested) return rpcNested;
    const direct = typeof payload.session_id === "string" ? payload.session_id.trim() : "";
    if (direct) return direct;
    const nested =
      payload.payload && typeof payload.payload.session_id === "string"
        ? payload.payload.session_id.trim()
        : "";
    return nested;
  } catch {
    return "";
  }
}

const TERMINAL_THEME = {
  background: "#0d2626",
  foreground: "#f0e6d2",
  cursor: "#f0e6d2",
  cursorAccent: "#0d2626",
  selectionBackground: "#f0e6d244",
};

export function ChatPage() {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedResume = (searchParams.get("resume") || "").trim();
  const [launchResume, setLaunchResume] = useState<string | null>(() => {
    return resolveInitialResumeSession(requestedResume, getStoredChatResumeSession());
  });
  const [sessionSeed, setSessionSeed] = useState(0);
  const [chatSessionId, setChatSessionId] = useState<string>(() => getStoredChatResumeSession().trim());
  const channel = useMemo(() => generateChannelId(), [launchResume, sessionSeed]);
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  function recordActiveSession(sessionId: string) {
    const cleaned = sessionId.trim();
    if (!cleaned) return;
    setChatSessionId(cleaned);
    setStoredChatResumeSession(cleaned);
  }

  function removeResumeFromUrl() {
    if (!searchParams.has("resume")) return;
    const next = new URLSearchParams(searchParams);
    next.delete("resume");
    setSearchParams(next, { replace: true });
  }

  function handleStartNewSession() {
    clearStoredChatResumeSession();
    setChatSessionId("");
    setLaunchResume(null);
    setBanner(null);
    setSessionSeed((value) => value + 1);
    removeResumeFromUrl();
  }

  useEffect(() => {
    let cancelled = false;

    async function loadStatus() {
      try {
        const payload = await fetchJSON<ChatStatus>("/api/chat/status");
        if (!cancelled) setStatus(payload);
      } catch {
        if (!cancelled) setBanner("Failed to query chat runtime status.");
      }
    }

    void loadStatus();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!requestedResume) return;
    recordActiveSession(requestedResume);
    if (requestedResume === launchResume) return;
    setLaunchResume(requestedResume);
  }, [requestedResume, launchResume]);

  useEffect(() => {
    const resumeId = (launchResume || "").trim();
    if (!resumeId) return;
    let cancelled = false;

    async function syncLatest() {
      try {
        const payload = await fetchJSON<LatestDescendant>(
          `/api/sessions/${encodeURIComponent(resumeId)}/latest-descendant`,
        );
        if (cancelled) return;
        if (payload.session_id && payload.session_id !== resumeId) {
          setLaunchResume(payload.session_id);
          recordActiveSession(payload.session_id);
          if (searchParams.get("resume") !== payload.session_id) {
            const next = new URLSearchParams(searchParams);
            next.set("resume", payload.session_id);
            setSearchParams(next, { replace: true });
          }
        }
      } catch {
        // Best effort; stale session should not block chat launch.
      }
    }

    void syncLatest();
    return () => {
      cancelled = true;
    };
  }, [launchResume, searchParams, setSearchParams]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const sync = () => setChatSessionId(getStoredChatResumeSession().trim());
    window.addEventListener(CHAT_SESSION_CHANGED_EVENT, sync as EventListener);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(CHAT_SESSION_CHANGED_EVENT, sync as EventListener);
      window.removeEventListener("storage", sync);
    };
  }, []);

  useEffect(() => {
    if (!status?.ready) {
      return;
    }
    const host = hostRef.current;
    if (!host) return;

    const term = new Terminal({
      allowProposedApi: true,
      cursorBlink: true,
      fontFamily:
        "'JetBrains Mono', 'Cascadia Mono', 'Fira Code', 'Source Code Pro', Menlo, Consolas, monospace",
      fontSize: 12,
      lineHeight: 1.08,
      scrollback: 5000,
      theme: TERMINAL_THEME,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new Unicode11Addon());
    term.loadAddon(new WebLinksAddon());
    try {
      term.loadAddon(new WebglAddon());
    } catch {
      // WebGL can fail on remote/virtualized GPUs; fallback renderer is fine.
    }

    term.open(host);
    fit.fit();
    term.focus();

    const ptyUrl = buildPtyUrl({ channel, resume: launchResume });
    const ptyWs = new WebSocket(ptyUrl);
    ptyWs.binaryType = "arraybuffer";
    wsRef.current = ptyWs;
    termRef.current = term;

    ptyWs.onopen = () => {
      term.writeln("\x1b[2mConnected to WORKAGENT chat PTY.\x1b[0m");
      const { cols, rows } = term;
      ptyWs.send(`\x1b[RESIZE:${cols};${rows}]`);
    };
    ptyWs.onmessage = (event) => {
      if (typeof event.data === "string") {
        term.write(event.data);
        return;
      }
      term.write(new Uint8Array(event.data));
    };
    ptyWs.onerror = () => {
      term.writeln("\r\n\x1b[31mPTY websocket error.\x1b[0m");
    };
    ptyWs.onclose = (event) => {
      term.writeln("\r\n\x1b[33mPTY connection closed.\x1b[0m");
      if (event.code === 4401) {
        setBanner("Session token expired. Please sign in again.");
      } else if (event.code === 4403) {
        setBanner("Embedded chat is disabled on the server. Start with --tui.");
      }
    };

    const disposeData = term.onData((text) => {
      if (ptyWs.readyState === WebSocket.OPEN) {
        ptyWs.send(text);
      }
    });
    const disposeResize = term.onResize(({ cols, rows }) => {
      if (ptyWs.readyState === WebSocket.OPEN) {
        ptyWs.send(`\x1b[RESIZE:${cols};${rows}]`);
      }
    });
    const resizeObserver = new ResizeObserver(() => {
      fit.fit();
    });
    resizeObserver.observe(host);

    const gatewayWs = new WebSocket(buildGatewayUrl(channel));
    const eventsWs = new WebSocket(buildEventsUrl(channel));
    eventsWs.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      const nextSessionId = extractSessionIdFromChatEvent(event.data);
      if (!nextSessionId) return;
      recordActiveSession(nextSessionId);
    };
    gatewayWs.onclose = (event) => {
      if (event.code === 4401) {
        setBanner("Session token expired. Please sign in again.");
      } else if (event.code === 4403) {
        setBanner("Embedded chat is disabled on the server. Start with --tui.");
      }
    };

    return () => {
      disposeData.dispose();
      disposeResize.dispose();
      resizeObserver.disconnect();
      eventsWs.close();
      gatewayWs.close();
      ptyWs.close();
      term.dispose();
      wsRef.current = null;
      termRef.current = null;
    };
  }, [channel, launchResume, status?.ready]);

  return (
    <section className="chat-workbench-page">
      <div className="chat-workbench">
        <article className="detail-panel chat-terminal-zone chat-terminal-pane">
          <header className="chat-zone-header">
            <h3>{`Terminal - ${chatSessionId || "new"}`}</h3>
            <button
              type="button"
              className="ghost-button chat-new-session-button"
              onClick={handleStartNewSession}
              title="Start new session"
            >
              New
            </button>
          </header>
          {!status?.ready ? (
            <p className="subtle-copy">
              Embedded chat is disabled. Start WORKAGENT with `hermes workagent --tui`.
            </p>
          ) : null}
          {banner ? <p className="error-text">{banner}</p> : null}
          <div className="terminal-host" ref={hostRef} />
        </article>
      </div>
    </section>
  );
}
