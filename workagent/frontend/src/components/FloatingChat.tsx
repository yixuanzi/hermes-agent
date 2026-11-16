// workagent/frontend/src/components/FloatingChat.tsx
import ReactMarkdown from "react-markdown";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAgentChat, formatToolDuration, type ChatMessage } from "../lib/useAgentChat";

type ToolMsg = Extract<ChatMessage, { role: "tool" }>;

/** Group consecutive tool messages into runs, keeping non-tool messages as-is.
 *  Swaps [agent, tool-group] → [tool-group, agent] so tools appear above the response. */
function groupMessages(messages: ChatMessage[]): (ChatMessage | ToolMsg[])[] {
  const raw: (ChatMessage | ToolMsg[])[] = [];
  let toolBuf: ToolMsg[] = [];
  for (const m of messages) {
    if (m.role === "tool") {
      toolBuf.push(m);
    } else {
      if (toolBuf.length) { raw.push(toolBuf); toolBuf = []; }
      raw.push(m);
    }
  }
  if (toolBuf.length) raw.push(toolBuf);

  // Swap: when agent message is immediately followed by a tool group, reorder
  // so tools render above the agent response text. Applies regardless of done
  // state so the order stays consistent after message.complete.
  const result: (ChatMessage | ToolMsg[])[] = [];
  for (let i = 0; i < raw.length; i++) {
    const cur = raw[i];
    const next = raw[i + 1];
    if (
      !Array.isArray(cur) && cur.role === "agent"
      && Array.isArray(next)
    ) {
      result.push(next);
      result.push(cur);
      i++;
    } else {
      result.push(cur);
    }
  }
  return result;
}
import "./FloatingChat.css";

const LONG_INPUT_THRESHOLD = 10000;
type ConfirmAction = "new" | "switch" | "closeTab";

