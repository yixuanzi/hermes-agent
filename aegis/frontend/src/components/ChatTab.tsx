import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  A2AContext,
  A2AContextAgent,
  Agent,
  ChatAttachment,
  Conversation,
  Message,
  PromptTemplate,
} from "../types";
import {
  ArrowLeft,
  Bot,
  Check,
  CheckCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clock,
  Code2,
  Copy,
  Eye,
  EyeOff,
  FileText,
  Image as ImageIcon,
  Layers,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Paperclip,
  PanelRightOpen,
  PanelsTopLeft,
  Plus,
  RefreshCw,
  Send,
  ShieldAlert,
  Trash2,
  Workflow,
  X,
} from "lucide-react";
import {
  AegisChatProvider,
  useAegisChatRuntime,
  useOptionalAegisChatRuntime,
} from "../lib/chatRuntime";
import { getStoredUser } from "../lib/auth";
import {
  clearCachedDrawerTabs,
  loadCachedDrawerTabs,
  saveCachedDrawerTabs,
  type CachedDrawerTabsByConversation,
} from "../lib/chatDrawerTabs";
import {
  insertAgent2UIComposerText,
  parseAgent2UIComposerInsertIntent,
} from "../lib/agent2ui";
import { fetchJSON, getApiErrorMessage } from "../lib/api";
import {
  findShortcutQuery,
  quickCommandToken,
  type ChatQuickCommand,
  type ChatQuickCommandListResponse,
  type ShortcutQuery,
} from "../lib/chatQuickCommands";
import { AEGIS_THEMES, getStoredTheme } from "../lib/theme";
import SessionWorkflow from "./SessionWorkflow";

interface ChatTabProps {
  agents: Agent[];
}

type PromptTemplateListResponse = { templates: PromptTemplate[] };
type AttachmentUploadResponse = { attachment: ChatAttachment };

interface PendingAttachment {
  localId: string;
  file: File;
  previewUrl?: string;
  attachment?: ChatAttachment;
  status: "uploading" | "ready" | "failed";
  error?: string;
}

type WorkflowDrawerTab = "workflow" | `file:${string}`;

interface DrawerFilePayload {
  title: string;
  type: string;
  content: string;
}

interface DynamicDrawerTab extends DrawerFilePayload {
  id: `file:${string}`;
  path: string;
  loading: boolean;
  refreshVersion: number;
  settledVersion: number;
  error: string;
}

interface SessionDrawerTabs {
  activeTab: WorkflowDrawerTab;
  tabs: DynamicDrawerTab[];
}

const WORKFLOW_DRAWER_WIDTH_STORAGE_KEY = "aegis_chat_workflow_drawer_width";
const DEFAULT_WORKFLOW_DRAWER_WIDTH = 50;
const MIN_WORKSPACE_PANE_WIDTH = 360;
const SHORTCUT_TOKEN_PATTERN = /@\[(?:agent|prompt|instruct)_[^\]]+\]/g;
const HAS_SHORTCUT_TOKEN_PATTERN = /@\[(?:agent|prompt|instruct)_[^\]]+\]/;

function renderShortcutTokens(text: string): React.ReactNode[] {
  const tokens: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  SHORTCUT_TOKEN_PATTERN.lastIndex = 0;
  while ((match = SHORTCUT_TOKEN_PATTERN.exec(text)) !== null) {
    if (match.index > cursor) {
      tokens.push(text.slice(cursor, match.index));
    }
    const token = match[0];
    tokens.push(
      <strong className="aegis-shortcut-token" key={`${match.index}-${token}`}>
        <u>{token}</u>
      </strong>,
    );
    cursor = match.index + token.length;
  }
  if (cursor < text.length) {
    tokens.push(text.slice(cursor));
  }
  return tokens;
}

function drawerWidthBounds(containerWidth: number) {
  if (!containerWidth || containerWidth <= MIN_WORKSPACE_PANE_WIDTH * 2) {
    return { min: 20, max: 80 };
  }
  return {
    min: Math.max(20, (MIN_WORKSPACE_PANE_WIDTH / containerWidth) * 100),
    max: Math.min(80, 100 - (MIN_WORKSPACE_PANE_WIDTH / containerWidth) * 100),
  };
}

function clampDrawerWidth(width: number, containerWidth: number): number {
  const { min, max } = drawerWidthBounds(containerWidth);
  return Math.min(max, Math.max(min, width));
}

interface QuickCommandTypeIconProps {
  type: ChatQuickCommand["type"];
}

function QuickCommandTypeIcon({ type }: QuickCommandTypeIconProps) {
  const iconClassName = "h-3.5 w-3.5";
  if (type === "agent") return <Bot className={iconClassName} aria-hidden="true" />;
  if (type === "prompt") return <Code2 className={iconClassName} aria-hidden="true" />;
  return <ShieldAlert className={iconClassName} aria-hidden="true" />;
}

function loadWorkflowDrawerWidth(): number {
  const rawValue = window.localStorage.getItem(WORKFLOW_DRAWER_WIDTH_STORAGE_KEY);
  if (rawValue === null) {
    return DEFAULT_WORKFLOW_DRAWER_WIDTH;
  }
  const stored = Number(rawValue);
  if (!Number.isFinite(stored)) {
    return DEFAULT_WORKFLOW_DRAWER_WIDTH;
  }
  return clampDrawerWidth(stored, window.innerWidth);
}

interface DrawerFilePreviewProps {
  title: string;
  type: string;
  content: string;
  loading: boolean;
  error: string;
  active: boolean;
  onComposerInsert: (text: string) => void;
}

function DrawerFilePreview({
  title,
  type,
  content,
  loading,
  error,
  active,
  onComposerInsert,
}: DrawerFilePreviewProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const handleMessage = (event: MessageEvent<unknown>) => {
      if (
        !active ||
        event.source !== iframeRef.current?.contentWindow
      ) {
        return;
      }
      const intent = parseAgent2UIComposerInsertIntent(event.data);
      if (intent) onComposerInsert(intent.text);
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [active, onComposerInsert]);

  if (loading && !content) {
    return (
      <div className="aegis-drawer-preview__status" role="status">
        LOADING FILE PREVIEW…
      </div>
    );
  }
  if (error && !content) {
    return (
      <div className="aegis-alert aegis-alert--danger m-4" role="alert">
        {error}
      </div>
    );
  }
  if (!content) {
    return (
      <div className="aegis-drawer-preview__status" role="status">
        BINARY FILE — PREVIEW UNAVAILABLE
      </div>
    );
  }
  if (type === "markdown") {
    return <div className="aegis-drawer-preview__markdown aegis-markdown"><ChatMarkdown content={content} /></div>;
  }
  if (["avif", "bmp", "gif", "ico", "jpeg", "jpg", "png", "svg", "webp"].includes(type)) {
    return <div className="aegis-drawer-preview__image-wrap"><img alt={title} className="aegis-drawer-preview__image" src={content} /></div>;
  }
  if (type !== "html") {
    return <pre className="aegis-drawer-preview__code"><code>{content}</code></pre>;
  }
  return (
    <iframe
      ref={iframeRef}
      title={title || "Aegis file preview"}
      data-testid="drawer-html-frame"
      className="aegis-drawer-preview__frame"
      sandbox="allow-scripts allow-forms"
      referrerPolicy="no-referrer"
      srcDoc={content}
    />
  );
}

function ChatMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => (
          <h1 className="aegis-markdown__h1">{children}</h1>
        ),
        h2: ({ children }) => (
          <h2 className="aegis-markdown__h2">{children}</h2>
        ),
        h3: ({ children }) => (
          <h3 className="aegis-markdown__h3">{children}</h3>
        ),
        p: ({ children }) => (
          <p className="aegis-markdown__paragraph">{children}</p>
        ),
        ul: ({ children }) => (
          <ul className="aegis-markdown__list aegis-markdown__list--unordered">
            {children}
          </ul>
        ),
        ol: ({ children }) => (
          <ol className="aegis-markdown__list aegis-markdown__list--ordered">
            {children}
          </ol>
        ),
        li: ({ children }) => (
          <li className="aegis-markdown__list-item">{children}</li>
        ),
        blockquote: ({ children }) => (
          <blockquote className="aegis-markdown__quote">{children}</blockquote>
        ),
        pre: ({ children }) => (
          <pre className="aegis-markdown__pre">{children}</pre>
        ),
        code: ({ children }) => (
          <code className="aegis-markdown__code">{children}</code>
        ),
        table: ({ children }) => (
          <div className="aegis-markdown__table-wrap">
            <table className="aegis-markdown__table">{children}</table>
          </div>
        ),
        tr: ({ children }) => (
          <tr className="aegis-markdown__tr">{children}</tr>
        ),
        th: ({ children }) => (
          <th className="aegis-markdown__th">{children}</th>
        ),
        td: ({ children }) => (
          <td className="aegis-markdown__td">{children}</td>
        ),
        a: ({ href, children }) => {
          const external = Boolean(href && /^https?:\/\//i.test(href));
          return (
            <a
              href={href}
              target={external ? "_blank" : undefined}
              rel={external ? "noreferrer" : undefined}
              className="aegis-markdown__link"
            >
              {children}
            </a>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

function isConversationBusy(conversation: Conversation | undefined): boolean {
  if (!conversation) {
    return false;
  }
  if (conversation.lastKnownRunState === "waiting_for_approval") {
    return true;
  }
  if (conversation.lastKnownRunState === "waiting_for_clarify") {
    return !conversation.pendingClarify?.awaitingText;
  }
  return false;
}

function agentLabel(srcagent?: string): string {
  return srcagent?.trim() || "AEGIS";
}

function describeRunState(
  state?: string,
  source: "main" | "delegate" = "main",
  srcagent?: string,
): string {
  const actor = agentLabel(srcagent);
  switch (state) {
    case "idle":
      return "SESSION COMPLETE · AWAITING NEXT REQUEST";
    case "running":
      return `${actor} · ${source === "delegate" ? "DELEGATE" : "MAIN"} EXECUTION IN PROGRESS`;
    case "waiting_for_delegate_input":
      return `${actor} · DELEGATE AWAITING INPUT`;
    case "waiting_for_approval":
      return `${actor} · APPROVAL REQUIRED TO CONTINUE`;
    case "waiting_for_clarify":
      return `${actor} · CLARIFICATION REQUIRED TO CONTINUE`;
    case "interrupted":
      return `${actor} · SESSION INTERRUPTED`;
    case "error":
      return `${actor} · SESSION ERROR · REVIEW WORKFLOW`;
    default:
      return `${actor} · ${state?.replace(/_/g, " ").toUpperCase() || "AWAITING FIRST TURN"}`;
  }
}

function buildSessionStatus(conversation?: Conversation): string {
  const hasRecordedActivity = Boolean(
    conversation?.sessionId ||
    conversation?.messages.length ||
    conversation?.workflowTrace?.length,
  );
  if (!hasRecordedActivity) {
    return "AWAITING FIRST TURN";
  }

  const latestEvent = conversation?.workflowTrace?.at(-1);
  if (latestEvent) {
    const actor = agentLabel(latestEvent.srcagent);
    if (latestEvent.type === "delegate.entered") {
      return `${actor} · DELEGATED AND ENTERED FOREGROUND`;
    }
    if (latestEvent.type === "delegate.exited") {
      return `${actor} · DELEGATION COMPLETE · CONTROL RETURNED TO MAIN`;
    }
    if (latestEvent.type === "tool.started") {
      return `${actor} · TOOL CALL RUNNING · ${latestEvent.toolName || "TOOL"}`;
    }
    if (latestEvent.type === "tool.completed") {
      return `${actor} · TOOL CALL COMPLETED · ${latestEvent.toolName || "TOOL"}`;
    }
    if (latestEvent.type === "run.state") {
      return describeRunState(
        latestEvent.state,
        latestEvent.source,
        latestEvent.srcagent,
      );
    }
    if (
      latestEvent.type === "message.completed" ||
      latestEvent.type === "message.stream.completed"
    ) {
      return `${actor} · RESPONSE RECEIVED`;
    }
    if (latestEvent.type === "message.accepted") {
      return `${actor} · REQUEST ACCEPTED`;
    }
  }

  if (conversation?.lastKnownRunState) {
    return describeRunState(
      conversation.lastKnownRunState,
      conversation.foregroundSource,
      conversation.foregroundAgentName,
    );
  }
  return "AWAITING FIRST TURN";
}

function statusSegmentTone(segment: string, index: number): string {
  if (/ERROR|INTERRUPTED/.test(segment)) {
    return "critical";
  }
  if (/COMPLETE|COMPLETED/.test(segment)) {
    return "complete";
  }
  if (/REQUIRED|AWAITING/.test(segment)) {
    return "attention";
  }
  if (/RUNNING|EXECUTION|PROCESSING|DELEGATED|CONTROL RETURNED/.test(segment)) {
    return "activity";
  }
  if (/TOOL|RESPONSE|REQUEST/.test(segment)) {
    return "object";
  }
  if (index === 0) {
    return "actor";
  }
  return index === 1 ? "detail" : "object";
}

function renderSessionStatus(status: string) {
  return status.split(" · ").map((segment, index) => (
    <React.Fragment key={`${index}:${segment}`}>
      {index > 0 ? (
        <span
          className="aegis-session-status-ticker__separator"
          aria-hidden="true"
        >
          {" "}
          ·{" "}
        </span>
      ) : null}
      <span
        className={`aegis-session-status-ticker__part aegis-session-status-ticker__part--${statusSegmentTone(segment, index)}`}
      >
        {segment}
      </span>
    </React.Fragment>
  ));
}

async function writeClipboardText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const element = document.createElement("textarea");
  element.value = text;
  element.setAttribute("readonly", "true");
  element.style.position = "absolute";
  element.style.left = "-9999px";
  document.body.appendChild(element);
  element.select();
  document.execCommand("copy");
  document.body.removeChild(element);
}

function getMessageCopyText(message: Message): string {
  if (message.kind === "delegate-tools" && message.delegateTools?.length) {
    return message.delegateTools
      .map((toolCall) => {
        const resultLine = toolCall.resultPreview
          ? `\nResult: ${toolCall.resultPreview}`
          : "";
        return `${toolCall.toolName}\nArgs: ${toolCall.argsPreview || "(none)"}${resultLine}`;
      })
      .join("\n\n");
  }
  if (message.kind === "main-tools" && message.chainSteps?.length) {
    return message.chainSteps
      .map((step) => `${step.agentName}\n${step.status}\n${step.message}`)
      .join("\n\n");
  }
  return message.text;
}

function getThemeMessageArgument(): string {
  const activeTheme = document.documentElement.dataset.aegisTheme;
  const theme =
    AEGIS_THEMES.find((candidate) => candidate.id === activeTheme) ??
    AEGIS_THEMES.find((candidate) => candidate.id === getStoredTheme()) ??
    AEGIS_THEMES[0];
  const { preview } = theme;
  return JSON.stringify({
    style: theme.description,
    background_surface: `${preview.background} / ${preview.surface}`,
    accent: preview.accent,
    text_muted: `${preview.text} / ${preview.muted}`,
    border: preview.border,
  });
}

function hydrateDrawerTabs(
  cached: CachedDrawerTabsByConversation,
): Record<string, SessionDrawerTabs> {
  return Object.fromEntries(
    Object.entries(cached).map(([conversationId, sessionTabs]) => [
      conversationId,
      {
        activeTab: sessionTabs.activeTab,
        tabs: sessionTabs.tabs.map((tab) => ({
          ...tab,
          type: "text",
          content: "",
          loading: false,
          refreshVersion: 0,
          settledVersion: -1,
          error: "",
        })),
      },
    ]),
  );
}

function ChatTabContent({ agents }: ChatTabProps) {
  void agents;
  const {
    conversations,
    activeConvId,
    activeConversation,
    transportError,
    rejectedInput,
    pendingHtmlPreviews,
    setActiveConversation,
    createConversation,
    clearHistory,
    deleteConversation,
    submitInput,
    respondApproval,
    respondClarify,
    markClarifyAwaitingText,
    resumeActiveConversation,
    setTransportError,
    clearRejectedInput,
    consumePendingHtmlPreview,
  } = useAegisChatRuntime();
  const drawerTabsUserId = getStoredUser()?.uid || "";
  const [inputVal, setInputVal] = useState("");
  const [delegateClarifySelection, setDelegateClarifySelection] = useState<string[]>([]);
  useEffect(() => {
    setDelegateClarifySelection([]);
  }, [activeConversation?.pendingClarify?.clarifyId]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [composerExpanded, setComposerExpanded] = useState(false);
  const [copiedMessageId, setCopiedMessageId] = useState("");
  const [showDelegateTools, setShowDelegateTools] = useState(false);
  const [markdownRenderingEnabled, setMarkdownRenderingEnabled] =
    useState(true);
  const [a2uiEnabled, setA2uiEnabled] = useState(false);
  const [expandedMessageIds, setExpandedMessageIds] = useState<
    Record<string, boolean>
  >({});
  const [sessionStatusPhase, setSessionStatusPhase] = useState<
    "announce" | "static" | "scroll"
  >("announce");
  const [workflowDrawerOpen, setWorkflowDrawerOpen] = useState(false);
  const [workflowFullscreen, setWorkflowFullscreen] = useState(false);
  const [drawerTabsByConversation, setDrawerTabsByConversation] = useState<
    Record<string, SessionDrawerTabs>
  >(() =>
    hydrateDrawerTabs(
      loadCachedDrawerTabs(
        drawerTabsUserId,
        conversations.map((conversation) => conversation.id),
      ),
    ),
  );
  const [workflowDrawerWidth, setWorkflowDrawerWidth] = useState(
    loadWorkflowDrawerWidth,
  );
  const [workflowDrawerResizing, setWorkflowDrawerResizing] = useState(false);
  const [expandedModifiedFileLists, setExpandedModifiedFileLists] = useState<
    Record<string, boolean>
  >({});
  const [promptTemplateDrawerOpen, setPromptTemplateDrawerOpen] =
    useState(false);
  const [promptTemplates, setPromptTemplates] = useState<PromptTemplate[]>([]);
  const [promptTemplateLoading, setPromptTemplateLoading] = useState(false);
  const [promptTemplateError, setPromptTemplateError] = useState("");
  const [a2aDialogOpen, setA2ADialogOpen] = useState(false);
  const [a2aContext, setA2AContext] = useState<A2AContext | null>(null);
  const [a2aLoading, setA2ALoading] = useState(false);
  const [a2aRefreshing, setA2ARefreshing] = useState(false);
  const [a2aError, setA2AError] = useState("");
  const [selectedA2AAgent, setSelectedA2AAgent] =
    useState<A2AContextAgent | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<
    PendingAttachment[]
  >([]);
  const [quickCommands, setQuickCommands] = useState<ChatQuickCommand[]>([]);
  const [quickCommandsLoaded, setQuickCommandsLoaded] = useState(false);
  const [quickCommandsLoading, setQuickCommandsLoading] = useState(false);
  const [quickCommandsError, setQuickCommandsError] = useState("");
  const [shortcutQuery, setShortcutQuery] = useState<ShortcutQuery | null>(null);
  const [activeQuickCommandIndex, setActiveQuickCommandIndex] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const copyFeedbackTimeoutRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pendingAttachmentsRef = useRef<PendingAttachment[]>([]);
  const chatWorkspaceRef = useRef<HTMLDivElement>(null);
  const drawerResizePointerRef = useRef<number | null>(null);
  const composerInputRef = useRef<HTMLDivElement>(null);
  const composerDomValueRef = useRef("");
  const shortcutQueryRef = useRef<ShortcutQuery | null>(null);
  const pendingComposerCaretRef = useRef<number | null>(null);
  const pendingComposerFocusRef = useRef(true);
  const lastComposerCaretRef = useRef<number | null>(null);
  const activeDrawerTabs = activeConvId ? drawerTabsByConversation[activeConvId] : undefined;
  const workflowDrawerTab = activeDrawerTabs?.activeTab || "workflow";
  const dynamicDrawerTabs = activeDrawerTabs?.tabs || [];
  const pendingHtmlPreview = activeConvId ? pendingHtmlPreviews[activeConvId] : undefined;

  useEffect(() => {
    return () => {
      if (copyFeedbackTimeoutRef.current !== null) {
        window.clearTimeout(copyFeedbackTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    pendingAttachmentsRef.current = pendingAttachments;
  }, [pendingAttachments]);

  useEffect(
    () => () => {
      pendingAttachmentsRef.current.forEach((attachment) => {
        if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
      });
    },
    [],
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [conversations, activeConvId]);

  function syncRichComposer(text: string) {
    const composer = composerInputRef.current;
    if (!composer) return;
    const knownTokens = new Set(quickCommands.map(quickCommandToken));
    const tokenPattern = SHORTCUT_TOKEN_PATTERN;
    tokenPattern.lastIndex = 0;
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    let match: RegExpExecArray | null;
    while ((match = tokenPattern.exec(text)) !== null) {
      if (match.index > cursor) {
        fragment.append(document.createTextNode(text.slice(cursor, match.index)));
      }
      const token = match[0];
      if (knownTokens.has(token)) {
        const strong = document.createElement("strong");
        strong.className = "aegis-shortcut-token";
        strong.contentEditable = "false";
        strong.dataset.shortcutToken = token;
        const underline = document.createElement("u");
        underline.textContent = token;
        strong.append(underline);
        fragment.append(strong);
      } else {
        fragment.append(document.createTextNode(token));
      }
      cursor = match.index + token.length;
    }
    if (cursor < text.length) fragment.append(document.createTextNode(text.slice(cursor)));
    composer.replaceChildren(fragment);
    composerDomValueRef.current = text;
  }

  function getComposerCaretOffset(): number | null {
    const composer = composerInputRef.current;
    const selection = window.getSelection();
    if (!composer || !selection?.rangeCount || !composer.contains(selection.anchorNode)) {
      return null;
    }
    const range = document.createRange();
    range.selectNodeContents(composer);
    range.setEnd(selection.anchorNode, selection.anchorOffset);
    return range.toString().length;
  }

  function setComposerCaret(offset: number) {
    const composer = composerInputRef.current;
    if (!composer) return;
    const range = document.createRange();
    const walker = document.createTreeWalker(composer, NodeFilter.SHOW_TEXT);
    let remaining = offset;
    let node = walker.nextNode();
    while (node) {
      const length = node.textContent?.length || 0;
      if (remaining <= length) {
        range.setStart(node, remaining);
        range.collapse(true);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
        return;
      }
      remaining -= length;
      node = walker.nextNode();
    }
    range.selectNodeContents(composer);
    range.collapse(false);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  useEffect(() => {
    if (composerDomValueRef.current !== inputVal) {
      syncRichComposer(inputVal);
    }
    const caret = pendingComposerCaretRef.current;
    if (caret === null) return;
    pendingComposerCaretRef.current = null;
    const shouldFocus = pendingComposerFocusRef.current;
    pendingComposerFocusRef.current = true;
    if (shouldFocus && !isConversationBusy(activeConversation)) {
      composerInputRef.current?.focus();
    }
    setComposerCaret(caret);
    lastComposerCaretRef.current = caret;
  }, [activeConversation, inputVal, quickCommands]);

  useEffect(() => {
    if (!rejectedInput) return;
    pendingComposerCaretRef.current = rejectedInput.text.length;
    setInputVal((current) => current || rejectedInput.text);
    shortcutQueryRef.current = null;
    setShortcutQuery(null);
    clearRejectedInput();
  }, [clearRejectedInput, rejectedInput]);

  useEffect(() => {
    if (
      shortcutQuery === null ||
      quickCommandsLoaded
    ) {
      return;
    }
    let cancelled = false;
    setQuickCommandsLoading(true);
    setQuickCommandsError("");
    void fetchJSON<ChatQuickCommandListResponse>("/api/chat/quick-commands")
      .then((response) => {
        if (!cancelled) {
          setQuickCommands(response.commands);
          setQuickCommandsLoaded(true);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setQuickCommandsError(
            getApiErrorMessage(error, "Unable to load quick commands."),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setQuickCommandsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [quickCommandsLoaded, shortcutQuery === null]);

  const sessionStatus = buildSessionStatus(activeConversation);
  const sessionStatusNeedsMarquee = sessionStatus.length > 36;

  useEffect(() => {
    setSessionStatusPhase("announce");
    const tickerTimer = window.setTimeout(
      () =>
        setSessionStatusPhase(sessionStatusNeedsMarquee ? "scroll" : "static"),
      1500,
    );
    return () => window.clearTimeout(tickerTimer);
  }, [sessionStatus, sessionStatusNeedsMarquee]);

  useEffect(() => {
    if (!workflowDrawerOpen) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (workflowFullscreen) {
          setWorkflowFullscreen(false);
        } else {
          setWorkflowDrawerOpen(false);
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [workflowDrawerOpen, workflowFullscreen]);

  const activeDynamicDrawerTab = dynamicDrawerTabs.find(
    (tab) => tab.id === workflowDrawerTab,
  );

  useEffect(() => {
    if (
      !workflowDrawerOpen ||
      !activeDynamicDrawerTab ||
      activeDynamicDrawerTab.settledVersion === activeDynamicDrawerTab.refreshVersion
    ) {
      return;
    }
    const tabId = activeDynamicDrawerTab.id;
    const conversationId = activeConvId;
    const refreshVersion = activeDynamicDrawerTab.refreshVersion;
    if (!conversationId) {
      return;
    }
    setDynamicDrawerTabsForConversation(conversationId, (current) =>
      current.map((tab) =>
        tab.id === tabId && tab.refreshVersion === refreshVersion
          ? { ...tab, loading: true, error: "" }
          : tab,
      ),
    );
    void fetchJSON<DrawerFilePayload>(
      `/api/chat/drawer-html?path=${encodeURIComponent(activeDynamicDrawerTab.path)}`,
    )
      .then((payload) => {
        setDynamicDrawerTabsForConversation(conversationId, (current) =>
          current.map((tab) =>
            tab.id === tabId && tab.refreshVersion === refreshVersion
              ? {
                  ...tab,
                  ...payload,
                  loading: false,
                  settledVersion: refreshVersion,
                  error: "",
                }
              : tab,
          ),
        );
      })
      .catch((error: unknown) => {
        setDynamicDrawerTabsForConversation(conversationId, (current) =>
          current.map((tab) =>
            tab.id === tabId && tab.refreshVersion === refreshVersion
              ? {
                  ...tab,
                  loading: false,
                  settledVersion: refreshVersion,
                  error: getApiErrorMessage(error, "Unable to load the file preview."),
                }
              : tab,
          ),
        );
      });
  }, [
    activeDynamicDrawerTab?.path,
    activeDynamicDrawerTab?.refreshVersion,
    activeDynamicDrawerTab?.settledVersion,
    activeConvId,
    workflowDrawerOpen,
    workflowDrawerTab,
  ]);

  useEffect(() => {
    window.localStorage.setItem(
      WORKFLOW_DRAWER_WIDTH_STORAGE_KEY,
      String(workflowDrawerWidth),
    );
  }, [workflowDrawerWidth]);

  useEffect(() => {
    saveCachedDrawerTabs(
      drawerTabsUserId,
      drawerTabsByConversation,
      conversations.map((conversation) => conversation.id),
    );
  }, [conversations, drawerTabsByConversation, drawerTabsUserId]);

  useEffect(() => {
    const clampStoredDrawerWidth = () => {
      const containerWidth = chatWorkspaceRef.current?.clientWidth || window.innerWidth;
      setWorkflowDrawerWidth((current) =>
        clampDrawerWidth(current, containerWidth),
      );
    };
    window.addEventListener("resize", clampStoredDrawerWidth);
    return () => window.removeEventListener("resize", clampStoredDrawerWidth);
  }, []);

  useEffect(() => {
    if (!a2aDialogOpen) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setA2ADialogOpen(false);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [a2aDialogOpen]);

  function closeWorkflow() {
    setWorkflowFullscreen(false);
    setWorkflowDrawerOpen(false);
  }

  function openWorkflowFromSessionStatus() {
    setWorkflowFullscreen(false);
    setWorkflowDrawerOpen(true);
  }

  function setDrawerWidthFromPointer(clientX: number) {
    const bounds = chatWorkspaceRef.current?.getBoundingClientRect();
    if (!bounds?.width || !Number.isFinite(clientX)) {
      return;
    }
    const nextWidth = ((clientX - bounds.left) / bounds.width) * 100;
    setWorkflowDrawerWidth(clampDrawerWidth(nextWidth, bounds.width));
  }

  function handleDrawerResizePointerDown(
    event: React.PointerEvent<HTMLDivElement>,
  ) {
    const pointerId = event.pointerId ?? 0;
    drawerResizePointerRef.current = pointerId;
    setWorkflowDrawerResizing(true);
    event.currentTarget.setPointerCapture?.(pointerId);
    setDrawerWidthFromPointer(event.clientX);
  }

  function handleDrawerResizePointerMove(
    event: React.PointerEvent<HTMLDivElement>,
  ) {
    if (drawerResizePointerRef.current === (event.pointerId ?? 0)) {
      setDrawerWidthFromPointer(event.clientX);
    }
  }

  function handleDrawerResizePointerUp(
    event: React.PointerEvent<HTMLDivElement>,
  ) {
    const pointerId = event.pointerId ?? 0;
    if (drawerResizePointerRef.current !== pointerId) {
      return;
    }
    drawerResizePointerRef.current = null;
    setWorkflowDrawerResizing(false);
    event.currentTarget.releasePointerCapture?.(pointerId);
  }

  function handleDrawerResizeKeyDown(
    event: React.KeyboardEvent<HTMLDivElement>,
  ) {
    const bounds = chatWorkspaceRef.current?.getBoundingClientRect();
    const containerWidth = bounds?.width || window.innerWidth;
    const { min, max } = drawerWidthBounds(containerWidth);
    const increment = event.shiftKey ? 5 : 1;
    if (event.key === "ArrowRight") {
      event.preventDefault();
      setWorkflowDrawerWidth((current) =>
        clampDrawerWidth(current + increment, containerWidth),
      );
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      setWorkflowDrawerWidth((current) =>
        clampDrawerWidth(current - increment, containerWidth),
      );
    } else if (event.key === "Home") {
      event.preventDefault();
      setWorkflowDrawerWidth(min);
    } else if (event.key === "End") {
      event.preventDefault();
      setWorkflowDrawerWidth(max);
    }
  }

  function handleSessionStatusKeyDown(
    event: React.KeyboardEvent<HTMLButtonElement>,
  ) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openWorkflowFromSessionStatus();
    }
  }

  function handleCreateNewConversation() {
    createConversation();
    setInputVal("");
    clearPendingAttachments();
    setTransportError("");
  }

  function handleClearHistory() {
    if (clearHistory()) {
      setDrawerTabsByConversation({});
      clearCachedDrawerTabs(drawerTabsUserId);
      setInputVal("");
      clearPendingAttachments();
      setTransportError("");
    }
  }

  function handleDeleteConversation(id: string, event: React.MouseEvent) {
    event.stopPropagation();
    deleteConversation(id);
    setDrawerTabsByConversation((current) => {
      const { [id]: _removed, ...remaining } = current;
      return remaining;
    });
  }

  function updateShortcutQuery(value: string, caret: number | null) {
    const nextQuery =
      findShortcutQuery(value, caret ?? value.length) ||
      (caret !== value.length ? findShortcutQuery(value, value.length) : null);
    const previousQuery = shortcutQueryRef.current;
    const queryChanged =
      previousQuery?.query !== nextQuery?.query ||
      previousQuery?.start !== nextQuery?.start ||
      previousQuery?.end !== nextQuery?.end;
    shortcutQueryRef.current = nextQuery;
    setShortcutQuery(nextQuery);
    if (queryChanged) setActiveQuickCommandIndex(0);
  }

  function handleComposerInput(event: React.FormEvent<HTMLDivElement>) {
    const value = (event.currentTarget.textContent || "").replace(/\u00a0/g, " ");
    composerDomValueRef.current = value;
    setInputVal(value);
    const caret = getComposerCaretOffset();
    lastComposerCaretRef.current = caret;
    updateShortcutQuery(value, caret);
  }

  function handleComposerSelect() {
    const caret = getComposerCaretOffset();
    lastComposerCaretRef.current = caret;
    updateShortcutQuery(inputVal, caret);
  }

  function rememberComposerCaret() {
    const caret = getComposerCaretOffset();
    if (caret !== null) lastComposerCaretRef.current = caret;
  }

  function insertAgent2UIIntoComposer(text: string) {
    shortcutQueryRef.current = null;
    setShortcutQuery(null);
    setInputVal((current) => {
      const next = insertAgent2UIComposerText(
        current,
        text,
        lastComposerCaretRef.current,
      );
      pendingComposerCaretRef.current = next.caret;
      pendingComposerFocusRef.current = !isConversationBusy(activeConversation);
      lastComposerCaretRef.current = next.caret;
      return next.text;
    });
  }

  const matchingQuickCommands = shortcutQuery
    ? quickCommands.filter((command) => {
        const query = shortcutQuery.query.toLocaleLowerCase();
        return [command.type, command.name, command.desc]
          .join(" ")
          .toLocaleLowerCase()
          .includes(query);
      })
    : [];

  function selectQuickCommand(command: ChatQuickCommand) {
    const query = shortcutQuery;
    if (!query) return;
    const token = quickCommandToken(command);
    const nextValue = `${inputVal.slice(0, query.start)}${token} ${inputVal.slice(query.end)}`;
    const nextCaret = query.start + token.length + 1;
    pendingComposerCaretRef.current = nextCaret;
    setInputVal(nextValue);
    shortcutQueryRef.current = null;
    setShortcutQuery(null);
  }

  function removeTokenBeforeCaret(): boolean {
    const selection = window.getSelection();
    const caret = getComposerCaretOffset();
    if (caret === null || !selection?.isCollapsed) return false;
    const trailingSpaceLength = inputVal[caret - 1] === " " ? 1 : 0;
    const tokenEnd = caret - trailingSpaceLength;
    const matchingToken = quickCommands
      .map(quickCommandToken)
      .sort((left, right) => right.length - left.length)
      .find((token) => inputVal.slice(0, tokenEnd).endsWith(token));
    if (!matchingToken) return false;
    const tokenStart = tokenEnd - matchingToken.length;
    const nextValue = `${inputVal.slice(0, tokenStart)}${inputVal.slice(caret)}`;
    pendingComposerCaretRef.current = tokenStart;
    setInputVal(nextValue);
    shortcutQueryRef.current = null;
    setShortcutQuery(null);
    return true;
  }

  function handleSubmit() {
    const attachments = pendingAttachments
      .filter(
        (attachment) => attachment.status === "ready" && attachment.attachment,
      )
      .map((attachment) => attachment.attachment as ChatAttachment);
    if (!inputVal.trim() && attachments.length === 0) {
      return;
    }
    const text =
      a2uiEnabled && !inputVal.includes("@[instruct_a2ui]")
        ? `@[instruct_a2ui]\n${inputVal}`
        : inputVal;
    submitInput(
      text,
      attachments,
      {
        theme_color: getThemeMessageArgument(),
        date: new Date().toISOString(),
      },
    );
    setInputVal("");
    shortcutQueryRef.current = null;
    setShortcutQuery(null);
    clearPendingAttachments();
  }

  function clearPendingAttachments() {
    setPendingAttachments((current) => {
      current.forEach((attachment) => {
        if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
      });
      return [];
    });
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function uploadAttachment(localId: string, file: File) {
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const response = await fetchJSON<AttachmentUploadResponse>(
        "/api/chat/attachments",
        {
          method: "POST",
          body: form,
        },
      );
      setPendingAttachments((current) =>
        current.map((attachment) =>
          attachment.localId === localId
            ? {
                ...attachment,
                status: "ready",
                attachment: response.attachment,
                error: undefined,
              }
            : attachment,
        ),
      );
    } catch (error) {
      setPendingAttachments((current) =>
        current.map((attachment) =>
          attachment.localId === localId
            ? {
                ...attachment,
                status: "failed",
                error: getApiErrorMessage(error, "Upload failed."),
              }
            : attachment,
        ),
      );
    }
  }

  function queueAttachments(files: FileList | File[]) {
    const nextFiles = Array.from(files);
    if (nextFiles.length === 0) return;
    const uploads = nextFiles.map((file) => ({
      localId: `upload-${crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`}`,
      file,
      previewUrl: file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : undefined,
      status: "uploading" as const,
    }));
    setPendingAttachments((current) => [...current, ...uploads]);
    uploads.forEach(
      (upload) => void uploadAttachment(upload.localId, upload.file),
    );
  }

  function removePendingAttachment(localId: string) {
    setPendingAttachments((current) =>
      current.filter((attachment) => {
        if (attachment.localId === localId && attachment.previewUrl)
          URL.revokeObjectURL(attachment.previewUrl);
        return attachment.localId !== localId;
      }),
    );
  }

  function retryPendingAttachment(localId: string) {
    const attachment = pendingAttachments.find(
      (item) => item.localId === localId,
    );
    if (!attachment) return;
    setPendingAttachments((current) =>
      current.map((item) =>
        item.localId === localId
          ? { ...item, status: "uploading", error: undefined }
          : item,
      ),
    );
    void uploadAttachment(localId, attachment.file);
  }

  function handleComposerPaste(
    event: React.ClipboardEvent<HTMLDivElement>,
  ) {
    const images = Array.from(event.clipboardData.files).filter((file) =>
      file.type.startsWith("image/"),
    );
    if (images.length > 0) {
      event.preventDefault();
      queueAttachments(images);
      return;
    }
    const pastedText = event.clipboardData.getData("text/plain");
    if (!pastedText) return;
    event.preventDefault();
    const selection = window.getSelection();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (!range) return;
    range.deleteContents();
    const textNode = document.createTextNode(pastedText);
    range.insertNode(textNode);
    range.setStartAfter(textNode);
    range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
    const composer = composerInputRef.current;
    if (composer) {
      const value = (composer.textContent || "").replace(/\u00a0/g, " ");
      composerDomValueRef.current = value;
      setInputVal(value);
      updateShortcutQuery(value, getComposerCaretOffset());
    }
  }

  function handleApproval(choice: "once" | "session" | "always" | "deny") {
    respondApproval(choice);
  }

  function handleClarifyChoice(answer: string) {
    if (activeConversation?.pendingClarify?.multiSelect) {
      setDelegateClarifySelection((current) =>
        current.includes(answer)
          ? current.filter((item) => item !== answer)
          : [...current, answer],
      );
      return;
    }
    respondClarify(answer);
  }

  function handleClarifySubmit() {
    if (delegateClarifySelection.length > 0) {
      respondClarify(JSON.stringify(delegateClarifySelection));
    }
  }

  function handleClarifyOther() {
    markClarifyAwaitingText();
  }

  function handleResume() {
    resumeActiveConversation();
  }

  async function openPromptTemplateDrawer() {
    setPromptTemplateDrawerOpen(true);
    setPromptTemplateLoading(true);
    setPromptTemplateError("");
    try {
      const response = await fetchJSON<PromptTemplateListResponse>(
        "/api/prompt-templates",
      );
      setPromptTemplates(response.templates);
    } catch (loadError) {
      setPromptTemplateError(
        getApiErrorMessage(loadError, "Unable to load prompt templates."),
      );
    } finally {
      setPromptTemplateLoading(false);
    }
  }

  function appendPromptTemplate(template: PromptTemplate) {
    setInputVal((current) =>
      current ? `${current} ${template.prompt}` : template.prompt,
    );
    setPromptTemplateDrawerOpen(false);
  }

  async function loadA2AContext(refresh = false) {
    if (refresh) {
      setA2ARefreshing(true);
    } else {
      setA2ALoading(true);
    }
    setA2AError("");
    try {
      const response = await fetchJSON<A2AContext>(
        refresh ? "/api/a2a/context/refresh" : "/api/a2a/context",
        refresh ? { method: "POST" } : undefined,
      );
      setA2AContext(response);
      setSelectedA2AAgent(null);
    } catch (loadError) {
      setA2AError(getApiErrorMessage(loadError, "Unable to load A2A agents."));
    } finally {
      setA2ALoading(false);
      setA2ARefreshing(false);
    }
  }

  function openA2AAgents() {
    setA2ADialogOpen(true);
    setSelectedA2AAgent(null);
    void loadA2AContext();
  }

  async function handleCopyMessage(message: Message) {
    try {
      await writeClipboardText(getMessageCopyText(message));
      setCopiedMessageId(message.id);
      if (copyFeedbackTimeoutRef.current !== null) {
        window.clearTimeout(copyFeedbackTimeoutRef.current);
      }
      copyFeedbackTimeoutRef.current = window.setTimeout(() => {
        setCopiedMessageId("");
        copyFeedbackTimeoutRef.current = null;
      }, 1600);
    } catch {
      setTransportError("Unable to copy this message right now.");
    }
  }

  function handleComposerKeyDown(
    event: React.KeyboardEvent<HTMLDivElement>,
  ) {
    if (shortcutQuery) {
      if (event.key === "ArrowDown" && matchingQuickCommands.length > 0) {
        event.preventDefault();
        setActiveQuickCommandIndex((current) =>
          (current + 1) % matchingQuickCommands.length,
        );
        return;
      }
      if (event.key === "ArrowUp" && matchingQuickCommands.length > 0) {
        event.preventDefault();
        setActiveQuickCommandIndex((current) =>
          (current - 1 + matchingQuickCommands.length) % matchingQuickCommands.length,
        );
        return;
      }
      if (
        (event.key === "Enter" || event.key === "Tab") &&
        matchingQuickCommands[activeQuickCommandIndex]
      ) {
        event.preventDefault();
        selectQuickCommand(matchingQuickCommands[activeQuickCommandIndex]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        shortcutQueryRef.current = null;
        setShortcutQuery(null);
        return;
      }
    }
    if (event.key === "Backspace" && removeTokenBeforeCaret()) {
      event.preventDefault();
      return;
    }
    if (!composerExpanded && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSubmit();
      return;
    }
    if (
      composerExpanded &&
      event.key === "Enter" &&
      (event.metaKey || event.ctrlKey)
    ) {
      event.preventDefault();
      handleSubmit();
      return;
    }
    if (composerExpanded && event.key === "Enter") {
      event.preventDefault();
      const selection = window.getSelection();
      const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
      if (!range) return;
      range.deleteContents();
      const lineBreak = document.createTextNode("\n");
      range.insertNode(lineBreak);
      range.setStartAfter(lineBreak);
      range.collapse(true);
      selection?.removeAllRanges();
      selection?.addRange(range);
      const composer = composerInputRef.current;
      if (composer) {
        const value = (composer.textContent || "").replace(/\u00a0/g, " ");
        composerDomValueRef.current = value;
        setInputVal(value);
        updateShortcutQuery(value, getComposerCaretOffset());
      }
    }
  }

  function toggleMessageExpanded(messageId: string) {
    setExpandedMessageIds((current) => ({
      ...current,
      [messageId]: !current[messageId],
    }));
  }

  function toggleModifiedFilesExpanded(messageId: string) {
    setExpandedModifiedFileLists((current) => ({
      ...current,
      [messageId]: !current[messageId],
    }));
  }

  function setDynamicDrawerTabsForConversation(
    conversationId: string,
    updater: (current: DynamicDrawerTab[]) => DynamicDrawerTab[],
  ) {
    setDrawerTabsByConversation((current) => {
      const existing = current[conversationId] || { activeTab: "workflow", tabs: [] };
      return {
        ...current,
        [conversationId]: {
          ...existing,
          tabs: updater(existing.tabs),
        },
      };
    });
  }

  function setWorkflowDrawerTab(tab: WorkflowDrawerTab) {
    if (!activeConvId) {
      return;
    }
    setDrawerTabsByConversation((current) => {
      const existing = current[activeConvId] || { activeTab: "workflow", tabs: [] };
      return {
        ...current,
        [activeConvId]: { ...existing, activeTab: tab },
      };
    });
  }

  function openModifiedFile(path: string) {
    const normalizedPath = path.trim();
    if (!normalizedPath) {
      return;
    }
    const tabId = `file:${encodeURIComponent(normalizedPath)}` as const;
    const conversationId = activeConvId;
    if (!conversationId) {
      return;
    }
    setWorkflowDrawerOpen(true);
    setWorkflowDrawerTab(tabId);
    setDynamicDrawerTabsForConversation(conversationId, (current) => {
      const existingTab = current.find((tab) => tab.id === tabId);
      if (existingTab) {
        return current.map((tab) =>
          tab.id === tabId
            ? { ...tab, refreshVersion: tab.refreshVersion + 1, error: "" }
            : tab,
        );
      }
      const title = normalizedPath.split("/").filter(Boolean).pop() || normalizedPath;
      return [
        ...current,
        {
          id: tabId,
          path: normalizedPath,
          title,
          type: "text",
          content: "",
          loading: false,
          refreshVersion: 0,
          settledVersion: -1,
          error: "",
        },
      ];
    });
  }

  useEffect(() => {
    if (!activeConvId || !pendingHtmlPreview) {
      return;
    }
    openModifiedFile(pendingHtmlPreview.path);
    consumePendingHtmlPreview(activeConvId, pendingHtmlPreview.messageId);
  }, [activeConvId, pendingHtmlPreview?.messageId, pendingHtmlPreview?.path]);

  function closeDynamicDrawerTab(tabId: DynamicDrawerTab["id"]) {
    const tabIndex = dynamicDrawerTabs.findIndex((tab) => tab.id === tabId);
    if (workflowDrawerTab === tabId) {
      const remainingTabs = dynamicDrawerTabs.filter((tab) => tab.id !== tabId);
      setWorkflowDrawerTab(remainingTabs[tabIndex]?.id || remainingTabs[tabIndex - 1]?.id || "workflow");
    }
    if (activeConvId) {
      setDynamicDrawerTabsForConversation(activeConvId, (current) =>
        current.filter((tab) => tab.id !== tabId),
      );
    }
  }

  const attachmentUploadPending = pendingAttachments.some(
    (attachment) => attachment.status !== "ready",
  );
  const sendDisabled =
    (!inputVal.trim() && pendingAttachments.length === 0) ||
    attachmentUploadPending ||
    isConversationBusy(activeConversation);
  const activeMessages = (activeConversation?.messages || []).filter(
    (message) => showDelegateTools || message.kind !== "delegate-tools",
  );
  const composerPlaceholder = activeConversation?.pendingClarify?.awaitingText
    ? "Answer clarify prompt... 输入你的补充说明"
    : "Ask Aegis anything... 触发关键词：'钓鱼邮件', '勒索病毒', '敏感泄露'...";
  const templatesByTag = promptTemplates.reduce<
    Record<string, PromptTemplate[]>
  >((groups, template) => {
    (groups[template.tag] ||= []).push(template);
    return groups;
  }, {});
  return (
    <div
      ref={chatWorkspaceRef}
      className={`flex bg-[#020408] items-stretch overflow-hidden text-xs ${
        workflowDrawerOpen
          ? "fixed inset-0 z-50 h-screen w-screen"
          : "h-full w-full"
      } ${workflowDrawerResizing ? "aegis-chat-workspace--resizing" : ""}`}
    >
      {!workflowDrawerOpen ? (
        <div
          className={`${sidebarCollapsed ? "w-16" : "w-80"} border-r border-slate-800 bg-[#05080F] flex flex-col pt-4 shrink-0 z-10 transition-[width] duration-200`}
        >
          <div
            className={`${sidebarCollapsed ? "px-2 pb-3" : "px-4 pb-3"} border-b border-slate-800 space-y-3`}
          >
            <div
              className={`flex ${sidebarCollapsed ? "flex-col gap-2" : "justify-between items-center"}`}
            >
              {!sidebarCollapsed ? (
                <span className="text-[10px] font-mono tracking-widest font-bold text-slate-500 uppercase">
                  CENTRAL ARCHIVE
                </span>
              ) : null}
              <div
                className={`flex ${sidebarCollapsed ? "flex-col items-center gap-2" : "items-center gap-2 ml-auto"}`}
              >
                <button
                  type="button"
                  aria-label={
                    sidebarCollapsed
                      ? "Expand session sidebar"
                      : "Collapse session sidebar"
                  }
                  onClick={() => setSidebarCollapsed((current) => !current)}
                  className="p-2 rounded border border-slate-800 bg-[#080C14] text-slate-400 hover:text-cyan-300 hover:border-cyan-900/50 transition-all"
                  title={
                    sidebarCollapsed
                      ? "Expand session sidebar"
                      : "Collapse session sidebar"
                  }
                >
                  {sidebarCollapsed ? (
                    <ChevronRight className="h-4 w-4" />
                  ) : (
                    <ChevronLeft className="h-4 w-4" />
                  )}
                </button>
                <button
                  type="button"
                  aria-label="Open workflow visualization"
                  aria-pressed={workflowDrawerOpen}
                  onClick={() => {
                    setWorkflowFullscreen(false);
                    setWorkflowDrawerOpen(true);
                  }}
                  className="p-2 rounded border border-slate-800 bg-[#080C14] text-slate-400 hover:text-cyan-300 hover:border-cyan-900/50 transition-all"
                  title="Open session workflow"
                >
                  <Workflow className="h-4 w-4" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  aria-label="Open A2A agents"
                  aria-pressed={a2aDialogOpen}
                  onClick={openA2AAgents}
                  className="p-2 rounded border border-slate-800 bg-[#080C14] text-slate-400 hover:text-cyan-300 hover:border-cyan-900/50 transition-all"
                  title="Browse A2A agents"
                >
                  <Bot className="h-4 w-4" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  aria-label="新建对话"
                  onClick={handleCreateNewConversation}
                  className="rounded border border-slate-800 bg-[#080C14] p-2 text-cyan-400 transition-all hover:border-cyan-900/50 hover:text-cyan-300"
                  title="New chat thread"
                >
                  <Plus className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            </div>
            {!sidebarCollapsed ? (
              <div className="relative">
                <span className="aegis-status-indicator aegis-status-indicator--success absolute top-1/2 left-3 -translate-y-1/2 animate-pulse" />
                <div className="text-white text-xs pl-7 py-2 bg-[#080C14] border border-slate-800 rounded font-mono">
                  Aegis Coordinator:{" "}
                  <strong className="aegis-status-text--success font-bold">ONLINE</strong>
                </div>
              </div>
            ) : (
              <div className="flex justify-center">
                <span className="aegis-status-indicator aegis-status-indicator--success aegis-status-glow--success" />
              </div>
            )}
          </div>

          {!sidebarCollapsed ? (
            <div className="flex-1 overflow-y-auto p-2 space-y-1 scrollbar-thin">
              {conversations.length === 0 ? (
                <div className="text-center p-6 text-slate-500 font-mono text-[11px]">
                  No active threads
                </div>
              ) : (
                conversations.map((conversation) => {
                  const isActive = conversation.id === activeConvId;
                  return (
                    <div
                      key={conversation.id}
                      onClick={() => setActiveConversation(conversation.id)}
                      className={`group p-3 rounded-lg cursor-pointer transition-all ${
                        isActive
                          ? "aegis-conversation--active border text-white shadow-md"
                          : "hover:bg-[#03060C] text-slate-500 hover:text-slate-300 border border-transparent"
                      }`}
                    >
                      <div className="flex justify-between items-start gap-2">
                        <div className="flex-1 min-w-0">
                          <div
                            className={`font-semibold text-xs ${isActive ? "text-cyan-400" : "text-slate-300 group-hover:text-white"} truncate`}
                          >
                            {conversation.title}
                          </div>
                          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500 font-mono">
                            <Clock className="h-3 w-3 text-slate-600" />{" "}
                            {conversation.timestamp}
                            {conversation.pendingApproval ? (
                              <span className="aegis-status-badge aegis-status-badge--warning aegis-status-badge--compact">
                                approval
                              </span>
                            ) : null}
                            {conversation.pendingClarify ? (
                              <span className="rounded border border-cyan-900/40 bg-cyan-950/20 px-1.5 py-0.5 text-[9px] font-bold uppercase text-cyan-300">
                                clarify
                              </span>
                            ) : null}
                            {!conversation.pendingApproval &&
                            !conversation.pendingClarify &&
                            conversation.lastKnownRunState === "running" ? (
                              <span className="aegis-status-badge aegis-status-badge--success aegis-status-badge--compact">
                                running
                              </span>
                            ) : null}
                            {conversation.hasUnread ? (
                              <span className="rounded border border-cyan-900/40 bg-cyan-950/20 px-1.5 py-0.5 text-[9px] font-bold uppercase text-cyan-300">
                                new
                              </span>
                            ) : null}
                          </div>
                        </div>
                        <button
                          type="button"
                          aria-label={`Delete conversation ${conversation.title}`}
                          onClick={(event) =>
                            handleDeleteConversation(conversation.id, event)
                          }
                          className="text-slate-600 hover:text-rose-400 opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-slate-800 transition-all shrink-0"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-start gap-3 pt-4">
              <div className="w-10 h-10 rounded-xl border border-slate-800 bg-[#080C14] text-cyan-400 font-mono flex items-center justify-center">
                {conversations.length}
              </div>
              <div className="text-[9px] font-mono tracking-[0.3em] text-slate-600 [writing-mode:vertical-rl] rotate-180">
                HISTORY
              </div>
            </div>
          )}

          <div className="p-3 border-t border-slate-800 bg-[#03060C]">
            <button
              type="button"
              aria-label="Clear local chat cache"
              onClick={handleClearHistory}
              className={`${
                sidebarCollapsed ? "w-10 h-10 mx-auto" : "w-full py-1.5"
              } aegis-btn aegis-btn--danger font-medium rounded text-center flex items-center justify-center gap-1.5 text-[11px]`}
            >
              <Trash2 className="h-3 w-3" />
              {!sidebarCollapsed ? <span>清空运行环境缓存</span> : null}
            </button>
          </div>
        </div>
      ) : null}

      {workflowDrawerOpen ? (
        <aside
          className="aegis-chat-drawer order-1 flex h-full min-w-0 shrink-0 flex-col"
          style={{
            width: workflowFullscreen
              ? "100%"
              : `calc(${workflowDrawerWidth}% - 5px)`,
          }}
          data-testid="chat-workflow-drawer"
        >
          <header className="aegis-chat-drawer__header">
            <div
              aria-label="Chat workspace panels"
              className="aegis-chat-drawer__tabs"
              role="tablist"
            >
              <button
                aria-controls="chat-workflow-panel"
                aria-selected={workflowDrawerTab === "workflow"}
                className={`aegis-chat-drawer__tab ${
                  workflowDrawerTab === "workflow"
                    ? "aegis-chat-drawer__tab--active"
                    : ""
                }`}
                id="chat-workflow-tab"
                onClick={() => setWorkflowDrawerTab("workflow")}
                role="tab"
                type="button"
              >
                <Workflow aria-hidden="true" className="h-3.5 w-3.5" />
                <span>SESSION WORKFLOW</span>
              </button>
              {dynamicDrawerTabs.map((tab) => {
                const isActive = workflowDrawerTab === tab.id;
                const tabTitle = tab.title.split("/").filter(Boolean).pop() || tab.title;
                return (
                  <div
                    key={tab.id}
                    className={`aegis-chat-drawer__tab-group ${
                      isActive ? "aegis-chat-drawer__tab-group--active" : ""
                    }`}
                  >
                    <button
                      aria-controls={`chat-drawer-panel-${encodeURIComponent(tab.id)}`}
                      aria-selected={isActive}
                      className={`aegis-chat-drawer__tab ${
                        isActive ? "aegis-chat-drawer__tab--active" : ""
                      }`}
                      id={`chat-drawer-tab-${encodeURIComponent(tab.id)}`}
                      onClick={() => setWorkflowDrawerTab(tab.id)}
                      role="tab"
                      type="button"
                    >
                      <FileText aria-hidden="true" className="h-3.5 w-3.5" />
                      <span>{tabTitle}</span>
                    </button>
                    <button
                      aria-label={`Close file preview ${tabTitle}`}
                      className="aegis-chat-drawer__tab-close"
                      onClick={() => closeDynamicDrawerTab(tab.id)}
                      title={`Close ${tabTitle}`}
                      type="button"
                    >
                      <X aria-hidden="true" className="h-3 w-3" />
                    </button>
                  </div>
                );
              })}
            </div>
            <div className="ml-auto flex shrink-0 items-center gap-1">
              <button
                type="button"
                aria-label="Enter workspace fullscreen"
                onClick={() => setWorkflowFullscreen(true)}
                className="aegis-chat-drawer__icon-button"
                disabled={workflowFullscreen}
                title="Fullscreen"
              >
                <Maximize2 className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                aria-label="Exit workspace fullscreen"
                onClick={() => setWorkflowFullscreen(false)}
                className="aegis-chat-drawer__icon-button"
                disabled={!workflowFullscreen}
                title="Restore split view"
              >
                <Minimize2 className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                aria-label="Close workflow visualization"
                onClick={closeWorkflow}
                className="aegis-chat-drawer__icon-button"
                title="Close workspace drawer"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </header>
          <div className="min-h-0 flex-1">
            <div
              aria-labelledby="chat-workflow-tab"
              className="h-full"
              hidden={workflowDrawerTab !== "workflow"}
              id="chat-workflow-panel"
              role="tabpanel"
            >
              <SessionWorkflow
                conversation={activeConversation}
                fullscreen={workflowFullscreen}
                onFullscreenChange={setWorkflowFullscreen}
                showHeader={false}
              />
            </div>
            {dynamicDrawerTabs.map((tab) => (
              <div
                key={tab.id}
                aria-labelledby={`chat-drawer-tab-${encodeURIComponent(tab.id)}`}
                className="aegis-drawer-preview h-full"
                hidden={workflowDrawerTab !== tab.id}
                id={`chat-drawer-panel-${encodeURIComponent(tab.id)}`}
                role="tabpanel"
              >
                {tab.error && tab.content ? (
                  <div className="aegis-drawer-preview__refresh-error" role="alert">
                    {tab.error}
                  </div>
                ) : null}
                <DrawerFilePreview
                  active={workflowDrawerTab === tab.id}
                  content={tab.content}
                  error={tab.error}
                  loading={tab.loading}
                  onComposerInsert={insertAgent2UIIntoComposer}
                  title={tab.title}
                  type={tab.type}
                />
              </div>
            ))}
          </div>
        </aside>
      ) : null}

      {workflowDrawerOpen && !workflowFullscreen ? (
        <div
          aria-label="Resize chat workspace panels"
          aria-orientation="vertical"
          aria-valuemax={Math.round(
            drawerWidthBounds(chatWorkspaceRef.current?.clientWidth || window.innerWidth)
              .max,
          )}
          aria-valuemin={Math.round(
            drawerWidthBounds(chatWorkspaceRef.current?.clientWidth || window.innerWidth)
              .min,
          )}
          aria-valuenow={Math.round(workflowDrawerWidth)}
          className="aegis-chat-drawer__splitter order-2"
          onKeyDown={handleDrawerResizeKeyDown}
          onPointerDown={handleDrawerResizePointerDown}
          onPointerMove={handleDrawerResizePointerMove}
          onPointerUp={handleDrawerResizePointerUp}
          onPointerCancel={handleDrawerResizePointerUp}
          role="separator"
          tabIndex={0}
        />
      ) : null}

      {!workflowFullscreen ? (
        <div
          data-testid="chat-workspace"
          className={`${workflowDrawerOpen ? "order-3 shrink-0" : "flex-1"} flex flex-col h-full min-w-0 bg-[#020408] relative ${composerExpanded ? "pb-32" : "pb-16"}`}
          style={
            workflowDrawerOpen
              ? { width: `calc(${100 - workflowDrawerWidth}% - 5px)` }
              : undefined
          }
        >
          <div className="min-h-16 p-4 border-b border-slate-800 bg-[#03060C] flex items-center gap-3">
            <h3
              className="shrink-0 max-w-[34%] truncate text-sm font-bold text-white uppercase italic"
              title={
                activeConversation ? activeConversation.title : "安全事件会话"
              }
            >
              {activeConversation ? activeConversation.title : "安全事件会话"}
            </h3>
            <button
              type="button"
              data-testid="session-status-ticker"
              aria-label={`Open session workflow. Latest status: ${sessionStatus}`}
              onClick={openWorkflowFromSessionStatus}
              onKeyDown={handleSessionStatusKeyDown}
              className="aegis-session-status-ticker min-w-0 flex-1 rounded border border-cyan-950/70 bg-cyan-950/10 px-2.5 py-1.5 text-left font-mono text-[10px] text-cyan-200 transition-colors hover:border-cyan-700/70 hover:bg-cyan-950/25 focus-visible:border-cyan-400 focus-visible:outline-none"
              title="Open session workflow"
            >
              <span
                className={`aegis-session-status-ticker__viewport aegis-session-status-ticker__viewport--${sessionStatusPhase}`}
                aria-live="polite"
              >
                {sessionStatusPhase !== "scroll" ? (
                  <span
                    key={sessionStatus}
                    className="aegis-session-status-ticker__announcement"
                  >
                    {renderSessionStatus(sessionStatus)}
                  </span>
                ) : (
                  <span
                    key={sessionStatus}
                    className="aegis-session-status-ticker__track"
                  >
                    <span className="aegis-session-status-ticker__item">
                      {renderSessionStatus(sessionStatus)}
                    </span>
                    <span
                      className="aegis-session-status-ticker__item"
                      aria-hidden="true"
                    >
                      {renderSessionStatus(sessionStatus)}
                    </span>
                  </span>
                )}
              </span>
            </button>
            <div className="shrink-0 text-[10px] font-mono text-slate-500 flex items-center gap-3">
              <button
                type="button"
                aria-label="Toggle delegate tool messages"
                onClick={() => setShowDelegateTools((current) => !current)}
                className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 transition-all ${
                  showDelegateTools
                    ? "border-cyan-900/60 bg-cyan-950/30 text-cyan-300"
                    : "border-slate-800 bg-[#080C14] text-slate-400 hover:text-cyan-300"
                }`}
              >
                {showDelegateTools ? (
                  <Eye className="h-3.5 w-3.5" />
                ) : (
                  <EyeOff className="h-3.5 w-3.5" />
                )}
                <span>
                  {showDelegateTools
                    ? "DELEGATE TOOLS ON"
                    : "DELEGATE TOOLS OFF"}
                </span>
              </button>
              <button
                type="button"
                aria-label="Open prompt templates"
                onClick={() => void openPromptTemplateDrawer()}
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-slate-800 bg-[#080C14] text-slate-400 transition-all hover:border-cyan-900/50 hover:bg-slate-800/80 hover:text-cyan-300"
                title="Prompt templates"
              >
                <PanelRightOpen className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
              <button
                type="button"
                aria-label={
                  markdownRenderingEnabled
                    ? "Disable Markdown rendering"
                    : "Enable Markdown rendering"
                }
                aria-pressed={markdownRenderingEnabled}
                onClick={() =>
                  setMarkdownRenderingEnabled((current) => !current)
                }
                className={`inline-flex h-7 w-7 items-center justify-center rounded border transition-all ${
                  markdownRenderingEnabled
                    ? "border-cyan-900/60 bg-cyan-950/30 text-cyan-300 hover:border-cyan-700 hover:text-cyan-100"
                    : "border-slate-800 bg-[#080C14] text-slate-500 hover:border-cyan-900/50 hover:text-cyan-300"
                }`}
                title={
                  markdownRenderingEnabled
                    ? "Disable Markdown rendering"
                    : "Enable Markdown rendering"
                }
              >
                <Code2 className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          </div>

          {transportError ? (
            <div className="aegis-alert aegis-alert--warning flex items-center justify-between border-x-0 border-t-0 px-4 py-3 text-sm">
              <span>{transportError}</span>
              {activeConversation?.sessionId ? (
                <button
                  onClick={handleResume}
                  className="text-xs font-bold text-cyan-300 hover:text-cyan-200"
                >
                  Resume Session
                </button>
              ) : null}
            </div>
          ) : null}

          <div className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-thin">
            {activeMessages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-center p-6 max-w-xl mx-auto space-y-4 my-auto">
                <div className="h-11 w-11 bg-cyan-500 shadow-[0_0_15px_rgba(6,182,212,0.4)] rounded-lg flex items-center justify-center shrink-0">
                  <Layers className="h-5 w-5 text-white animate-pulse" />
                </div>
                <div className="space-y-1">
                  <h4 className="text-sm font-bold text-white uppercase italic tracking-wider">
                    Aegis 协同中枢智能对话
                  </h4>
                  <p className="text-[11px] text-slate-400 leading-relaxed max-w-md">
                    向 Aegis 提交任何风险分析请求。Aegis
                    会为活跃会话保持独立实时通道，持续接收主 Agent、Delegate
                    Agent 与授权批准事件。
                  </p>
                </div>
              </div>
            ) : null}

            {activeMessages.map((message) => {
              if (message.kind === "delegate-event") {
                return (
                  <div key={message.id} className="flex justify-center">
                    <div className="aegis-delegate-event">{message.text}</div>
                  </div>
                );
              }

              const isDelegateTools = message.kind === "delegate-tools";
              const isMainTools = message.kind === "main-tools";
              const isExpanded = !!expandedMessageIds[message.id];
              const modifiedFiles = message.modifiedFiles || [];
              const modifiedFilesExpanded = !!expandedModifiedFileLists[message.id];
              const modifiedFilesListId = `modified-files-${message.id}`;
              const isAegis = message.sender === "aegis";
              const agentBadge = isAegis
                ? message.source === "delegate"
                  ? "DG"
                  : "AE"
                : "OP";
              const badgeClassName = isAegis
                ? message.source === "delegate"
                  ? "aegis-delegate-badge"
                  : "bg-[#080C14] border-slate-800 text-cyan-400"
                : "bg-[#03060C] border-slate-800 text-slate-400";
              const bubbleClassName = isAegis
                ? message.source === "delegate"
                  ? "aegis-delegate-bubble"
                  : "bg-cyan-950/10 border border-cyan-800/60 shadow-[inset_0_0_0_1px_rgba(34,211,238,0.06)]"
                : "bg-[#080C14] border border-slate-800/80";
              const actorLabelClassName = isAegis
                ? message.source === "delegate"
                  ? "aegis-delegate-label"
                  : "text-cyan-300"
                : "text-slate-300";
              const actorLabel = isAegis
                ? message.source === "delegate"
                  ? message.srcagent || "Delegate Agent"
                  : "Aegis Co-Pilot"
                : "Operator";

              return (
                <div
                  key={message.id}
                  className="w-full space-y-3"
                >
                  <div
                    data-testid="chat-message"
                    data-sender={message.sender}
                    className={`flex gap-3 w-full ${isAegis ? "mr-auto" : "ml-auto flex-row-reverse"}`}
                  >
                    <div
                      className={`h-8 w-8 rounded-lg shrink-0 flex items-center justify-center border text-[11px] font-bold font-mono ${badgeClassName}`}
                    >
                      {agentBadge}
                    </div>

                  <div className="space-y-2 flex-1 min-w-0">
                    <div
                      className={`flex items-center gap-2 ${isAegis ? "" : "justify-end"}`}
                    >
                      <span
                        className={`font-bold text-[11px] ${actorLabelClassName}`}
                      >
                        {actorLabel}
                      </span>
                      <span className="text-[9px] text-slate-500 font-mono">
                        {message.timestamp}
                      </span>
                    </div>

                    <div
                      className={`relative p-3.5 pr-12 rounded-lg text-slate-300 leading-relaxed ${bubbleClassName}`}
                    >
                      {isDelegateTools ? (
                        <div className="space-y-3">
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <div className="text-sm font-semibold text-cyan-200">
                                Delegate Tool Activity
                              </div>
                              <div className="text-[10px] font-mono text-slate-500">
                                {(message.delegateTools || []).length} tool call
                                {(message.delegateTools || []).length === 1
                                  ? ""
                                  : "s"}
                              </div>
                            </div>
                            <button
                              type="button"
                              aria-label={
                                isExpanded
                                  ? "Collapse delegate tool details"
                                  : "Expand delegate tool details"
                              }
                              onClick={() => toggleMessageExpanded(message.id)}
                              className="inline-flex items-center gap-1 rounded border border-slate-800 bg-[#080C14] px-2 py-1 text-[10px] font-mono text-slate-400 hover:text-cyan-300"
                            >
                              {isExpanded ? (
                                <ChevronUp className="h-3.5 w-3.5" />
                              ) : (
                                <ChevronDown className="h-3.5 w-3.5" />
                              )}
                              <span>
                                {isExpanded ? "HIDE DETAILS" : "SHOW DETAILS"}
                              </span>
                            </button>
                          </div>
                          {isExpanded ? (
                            <div className="space-y-2">
                              {(message.delegateTools || []).map((toolCall) => (
                                <div
                                  key={toolCall.id}
                                  className="rounded-lg border border-slate-800 bg-[#080C14] p-3"
                                >
                                  <div className="flex items-center justify-between gap-3">
                                    <span className="text-[11px] font-bold text-white">
                                      {toolCall.toolName}
                                    </span>
                                    <span
                                      className={`text-[9px] font-mono uppercase ${
                                        toolCall.status === "completed"
                                          ? "aegis-status-text--success"
                                          : "aegis-status-text--accent"
                                      }`}
                                    >
                                      {toolCall.status}
                                    </span>
                                  </div>
                                  <div className="mt-2 whitespace-pre-wrap break-words rounded border border-slate-800/80 bg-[#020408] px-2 py-1.5 text-[10px] font-mono text-slate-300">
                                    {toolCall.argsPreview || "(no args)"}
                                  </div>
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ) : isMainTools ? (
                        <div data-testid="message-chain" className="space-y-3">
                          <div className="text-[10px] font-mono text-cyan-400 uppercase tracking-widest flex items-center gap-1.5 font-bold">
                            <Layers className="h-3.5 w-3.5 text-cyan-500" />{" "}
                            Orchestration Chain
                          </div>
                          <div className="flex gap-3 overflow-x-auto pb-2 relative scrollbar-thin">
                            {(message.chainSteps || []).map((step) => (
                              <div
                                key={
                                  step.id ||
                                  `${step.agentName}-${step.timestamp}`
                                }
                                className="min-w-[240px] max-w-[240px] p-2.5 bg-[#080C14] border border-slate-800 rounded-lg relative overflow-hidden flex flex-col justify-between shrink-0"
                              >
                                <div className="absolute top-0 left-0 right-0 h-[2px] bg-cyan-500" />
                                <div>
                                  <div className="font-bold text-white flex items-center gap-1.5 text-[11px] truncate">
                                    <span
                                      className={`h-1.5 w-1.5 rounded-full ${step.type === "agent" ? "bg-cyan-400" : "bg-purple-500"}`}
                                    />
                                    {step.agentName}
                                  </div>
                                  <p className="text-[10px] text-slate-400 mt-1 pb-1 line-clamp-2 leading-normal">
                                    {step.message}
                                  </p>
                                </div>
                                <div className="flex justify-between items-center border-t border-slate-800 pt-1.5 mt-2 text-[9px] font-mono">
                                  <span
                                    className={`font-bold uppercase flex items-center gap-0.5 ${
                                      step.status === "Completed"
                                        ? "aegis-status-text--success"
                                        : step.status === "Failed"
                                          ? "aegis-status-text--danger"
                                          : "aegis-status-text--accent"
                                    }`}
                                  >
                                    <CheckCircle className="h-2.5 w-2.5" />{" "}
                                    {step.status}
                                  </span>
                                  <span className="text-slate-500">
                                    {step.timestamp}
                                  </span>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {message.attachments?.length ? (
                            <div
                              className="flex flex-wrap gap-1.5"
                              data-testid="message-attachments"
                            >
                              {message.attachments.map((attachment) => (
                                <span
                                  key={attachment.id}
                                  className="inline-flex max-w-full items-center gap-1.5 rounded border border-cyan-900/50 bg-cyan-950/20 px-2 py-1 font-mono text-[10px] text-cyan-200"
                                >
                                  {attachment.kind === "image" ? (
                                    <ImageIcon className="h-3 w-3 shrink-0 text-cyan-400" />
                                  ) : (
                                    <FileText className="h-3 w-3 shrink-0 text-slate-400" />
                                  )}
                                  <span className="truncate">
                                    {attachment.display_name}
                                  </span>
                                </span>
                              ))}
                            </div>
                          ) : null}
                          {message.text ? (
                            <div
                              data-testid="message-text"
                              data-markdown-rendered={markdownRenderingEnabled}
                              className={`${markdownRenderingEnabled ? "aegis-markdown" : "whitespace-pre-wrap"} text-sm leading-relaxed select-text cursor-text`}
                            >
                              {message.sender === "user" &&
                              HAS_SHORTCUT_TOKEN_PATTERN.test(message.text) ? (
                                <span className="whitespace-pre-wrap">
                                  {renderShortcutTokens(message.text)}
                                </span>
                              ) : markdownRenderingEnabled ? (
                                <ChatMarkdown content={message.text} />
                              ) : (
                                message.text
                              )}
                            </div>
                          ) : null}
                        </div>
                      )}
                      <button
                        type="button"
                        aria-label="Copy message"
                        onClick={() => void handleCopyMessage(message)}
                        className="absolute right-2 bottom-2 h-7 w-7 rounded border border-slate-800 bg-[#080C14] text-slate-400 hover:text-cyan-300 hover:border-cyan-900/50 transition-all flex items-center justify-center"
                        title="Copy message"
                      >
                        {copiedMessageId === message.id ? (
                          <Check className="h-3.5 w-3.5" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                  </div>
                  </div>
                  {isAegis && modifiedFiles.length ? (
                    <section
                      aria-label="Overview files"
                      className="aegis-modified-files"
                      data-testid="modified-files"
                    >
                      <button
                        aria-controls={modifiedFilesListId}
                        aria-expanded={modifiedFilesExpanded}
                        className="aegis-modified-files__header"
                        onClick={() => toggleModifiedFilesExpanded(message.id)}
                        type="button"
                      >
                        <FileText aria-hidden="true" className="h-3.5 w-3.5" />
                        <span>OVERVIEW FILES</span>
                        <span className="aegis-modified-files__count">{modifiedFiles.length}</span>
                      </button>
                      {modifiedFilesExpanded ? (
                        <div className="aegis-modified-files__list" id={modifiedFilesListId}>
                          {modifiedFiles.map((path) => (
                            <button
                              key={path}
                              className="aegis-modified-files__item"
                              onClick={() => openModifiedFile(path)}
                              title={`Open ${path}`}
                              type="button"
                            >
                              <FileText aria-hidden="true" className="h-3 w-3 shrink-0" />
                              <span>{path}</span>
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </section>
                  ) : null}
                </div>
              );
            })}

            <div ref={bottomRef} />
          </div>

          {activeConversation?.pendingApproval ? (
            <div className="aegis-alert aegis-alert--warning mx-4 mb-3 rounded-xl p-4">
              <div className="flex items-start gap-3">
                <ShieldAlert className="h-5 w-5 shrink-0 mt-0.5" />
                <div className="flex-1">
                  <div className="text-sm font-bold">
                    {activeConversation.pendingApproval.source === 'delegate'
                      ? `Delegate Approval · ${activeConversation.foregroundAgentName || 'remote agent'}`
                      : 'Approval Required'}
                  </div>
                  <div className="mt-1 text-xs">
                    {activeConversation.pendingApproval.description}
                  </div>
                  <div className="mt-2 rounded border border-current/30 bg-[#080C14] px-3 py-2 font-mono text-[11px]">
                    {activeConversation.pendingApproval.command}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      onClick={() => handleApproval("once")}
                      className="px-3 py-1.5 rounded bg-cyan-500 text-white font-bold text-xs"
                    >
                      Allow Once
                    </button>
                    {activeConversation.pendingApproval.allowSession !== false ? (
                      <button
                        onClick={() => handleApproval("session")}
                        className="px-3 py-1.5 rounded border border-slate-700 text-slate-200 font-bold text-xs"
                      >
                        Session
                      </button>
                    ) : null}
                    {activeConversation.pendingApproval.allowPermanent !== false ? (
                      <button
                        onClick={() => handleApproval("always")}
                        className="px-3 py-1.5 rounded border border-slate-700 text-slate-200 font-bold text-xs"
                      >
                        Always
                      </button>
                    ) : null}
                    <button
                      onClick={() => handleApproval("deny")}
                      className="aegis-btn aegis-btn--danger px-3 py-1.5 rounded font-bold text-xs"
                    >
                      Deny
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {activeConversation?.pendingClarify ? (
            <div className="mx-4 mb-3 rounded-xl border border-cyan-900/30 bg-cyan-950/20 p-4 text-cyan-100">
              <div className="flex items-start gap-3">
                <Clock className="h-5 w-5 text-cyan-400 shrink-0 mt-0.5" />
                <div className="flex-1">
                  <div className="text-sm font-bold text-cyan-200">
                    {activeConversation.pendingClarify.source === 'delegate'
                      ? `Delegate Clarification · ${activeConversation.foregroundAgentName || 'remote agent'}`
                      : 'Clarify Required'}
                  </div>
                  <div className="mt-1 text-xs text-cyan-100/90">
                    {activeConversation.pendingClarify.question}
                  </div>
                  {activeConversation.pendingClarify.awaitingText ? (
                    <div className="mt-3 rounded border border-cyan-900/20 bg-[#080C14] px-3 py-2 text-[11px] text-cyan-200">
                      Type your answer below. Your next message will be sent as
                      the clarify response.
                    </div>
                  ) : (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {activeConversation.pendingClarify.choices.map(
                        (choice) => (
                          <button
                            key={choice}
                            onClick={() => handleClarifyChoice(choice)}
                            className={`px-3 py-1.5 rounded border text-slate-200 font-bold text-xs ${delegateClarifySelection.includes(choice) ? 'border-cyan-400 bg-cyan-900/50' : 'border-slate-700'}`}
                          >
                            {choice}
                          </button>
                        ),
                      )}
                      {activeConversation.pendingClarify.multiSelect ? (
                        <button
                          onClick={handleClarifySubmit}
                          disabled={delegateClarifySelection.length === 0}
                          className="px-3 py-1.5 rounded bg-cyan-500 text-white font-bold text-xs disabled:opacity-40"
                        >
                          Submit Selection
                        </button>
                      ) : null}
                      <button
                        onClick={handleClarifyOther}
                        className="px-3 py-1.5 rounded bg-cyan-500 text-white font-bold text-xs"
                      >
                        Other
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : null}

          <div className="absolute bottom-0 left-0 right-0 z-10 border-t border-slate-800 bg-[#03060C] p-3">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="sr-only"
              aria-label="Upload chat attachments"
              onChange={(event) => queueAttachments(event.target.files || [])}
            />
            <div className="space-y-2">
              {pendingAttachments.length ? (
                <div className="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto rounded border border-cyan-950/60 bg-[#05080F] p-1.5">
                  {pendingAttachments.map((attachment) => (
                    <div
                      key={attachment.localId}
                      className={`group flex max-w-[220px] items-center gap-1.5 rounded border px-1.5 py-1 text-[10px] font-mono ${attachment.status === "failed" ? "aegis-alert aegis-alert--danger" : "border-slate-700 bg-[#080C14] text-slate-300"}`}
                    >
                      {attachment.previewUrl ? (
                        <img
                          src={attachment.previewUrl}
                          alt=""
                          className="h-5 w-5 rounded object-cover"
                        />
                      ) : (
                        <FileText className="h-3.5 w-3.5 shrink-0 text-slate-500" />
                      )}
                      <span className="max-w-[116px] truncate">
                        {attachment.file.name}
                      </span>
                      {attachment.status === "uploading" ? (
                        <LoaderCircle
                          className="h-3 w-3 shrink-0 animate-spin text-cyan-400"
                          aria-label="Uploading"
                        />
                      ) : null}
                      {attachment.status === "failed" ? (
                        <button
                          type="button"
                          onClick={() =>
                            retryPendingAttachment(attachment.localId)
                          }
                          className="aegis-status-text--danger hover:text-white"
                          title={attachment.error || "Retry upload"}
                        >
                          RETRY
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() =>
                          removePendingAttachment(attachment.localId)
                        }
                        className="text-slate-500 hover:text-white"
                        aria-label={`Remove ${attachment.file.name}`}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="flex items-start gap-2">
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isConversationBusy(activeConversation)}
                  className="shrink-0 rounded border border-slate-800 bg-[#05080F] p-2 text-slate-500 transition-all hover:bg-slate-800/80 hover:text-white active:scale-95 disabled:cursor-not-allowed disabled:opacity-50"
                  title="Attach images or files"
                >
                  <Paperclip className="h-4 w-4" />
                </button>

                <div className="relative min-w-0 flex-1">
                  <div className={`aegis-shortcut-composer ${composerExpanded ? "aegis-shortcut-composer--expanded" : ""}`}>
                    <div
                      ref={composerInputRef}
                      aria-autocomplete="list"
                      aria-activedescendant={
                        shortcutQuery && matchingQuickCommands.length > 0
                          ? `chat-quick-command-option-${activeQuickCommandIndex}`
                          : undefined
                      }
                      aria-controls={shortcutQuery ? "chat-quick-command-listbox" : undefined}
                      aria-expanded={Boolean(shortcutQuery)}
                      aria-label="Chat message"
                      aria-multiline="true"
                      aria-placeholder={composerPlaceholder}
                      aria-disabled={isConversationBusy(activeConversation)}
                      className="aegis-shortcut-composer__input"
                      contentEditable={!isConversationBusy(activeConversation)}
                      data-placeholder={composerPlaceholder}
                      onKeyDown={handleComposerKeyDown}
                      onKeyUp={rememberComposerCaret}
                      onInput={handleComposerInput}
                      onMouseUp={rememberComposerCaret}
                      onPaste={handleComposerPaste}
                      onSelect={handleComposerSelect}
                      role="combobox"
                      suppressContentEditableWarning
                    />
                  </div>
                  {shortcutQuery ? (
                    <div
                      id="chat-quick-command-listbox"
                      role="listbox"
                      aria-label="Available quick commands"
                      className="aegis-quick-command-menu"
                    >
                      {quickCommandsLoading ? (
                        <div className="aegis-quick-command-menu__status" role="status">
                          LOADING QUICK COMMANDS…
                        </div>
                      ) : null}
                      {quickCommandsError ? (
                        <div className="aegis-quick-command-menu__status aegis-quick-command-menu__status--error" role="alert">
                          {quickCommandsError}
                        </div>
                      ) : null}
                      {!quickCommandsLoading && !quickCommandsError && matchingQuickCommands.length === 0 ? (
                        <div className="aegis-quick-command-menu__status">NO MATCHING COMMANDS</div>
                      ) : null}
                      {!quickCommandsLoading && !quickCommandsError ? matchingQuickCommands.map((command, index) => (
                        <button
                          key={`${command.type}-${command.name}-${index}`}
                          id={`chat-quick-command-option-${index}`}
                          aria-selected={index === activeQuickCommandIndex}
                          className={`aegis-quick-command-menu__option ${index === activeQuickCommandIndex ? "aegis-quick-command-menu__option--active" : ""}`}
                          onMouseDown={(event) => {
                            event.preventDefault();
                            selectQuickCommand(command);
                          }}
                          role="option"
                          type="button"
                        >
                          <span className="aegis-quick-command-menu__icon"><QuickCommandTypeIcon type={command.type} /></span>
                          <span className="min-w-0 flex-1 text-left">
                            <span className="block truncate font-mono text-[11px] font-bold tracking-wide text-[var(--aegis-text)]">{command.name}</span>
                            <span className="block truncate text-[10px] text-[var(--aegis-text-muted)]">{command.desc || "No description"}</span>
                          </span>
                          <span className="aegis-quick-command-menu__type">{command.type}</span>
                        </button>
                      )) : null}
                    </div>
                  ) : null}
                  <button
                    type="button"
                    aria-label={
                      composerExpanded ? "Collapse composer" : "Expand composer"
                    }
                    onClick={() => setComposerExpanded((current) => !current)}
                    className="absolute bottom-2 right-2 z-20 h-6 w-6 rounded text-slate-500 hover:text-cyan-300 hover:bg-slate-800/70 transition-all flex items-center justify-center"
                    title={
                      composerExpanded ? "Collapse composer" : "Expand composer"
                    }
                  >
                    {composerExpanded ? (
                      <Minimize2 className="h-3.5 w-3.5" />
                    ) : (
                      <Maximize2 className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>

                <div className="group relative shrink-0">
                  <button
                    type="button"
                    aria-label={
                      a2uiEnabled
                        ? "Disable A2UI preview"
                        : "Enable A2UI preview"
                    }
                    aria-describedby="a2ui-preview-help"
                    aria-pressed={a2uiEnabled}
                    onClick={() => setA2uiEnabled((current) => !current)}
                    className={`inline-flex h-9 items-center gap-1.5 rounded border px-2 text-[10px] font-mono font-bold tracking-wide transition-all ${
                      a2uiEnabled
                        ? "border-cyan-700 bg-cyan-950/40 text-cyan-200"
                        : "border-slate-800 bg-[#05080F] text-slate-400 hover:border-cyan-900/50 hover:bg-slate-800/80 hover:text-cyan-300"
                    }`}
                    title="A2UI preview"
                  >
                    <PanelsTopLeft className="h-4 w-4" aria-hidden="true" />
                    <span>A2UI</span>
                  </button>
                  <span
                    id="a2ui-preview-help"
                    role="tooltip"
                    className="pointer-events-none absolute bottom-full left-0 z-30 mb-2 w-56 rounded border border-slate-700 bg-[#080C14] px-3 py-2 text-left text-[11px] leading-relaxed text-slate-300 opacity-0 shadow-xl transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                  >
                    Adds the A2UI quick-instruction prefix when this message
                    is sent.
                  </span>
                </div>

                <button
                  type="button"
                  onClick={handleSubmit}
                  disabled={sendDisabled}
                  className="aegis-send-button shrink-0"
                >
                  <Send className="h-3 w-3" /> 发送
                </button>
              </div>
            </div>
          </div>
          {promptTemplateDrawerOpen ? (
            <aside
              aria-label="Prompt templates"
              className="absolute inset-y-0 right-0 z-30 flex w-full max-w-md flex-col border-l border-slate-700 bg-[#05080F] shadow-[-20px_0_55px_rgba(0,0,0,0.45)]"
            >
              <header className="flex items-start justify-between gap-4 border-b border-slate-800 px-5 py-4">
                <div>
                  <p className="font-mono text-[10px] font-bold tracking-[0.16em] text-cyan-400">
                    USER PROMPT LIBRARY
                  </p>
                  <h4 className="mt-1 text-sm font-semibold text-white">
                    选择模版
                  </h4>
                  <p className="mt-1 text-[11px] text-slate-500">
                    点击后追加到当前消息草稿，不会自动发送。
                  </p>
                </div>
                <button
                  type="button"
                  aria-label="Close prompt templates"
                  onClick={() => setPromptTemplateDrawerOpen(false)}
                  className="rounded p-2 text-slate-500 hover:bg-slate-800 hover:text-white"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </header>
              <div className="flex-1 overflow-y-auto p-4 scrollbar-thin">
                {promptTemplateLoading ? (
                  <div className="py-10 text-center font-mono text-xs text-slate-500">
                    LOADING TEMPLATES…
                  </div>
                ) : null}
                {promptTemplateError ? (
                  <div
                    role="alert"
                    className="rounded border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-xs text-rose-200"
                  >
                    {promptTemplateError}
                  </div>
                ) : null}
                {!promptTemplateLoading &&
                !promptTemplateError &&
                promptTemplates.length === 0 ? (
                  <div className="rounded border border-dashed border-slate-700 p-6 text-center text-xs text-slate-500">
                    No templates yet. Create them from User Profile / Prompt
                    Template.
                  </div>
                ) : null}
                {!promptTemplateLoading && !promptTemplateError
                  ? Object.entries(templatesByTag).map(([tag, templates]) => (
                      <section
                        key={tag}
                        className="mb-5 last:mb-0"
                        aria-label={`${tag} templates`}
                      >
                        <h5 className="mb-2 flex items-center gap-2 font-mono text-[10px] font-bold tracking-widest text-cyan-400">
                          <span className="h-px flex-1 bg-cyan-950" />
                          {tag}
                          <span className="h-px flex-1 bg-cyan-950" />
                        </h5>
                        <div className="space-y-2">
                          {templates.map((template) => (
                            <button
                              key={template.id}
                              type="button"
                              onClick={() => appendPromptTemplate(template)}
                              className="w-full rounded-lg border border-slate-800 bg-[#03060C] p-3 text-left transition hover:border-cyan-700 hover:bg-cyan-950/20"
                            >
                              <div className="text-xs font-medium text-slate-200">
                                {template.desc || template.tag}
                              </div>
                              <div className="mt-1 line-clamp-3 whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-slate-500">
                                {template.prompt}
                              </div>
                            </button>
                          ))}
                        </div>
                      </section>
                    ))
                  : null}
              </div>
            </aside>
          ) : null}
        </div>
      ) : null}
      {a2aDialogOpen ? (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-[#020408]/80 p-4 backdrop-blur-sm"
          role="presentation"
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-label="A2A agents"
            className="flex max-h-[min(720px,calc(100vh-2rem))] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-[#05080F] shadow-[0_25px_90px_rgba(0,0,0,0.68)]"
          >
            <header className="flex items-start justify-between gap-4 border-b border-slate-800 px-5 py-4">
              <div>
                <p className="font-mono text-[10px] font-bold tracking-[0.16em] text-cyan-400">
                  A2A AGENT CONTEXT
                </p>
                <h4 className="mt-1 text-sm font-semibold text-white">
                  {selectedA2AAgent ? selectedA2AAgent.name : "Active Agents"}
                </h4>
                <p className="mt-1 text-[11px] text-slate-500">
                  {selectedA2AAgent
                    ? "Inspect the available capability set for this agent."
                    : "Live registry snapshot; opening this window does not send a chat message."}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  aria-label="Refresh A2A agents"
                  onClick={() => void loadA2AContext(true)}
                  disabled={a2aLoading || a2aRefreshing}
                  className="rounded border border-slate-800 bg-[#080C14] p-2 text-slate-400 transition hover:border-cyan-900/50 hover:text-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
                  title="Refresh A2A agents"
                >
                  <RefreshCw
                    className={`h-4 w-4 ${a2aRefreshing ? "animate-spin" : ""}`}
                  />
                </button>
                <button
                  type="button"
                  aria-label="Close A2A agents"
                  onClick={() => setA2ADialogOpen(false)}
                  className="rounded border border-slate-800 bg-[#080C14] p-2 text-slate-400 transition hover:border-slate-600 hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto p-5 scrollbar-thin">
              {a2aLoading ? (
                <div className="py-14 text-center font-mono text-xs text-slate-500">
                  LOADING AGENT CONTEXT…
                </div>
              ) : null}
              {a2aError ? (
                <div
                  role="alert"
                  className="rounded border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-xs text-rose-200"
                >
                  {a2aError}
                </div>
              ) : null}
              {!a2aLoading && !a2aError && a2aContext?.refresh_error ? (
                <div
                  role="alert"
                  className="mb-4 rounded border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-200"
                >
                  Showing the last successful snapshot. Refresh failed:{" "}
                  {a2aContext.refresh_error}
                </div>
              ) : null}
              {!a2aLoading && !a2aError && selectedA2AAgent ? (
                <div className="space-y-5">
                  <button
                    type="button"
                    aria-label="Back to agents"
                    onClick={() => setSelectedA2AAgent(null)}
                    className="inline-flex items-center gap-1.5 rounded border border-slate-800 bg-[#080C14] px-3 py-1.5 text-[11px] font-medium text-slate-300 transition hover:border-cyan-900/50 hover:text-cyan-300"
                  >
                    <ArrowLeft className="h-3.5 w-3.5" /> Back to agents
                  </button>
                  <div className="grid gap-3 rounded-lg border border-slate-800 bg-[#03060C] p-4 sm:grid-cols-2">
                    <div>
                      <p className="font-mono text-[10px] tracking-widest text-slate-500">
                        URL
                      </p>
                      <p className="mt-1 break-all text-xs text-slate-200">
                        {selectedA2AAgent.url || "Not provided"}
                      </p>
                    </div>
                    <div>
                      <p className="font-mono text-[10px] tracking-widest text-slate-500">
                        STATUS
                      </p>
                      <p
                        className={`mt-1 text-xs font-semibold ${selectedA2AAgent.available ? "aegis-status-text--success" : "aegis-status-text--warning"}`}
                      >
                        {selectedA2AAgent.status || "unknown"} ·{" "}
                        {selectedA2AAgent.available
                          ? "available"
                          : "unavailable"}
                      </p>
                    </div>
                    <div className="sm:col-span-2">
                      <p className="font-mono text-[10px] tracking-widest text-slate-500">
                        DESCRIPTION
                      </p>
                      <p className="mt-1 text-xs leading-relaxed text-slate-300">
                        {selectedA2AAgent.description ||
                          "No description provided."}
                      </p>
                    </div>
                    {selectedA2AAgent.error ? (
                      <div className="sm:col-span-2 rounded border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-xs text-rose-200">
                        {selectedA2AAgent.error}
                      </div>
                    ) : null}
                  </div>
                  <div>
                    <p className="font-mono text-[10px] font-bold tracking-[0.16em] text-cyan-400">
                      CAPABILITIES · {selectedA2AAgent.capabilities.length}
                    </p>
                    {selectedA2AAgent.capabilities.length === 0 ? (
                      <p className="mt-3 rounded border border-dashed border-slate-700 p-4 text-xs text-slate-500">
                        No capabilities were published by this agent.
                      </p>
                    ) : (
                      <ul className="mt-3 space-y-2">
                        {selectedA2AAgent.capabilities.map(
                          (capability, index) => (
                            <li
                              key={`${capability}-${index}`}
                              className="rounded border border-slate-800 bg-[#03060C] px-3 py-2 text-xs leading-relaxed text-slate-300"
                            >
                              {capability}
                            </li>
                          ),
                        )}
                      </ul>
                    )}
                  </div>
                </div>
              ) : null}
              {!a2aLoading &&
              !a2aError &&
              !selectedA2AAgent &&
              a2aContext &&
              a2aContext.agents.length === 0 ? (
                <div className="rounded border border-dashed border-slate-700 p-8 text-center text-xs text-slate-500">
                  No active A2A agents are available in the current registry
                  snapshot.
                </div>
              ) : null}
              {!a2aLoading &&
              !a2aError &&
              !selectedA2AAgent &&
              a2aContext &&
              a2aContext.agents.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {a2aContext.agents.map((agent) => (
                    <button
                      key={`${agent.name}-${agent.url || ""}`}
                      type="button"
                      aria-label={`View agent ${agent.name}`}
                      onClick={() => setSelectedA2AAgent(agent)}
                      className="aegis-a2a-agent-card group flex min-h-44 flex-col rounded-lg p-4 text-left"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-sm font-semibold text-white group-hover:text-cyan-200">
                          {agent.name}
                        </span>
                        <span
                          className={`aegis-status-badge ${agent.available ? "aegis-status-badge--success" : "aegis-status-badge--warning"}`}
                        >
                          {agent.available ? "available" : "unavailable"}
                        </span>
                      </div>
                      <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-slate-400">
                        {agent.description || "No description provided."}
                      </p>
                      <div className="mt-auto flex items-center justify-between border-t border-slate-800 pt-3 font-mono text-[10px] text-slate-500">
                        <span>{agent.status || "unknown"}</span>
                        <span>{agent.capabilities.length} capabilities</span>
                      </div>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

export default function ChatTab(props: ChatTabProps) {
  const runtime = useOptionalAegisChatRuntime();
  if (runtime) {
    return <ChatTabContent {...props} />;
  }
  return (
    <AegisChatProvider isChatVisible={true}>
      <ChatTabContent {...props} />
    </AegisChatProvider>
  );
}