export function FloatingChat() {
  const chat = useAgentChat();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>("new");
  const [pendingSwitchDbId, setPendingSwitchDbId] = useState<string | null>(null);
  const [pendingCloseDbId, setPendingCloseDbId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const isStreaming = chat.state.phase === "streaming";

  // Auto-reconnect on mount if there's a saved session
  useEffect(() => {
    if (chat.state.phase === "connecting" && chat.state.sessionId) {
      chat.connect();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.state.messages.length]);

  const requestConfirmOrAction = useCallback((action: ConfirmAction, onConfirmed: () => void) => {
    if (isStreaming) {
      setConfirmAction(action);
      setShowConfirm(true);
      return;
    }
    onConfirmed();
  }, [isStreaming]);

  const handleIconClick = useCallback(() => {
    if (drawerOpen) {
      setDrawerOpen(false);
      return;
    }
    setDrawerOpen(true);
    if (chat.state.phase === "disconnected") {
      chat.connect();
    }
  }, [drawerOpen, chat]);

  const handleClose = useCallback(() => {
    setDrawerOpen(false);
  }, []);

  const handleNew = useCallback(() => {
    requestConfirmOrAction("new", () => chat.startNewSession());
  }, [chat, requestConfirmOrAction]);

  const handleTabSwitch = useCallback((dbId: string) => {
    requestConfirmOrAction("switch", () => chat.switchToTab(dbId));
    if (isStreaming) setPendingSwitchDbId(dbId);
  }, [isStreaming, chat, requestConfirmOrAction]);

  const handleKeepRunning = useCallback(() => {
    setShowConfirm(false);
    setPendingSwitchDbId(null);
    setPendingCloseDbId(null);
  }, []);

  const handleInterruptConfirm = useCallback(() => {
    setShowConfirm(false);
    if (confirmAction === "closeTab" && pendingCloseDbId) {
      if (isStreaming) { chat.interrupt(); chat.disconnect(); }
      chat.closeTab(pendingCloseDbId);
      setPendingCloseDbId(null);
    } else if (confirmAction === "new") {
      chat.interrupt();
      chat.disconnect();
      chat.startNewSession();
    } else if (confirmAction === "switch" && pendingSwitchDbId) {
      chat.interrupt();
      chat.disconnect();
      chat.switchToTab(pendingSwitchDbId);
      setPendingSwitchDbId(null);
    }
  }, [chat, confirmAction, pendingSwitchDbId, pendingCloseDbId, isStreaming]);

  const handleSend = useCallback(() => {
    if (!input.trim() || isStreaming) return;
    chat.send(input.trim());
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "36px";
  }, [input, isStreaming, chat]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handleTextareaInput = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "36px";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }, []);

  const isAuthError = chat.state.error?.includes("expired") || chat.state.error?.includes("4401") || chat.state.error?.includes("403");

  return (
    <>
      {/* Drawer */}
      <div className={`widget-drawer ${drawerOpen ? "widget-drawer-open" : ""} ${fullscreen ? "widget-drawer-fullscreen" : ""}`}>
        <div className="widget-drawer-header">
          <span className="widget-drawer-title">
            Agent Chat
            {chat.state.sessionId ? (
              <span className="widget-drawer-session">
                {chat.state.sessionId.slice(0, 8)}
              </span>
            ) : null}
          </span>
          <button className="widget-btn-new" onClick={handleNew} type="button">
            New
          </button>
          <button
            className="widget-btn-expand"
            onClick={() => setFullscreen((f) => !f)}
            title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
            type="button"
          >
            {fullscreen ? "⤢" : "⤡"}
          </button>
          <button className="widget-btn-close" onClick={handleClose} type="button">
            &times;
          </button>
        </div>

        {chat.state.error ? (
          <div
            className={`widget-error-banner ${isAuthError ? "widget-error-auth" : ""}`}
            onClick={isAuthError ? () => { window.location.href = "/login"; } : undefined}
          >
            {isAuthError ? "Session expired. Please sign in again." : chat.state.error}
          </div>
        ) : null}

        <div className="widget-messages">
          {groupMessages(chat.state.messages).map((group, gi) =>
            Array.isArray(group) ? (
              <ToolGroupBubble key={`tg-${gi}`} tools={group} />
            ) : (
              <MessageBubble key={group.id} msg={group} />
            ),
          )}
          {chat.state.activeApproval && (
            <ApprovalCard
              text={chat.state.activeApproval.command || "Approve this action?"}
              onAccept={() => chat.respondApproval(true)}
              onReject={() => chat.respondApproval(false)}
            />
          )}
          {chat.state.activeClarify && (
            <ClarifyCard
              question={chat.state.activeClarify.question}
              choices={chat.state.activeClarify.choices}
              onChoice={chat.respondClarify}
            />
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Tab bar — above input area */}
        {chat.tabs.length > 0 && (
          <div className="widget-tab-bar">
            {chat.tabs.map(tab => (
              <button
                key={tab.dbId}
                className={`widget-tab ${tab.dbId === chat.activeTabDbId ? "widget-tab-active" : ""}`}
                onClick={() => handleTabSwitch(tab.dbId)}
                type="button"
                title={tab.title}
              >
                <span className="widget-tab-title">{tab.title}</span>
                <span
                  className="widget-tab-close"
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmAction("closeTab");
                    setPendingCloseDbId(tab.dbId);
                    setShowConfirm(true);
                  }}
                  role="button"
                  title="Close tab"
                >
                  &times;
                </span>
              </button>
            ))}
          </div>
        )}

        <div className="widget-input-area">
          <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
            <textarea
              ref={textareaRef}
              className="widget-textarea"
              value={input}
              onChange={handleTextareaInput}
              onKeyDown={handleKeyDown}
              disabled={isStreaming}
              placeholder={isStreaming ? "Agent is responding..." : "Type a message..."}
              rows={1}
            />
            {input.length > LONG_INPUT_THRESHOLD ? (
              <div className="widget-char-count">{input.length.toLocaleString()} chars</div>
            ) : null}
          </div>
          <button
            className="widget-btn-send"
            onClick={handleSend}
            disabled={!input.trim() || isStreaming}
            type="button"
          >
            Send
          </button>
        </div>
      </div>

      {/* Floating icon */}
      <div className="widget-icon" onClick={handleIconClick} role="button" tabIndex={0}>
        <div className="widget-pulse" />
        <div className="widget-orbit-inner">
          <div className="widget-orbit-dot" />
        </div>
        <div className="widget-orbit-outer">
          <div className="widget-orbit-dot" />
        </div>
        {!drawerOpen && isStreaming ? <div className="widget-active-dot" /> : null}
        <WorkagentLogo className="widget-icon-logo" />
      </div>

      {/* Confirm dialog */}
      {showConfirm ? (
        <div className="widget-confirm-overlay">
          <div className="widget-confirm-card">
            <div className="widget-confirm-title">
              {confirmAction === "closeTab" ? "Delete session tab?" : "Agent is still working"}
            </div>
            <div className="widget-confirm-text">
              {confirmAction === "new"
                ? "Start a new session?"
                : confirmAction === "switch"
                ? "Switch to another session?"
                : confirmAction === "closeTab"
                ? "This tab's cached messages will be removed."
                : "Close anyway?"}
            </div>
            <div className="widget-confirm-actions">
              <button className="widget-btn-accept" onClick={handleKeepRunning} type="button">
                Cancel
              </button>
              <button className="widget-btn-reject" onClick={handleInterruptConfirm} type="button">
                {confirmAction === "new" ? "Interrupt & New" : confirmAction === "switch" ? "Interrupt & Switch" : confirmAction === "closeTab" ? "Delete" : "Interrupt & Close"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  switch (msg.role) {
    case "user":
      return (
        <div className="widget-msg widget-msg-user">
          <div className="widget-msg-text">{msg.text}</div>
        </div>
      );
    case "agent":
      return (
        <div className="widget-msg">
          <div className="widget-agent-row">
            <div className="widget-avatar"><WorkagentLogo className="widget-avatar-logo" /></div>
            <div className="widget-msg widget-msg-agent">
              <div className="widget-msg-text">
                <ReactMarkdown>{msg.text || (msg.done ? "" : "...")}</ReactMarkdown>
              </div>
            </div>
          </div>
        </div>
      );
  }
}

function ToolGroupBubble({ tools }: { tools: ToolMsg[] }) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const runningCount = tools.filter((t) => t.status === "running").length;

  return (
    <div className="widget-msg widget-msg-tool-group">
      <div className="widget-tool-group-header">
        <span className="widget-tool-group-icon">{"\u{1F527}"}</span>
        <span className="widget-tool-group-count">
          {runningCount > 0 ? `${runningCount}/${tools.length} running` : `${tools.length} tools`}
        </span>
      </div>
      {tools.map((tool) => {
        const expanded = expandedIds.has(tool.id);
        const duration = tool.status === "done" && tool.duration_s != null
          ? ` ${formatToolDuration(tool.duration_s)}` : "";
        return (
          <div key={tool.id} className="widget-tool-item">
            <div
              className="widget-tool-row widget-tool-clickable"
              onClick={() => toggle(tool.id)}
              role="button"
              tabIndex={0}
            >
              <span className="widget-tool-chevron">{expanded ? "▼" : "▶"}</span>
              <span className="widget-tool-name">{tool.name}</span>
              <span className={`widget-tool-status ${tool.status === "done" ? "widget-tool-status-done" : ""}`}>
                {tool.status === "running" ? "Running..." : `Done${duration}`}
              </span>
            </div>
            {expanded && (
              <div className="widget-tool-detail">
                {tool.context ? (
                  <div className="widget-tool-section">
                    <div className="widget-tool-label">Args</div>
                    <pre className="widget-tool-pre">{tool.context}</pre>
                  </div>
                ) : null}
                {tool.summary ? (
                  <div className="widget-tool-section">
                    <div className="widget-tool-label">Result</div>
                    <pre className="widget-tool-pre">{tool.summary}</pre>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ApprovalCard({ text, onAccept, onReject }: { text: string; onAccept: () => void; onReject: () => void }) {
  return (
    <div className="widget-approval-card">
      <div className="widget-approval-text">{text}</div>
      <div className="widget-approval-actions">
        <button className="widget-btn-accept" onClick={onAccept} type="button">Allow</button>
        <button className="widget-btn-reject" onClick={onReject} type="button">Deny</button>
      </div>
    </div>
  );
}

function ClarifyCard({ question, choices, onChoice }: { question: string; choices: string[]; onChoice: (c: string) => void }) {
  return (
    <div className="widget-clarify-card">
      <div className="widget-clarify-text">{question}</div>
      <div className="widget-clarify-actions">
        {choices.map((c) => (
          <button key={c} className="widget-btn-choice" onClick={() => onChoice(c)} type="button">
            {c}
          </button>
        ))}
      </div>
    </div>
  );
}

function WorkagentLogo({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 96 96" aria-label="WORKAGENT">
      <g stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" fill="none">
        <rect x="22" y="20" width="52" height="34" rx="11" />
        <path d="M48 14v6" />
        <circle cx="38" cy="37" r="3.5" />
        <circle cx="58" cy="37" r="3.5" />
        <path d="M41 46h14" />
        <path d="M48 56l16 6v9c0 11-8.2 16.6-16 20-7.8-3.4-16-9-16-20v-9l16-6Z" />
        <circle cx="70.5" cy="20.5" r="8.5" />
        <path d="M70.5 8v4M70.5 29v4M58 20.5h4M79 20.5h4M61.7 11.7l2.8 2.8M76.5 26.5l2.8 2.8M79.3 11.7l-2.8 2.8M64.5 26.5l-2.8 2.8" />
        <circle cx="70.5" cy="20.5" r="2.4" />
      </g>
    </svg>
  );
}
