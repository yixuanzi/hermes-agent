/**
 * AISOC unified chat runtime (React context provider).
 *
 * Forked from aegis/frontend/src/lib/chatRuntime.tsx with one structural
 * change: the conversation list is sourced from the backend session store
 * (GET /api/sessions?source=aisoc_web) instead of localStorage, and history
 * for a persisted session is hydrated on demand from
 * GET /api/sessions/{id}/detail — but that endpoint only reconstructs plain
 * chat text (see session_service.py), never the main/delegate tool-call
 * structure a live session builds up (`Conversation.workflowTrace`, and the
 * inline `main-tools`/`delegate-tools` message bubbles). The backend has
 * nowhere durable to keep that structure (see workflowTraceCache for the
 * full rationale), so both `workflowTrace` and the full `messages` array are
 * snapshotted straight to localStorage as they grow and restored from there
 * on reload — the same strategy the old pre-fork widget (lib/useAgentChat.ts)
 * used for its whole message list. hydrateConversation() only falls through
 * to the network fetch when no cached messages exist for that conversation.
 * localStorage otherwise only keeps UI preferences (active conversation id)
 * under the `aisoc_chat_` prefix.
 *
 * WebSocket protocol: /api/chat/session — session.bind / message.send /
 * approval.respond / clarify.respond / session.interrupt / session.resume,
 * with the server event envelope handled in handleSocketEvent below.
 */
import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { fetchJSON } from '../../lib/api';
import { getStoredToken } from '../../lib/auth';
import {
  ChainStep,
  ChatAttachment,
  ChatAttachmentSummary,
  Conversation,
  WorkflowTraceEvent,
  DelegateToolCall,
  Message,
} from '../types';
import { getFrontendSettings } from '../lib/frontendSettings';
import { applyWorkflowSocketEvent } from '../lib/sessionWorkflow';
import { loadCachedWorkflowTrace, saveCachedWorkflowTrace } from '../lib/workflowTraceCache';
import { loadCachedMessages, saveCachedMessages } from '../lib/messageCache';

export type ChatSocketEvent = {
  type: string;
  server_event_id?: string;
  ts?: number;
  session_id?: string;
  title?: string;
  resumed?: boolean;
  turn_id?: string;
  source?: 'main' | 'delegate';
  srcagent?: string;
  message_id?: string;
  delta?: string;
  content?: string;
  completed?: boolean;
  client_msg_id?: string;
  tool_name?: string;
  tool_call_id?: string;
  args_preview?: string;
  result_preview?: string;
  modified_files?: string[];
  child_session_id?: string;
  reason?: string;
  state?: string;
  approval_id?: string;
  clarify_id?: string;
  command?: string;
  description?: string;
  question?: string;
  choices?: string[];
  code?: string;
  message?: string;
};

export interface RejectedChatInput {
  clientMsgId: string;
  text: string;
}

export interface PendingHtmlPreview {
  conversationId: string;
  messageId: string;
  path: string;
}

export type ChatMessageArgs = Record<string, unknown>;

type PendingBoundAction =
  | {
      type: 'message.send';
      text: string;
      clientMsgId: string;
      attachments: ChatAttachment[];
      args?: ChatMessageArgs;
    }
  | { type: 'approval.respond'; choice: 'once' | 'session' | 'always' | 'deny' }
  | { type: 'clarify.respond'; answer: string }
  | { type: 'session.resume' };

type SocketEntry = {
  socket: WebSocket;
  localConversationId: string;
  sessionId: string;
};

type SessionListItem = {
  id: string;
  title?: string | null;
  preview?: string | null;
  started_at?: number | null;
  last_active?: number | null;
  message_count?: number | null;
};

type SessionListResponse = {
  sessions: SessionListItem[];
  total: number;
};

type SessionDetailMessage = {
  role: string;
  content: string;
  tool_name?: string | null;
  tool_call_id?: string | null;
  timestamp?: number | null;
};

type SessionDetailResponse = {
  session_id: string;
  messages: SessionDetailMessage[];
};

type ChatRuntimeContextValue = {
  conversations: Conversation[];
  activeConvId: string;
  activeConversation?: Conversation;
  transportError: string;
  rejectedInput?: RejectedChatInput;
  pendingHtmlPreviews: Record<string, PendingHtmlPreview>;
  chatAttentionCount: number;
  sessionsLoading: boolean;
  setActiveConversation: (conversationId: string) => void;
  createConversation: () => void;
  deleteConversation: (conversationId: string) => void;
  refreshSessions: () => Promise<void>;
  openSession: (sessionId: string) => void;
  submitInput: (text: string, attachments?: ChatAttachment[], args?: ChatMessageArgs) => void;
  respondApproval: (choice: 'once' | 'session' | 'always' | 'deny') => void;
  respondClarify: (answer: string) => void;
  markClarifyAwaitingText: () => void;
  interruptActiveConversation: () => void;
  resumeActiveConversation: () => void;
  setTransportError: (message: string) => void;
  clearRejectedInput: () => void;
  consumePendingHtmlPreview: (conversationId: string, messageId: string) => void;
  clearPendingHtmlPreviews: () => void;
};

const ACTIVE_CONV_STORAGE_KEY = 'aisoc_chat_active_conversation';
const SESSION_SOURCE = 'aisoc_web';
const SESSION_LIST_LIMIT = 100;
const DEFAULT_TITLE = 'New Conversation';

const ChatRuntimeContext = createContext<ChatRuntimeContextValue | null>(null);

function createLocalId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `local-${crypto.randomUUID()}`;
  }
  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function createMessageId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatClock(now: Date = new Date()): string {
  return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** 会话列表用：带月日，方便区分跨天的会话顺序（消息气泡时间戳仍用 formatClock，不带日期）。 */
function formatClockWithDate(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${month}-${day} ${formatClock(now)}`;
}

function formatEpochClock(epochSeconds?: number | null): string {
  if (!epochSeconds) {
    return '';
  }
  return formatClockWithDate(new Date(epochSeconds * 1000));
}

function loadStoredActiveConvId(): string {
  try {
    return window.localStorage.getItem(ACTIVE_CONV_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function saveStoredActiveConvId(conversationId: string): void {
  try {
    window.localStorage.setItem(ACTIVE_CONV_STORAGE_KEY, conversationId);
  } catch {
    // Preference persistence is best effort only.
  }
}

function buildSocketUrl(token: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/chat/session?token=${encodeURIComponent(token)}`;
}

function getOpenReadyState(): number {
  return typeof WebSocket !== 'undefined' && typeof WebSocket.OPEN === 'number' ? WebSocket.OPEN : 1;
}

function getConnectingReadyState(): number {
  return typeof WebSocket !== 'undefined' && typeof WebSocket.CONNECTING === 'number' ? WebSocket.CONNECTING : 0;
}

function createConversationRecord(): Conversation {
  return {
    id: createLocalId(),
    title: DEFAULT_TITLE,
    messages: [],
    timestamp: 'Just now',
    lastUpdatedAt: new Date().toISOString(),
    lastKnownRunState: 'idle',
    foregroundSource: 'main',
    foregroundAgentName: '',
    liveChainTurnId: undefined,
    liveChainSteps: [],
    pendingApproval: null,
    pendingClarify: null,
    hasUnread: false,
    transportState: 'idle',
    hydrated: true,
    remote: false,
  };
}

function sessionItemTitle(item: SessionListItem): string {
  const title = String(item.title || '').trim();
  if (title) {
    return title;
  }
  const preview = String(item.preview || '').trim();
  if (preview) {
    return preview.length > 30 ? `${preview.slice(0, 30)}...` : preview;
  }
  return `Session ${String(item.id).slice(0, 8)}`;
}

/** Shared cache key for workflowTraceCache and messageCache: durable across a
 * refresh (sessionId), falling back to the client-generated local id only
 * before session.bound attaches a sessionId. Mirrors ChatPage.tsx's
 * drawerCacheKey — same reason: `id` stays the local id for this tab's
 * lifetime, but a reload rebuilds conversations from /api/sessions where
 * `id` is set to the sessionId instead. */
function durableConversationCacheKey(conversation: { id: string; sessionId?: string }): string {
  return conversation.sessionId || conversation.id;
}

const LOCAL_CACHE_USER_ID = 'aisoc-web';

function conversationFromSessionItem(item: SessionListItem): Conversation {
  return {
    id: String(item.id),
    sessionId: String(item.id),
    title: sessionItemTitle(item),
    messages: [],
    timestamp: formatEpochClock(item.last_active ?? item.started_at) || 'Earlier',
    lastUpdatedAt: new Date(((item.last_active ?? item.started_at) || 0) * 1000).toISOString(),
    lastKnownRunState: 'idle',
    foregroundSource: 'main',
    foregroundAgentName: '',
    liveChainSteps: [],
    pendingApproval: null,
    pendingClarify: null,
    hasUnread: false,
    transportState: 'idle',
    hydrated: false,
    remote: true,
  };
}

const FILE_MUTATING_TOOLS = new Set(['write_file', 'patch']);

/**
 * Mirrors agent/tool_dispatch_helpers.py's _extract_landed_file_mutation_paths
 * (the RESULT-based branch only — write_file/patch always report
 * `files_modified` on success, per tools/file_operations.py:212-213/1663, so
 * the args-derived fallback the Python version also has isn't needed here).
 */
function landedFileMutationPaths(toolName: string | null | undefined, resultText: string): string[] {
  if (!toolName || !FILE_MUTATING_TOOLS.has(toolName)) {
    return [];
  }
  let data: unknown;
  try {
    data = JSON.parse(resultText);
  } catch {
    return [];
  }
  if (!data || typeof data !== 'object') {
    return [];
  }
  const record = data as Record<string, unknown>;
  if (Array.isArray(record.files_modified) && record.files_modified.length > 0) {
    return record.files_modified.filter((p): p is string => typeof p === 'string' && p.length > 0);
  }
  if (typeof record.resolved_path === 'string' && record.resolved_path) {
    return [record.resolved_path];
  }
  return [];
}

/**
 * 历史消息里连续的 role="tool" 行还原成一个 main-tools 气泡（字段照抄
 * upsertMainToolMessage 的实时构造）。一次回合内可能有多轮工具调用——中间
 * 纯调用、无文本的 assistant 行后端已经过滤掉了，所以留给这里的就是一串
 * 连续的 tool 行，按"连续"分组即可，不需要 turn_id（历史记录里也没存）。
 * 同一组里 write_file/patch 成功落地的路径顺带还原成 modifiedFiles，挂在
 * 紧跟着的 assistant 回复上（AssistantBubble 靠这个字段渲染可点开的文件芯片）。
 */
function messagesFromSessionDetail(detail: SessionDetailResponse): Message[] {
  const messages: Message[] = [];
  let pendingSteps: (ChainStep & { id: string })[] = [];
  let pendingGroupIndex: number | null = null;
  let pendingModifiedFiles: string[] = [];

  function flushToolGroup(): string[] {
    const modifiedFiles = pendingModifiedFiles;
    pendingModifiedFiles = [];
    if (pendingGroupIndex === null || pendingSteps.length === 0) {
      pendingSteps = [];
      pendingGroupIndex = null;
      return modifiedFiles;
    }
    messages.push({
      id: `history-tools-${detail.session_id}-${pendingGroupIndex}`,
      sender: 'assistant',
      kind: 'main-tools',
      text: 'Main orchestration activity',
      timestamp: pendingSteps[pendingSteps.length - 1].timestamp,
      source: 'main',
      chainSteps: pendingSteps,
    });
    pendingSteps = [];
    pendingGroupIndex = null;
    return modifiedFiles;
  }

  detail.messages.forEach((item, index) => {
    if (item.role === 'tool') {
      if (pendingGroupIndex === null) {
        pendingGroupIndex = index;
      }
      const text = String(item.content || '').trim();
      pendingSteps.push({
        id: item.tool_call_id || `history-tools-${detail.session_id}-${index}`,
        agentName: item.tool_name || 'Tool',
        type: 'vip_tool',
        status: 'Completed',
        message: text || '(no output)',
        timestamp: formatEpochClock(item.timestamp) || '',
      });
      for (const path of landedFileMutationPaths(item.tool_name, text)) {
        if (!pendingModifiedFiles.includes(path)) {
          pendingModifiedFiles.push(path);
        }
      }
      return;
    }
    if (item.role !== 'user' && item.role !== 'assistant') {
      return;
    }
    const text = String(item.content || '').trim();
    if (!text) {
      return;
    }
    // Tool calls happen before the reply that follows them — flush first so
    // the tool group bubble lands above it, matching the live layout.
    const modifiedFiles = flushToolGroup();
    messages.push({
      id: `history-${detail.session_id}-${index}`,
      sender: item.role === 'user' ? 'user' : 'assistant',
      kind: 'chat',
      text,
      timestamp: formatEpochClock(item.timestamp) || '',
      source: 'main',
      modifiedFiles:
        item.role === 'assistant' && modifiedFiles.length > 0 ? modifiedFiles : undefined,
    });
  });
  // Trailing tool calls with no final reply yet (session ended mid-turn).
  flushToolGroup();
  return messages;
}

function findMessageIndex(
  messages: Message[],
  {
    messageId,
    turnId,
    source,
    srcagent,
  }: {
    messageId: string;
    turnId?: string;
    source?: 'main' | 'delegate';
    srcagent?: string;
  },
): number {
  const exactIndex = messages.findIndex((message) => message.id === messageId);
  if (exactIndex >= 0) {
    return exactIndex;
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const candidate = messages[index];
    if ((candidate.kind || 'chat') !== 'chat') {
      continue;
    }
    if (candidate.turnId !== turnId) {
      continue;
    }
    if (candidate.source !== source) {
      continue;
    }
    if ((candidate.srcagent || '') !== (srcagent || '')) {
      continue;
    }
    if (candidate.pending) {
      return index;
    }
  }
  return -1;
}

function upsertChainStep(steps: ChainStep[], nextStep: ChainStep & { id: string }): ChainStep[] {
  const index = steps.findIndex((step) => step.id === nextStep.id);
  if (index < 0) {
    return [...steps, nextStep];
  }
  const updated = [...steps];
  updated[index] = { ...updated[index], ...nextStep };
  return updated;
}

function upsertDelegateToolCall(
  calls: DelegateToolCall[],
  nextCall: DelegateToolCall,
): DelegateToolCall[] {
  const index = calls.findIndex((call) => call.id === nextCall.id);
  if (index < 0) {
    return [...calls, nextCall];
  }
  const updated = [...calls];
  updated[index] = { ...updated[index], ...nextCall };
  return updated;
}

function buildDelegateToolSummaryId(turnId?: string, srcagent?: string): string {
  return `delegate-tools:${turnId || 'unknown'}:${srcagent || 'delegate'}`;
}

function buildMainToolSummaryId(turnId?: string): string {
  return `main-tools:${turnId || 'unknown'}`;
}

function buildDelegateEventText(payload: ChatSocketEvent): string {
  const agentLabel = payload.srcagent || 'Delegate Agent';
  if (payload.type === 'delegate.entered') {
    return `${agentLabel} entered foreground`;
  }
  const reason = payload.reason ? ` · ${payload.reason}` : '';
  return `${agentLabel} returned control to main${reason}`;
}

function upsertDelegateToolMessage(
  conversation: Conversation,
  payload: ChatSocketEvent,
): Message[] {
  const messageId = buildDelegateToolSummaryId(payload.turn_id, payload.srcagent);
  const toolCallId = payload.tool_call_id || createMessageId('delegate-tool');
  const nextCall: DelegateToolCall = {
    id: toolCallId,
    toolName: payload.tool_name || 'delegate_tool',
    argsPreview: payload.args_preview || '',
    resultPreview: payload.result_preview,
    status: payload.type === 'tool.completed' ? 'completed' : 'running',
  };
  const existingIndex = conversation.messages.findIndex((message) => message.id === messageId);
  if (existingIndex >= 0) {
    const updatedMessages = [...conversation.messages];
    const existingMessage = updatedMessages[existingIndex];
    updatedMessages[existingIndex] = {
      ...existingMessage,
      timestamp: formatClock(),
      text: `${payload.srcagent || 'Delegate Agent'} tool activity`,
      delegateTools: upsertDelegateToolCall(existingMessage.delegateTools || [], nextCall),
    };
    return updatedMessages;
  }
  return [
    ...conversation.messages,
    {
      id: messageId,
      sender: 'assistant',
      kind: 'delegate-tools',
      text: `${payload.srcagent || 'Delegate Agent'} tool activity`,
      timestamp: formatClock(),
      source: 'delegate',
      srcagent: payload.srcagent,
      turnId: payload.turn_id,
      delegateTools: [nextCall],
    },
  ];
}

function upsertMainToolMessage(
  conversation: Conversation,
  payload: ChatSocketEvent,
): Message[] {
  const messageId = buildMainToolSummaryId(payload.turn_id);
  const nextStep: ChainStep & { id: string } = {
    id: payload.tool_call_id || `tool:${payload.tool_name || 'tool'}`,
    agentName: payload.tool_name || 'Tool',
    type: 'vip_tool',
    status: payload.type === 'tool.completed' ? 'Completed' : 'Processing',
    message:
      payload.type === 'tool.completed'
        ? payload.result_preview || `${payload.tool_name || 'Tool'} completed`
        : payload.args_preview || payload.tool_name || 'Tool started',
    timestamp: formatClock(),
  };
  const existingIndex = conversation.messages.findIndex((message) => message.id === messageId);
  if (existingIndex >= 0) {
    const updatedMessages = [...conversation.messages];
    const existingMessage = updatedMessages[existingIndex];
    updatedMessages[existingIndex] = {
      ...existingMessage,
      timestamp: formatClock(),
      chainSteps: upsertChainStep(existingMessage.chainSteps || [], nextStep),
    };
    return updatedMessages;
  }
  return [
    ...conversation.messages,
    {
      id: messageId,
      sender: 'assistant',
      kind: 'main-tools',
      text: 'Main orchestration activity',
      timestamp: formatClock(),
      source: 'main',
      turnId: payload.turn_id,
      chainSteps: [nextStep],
    },
  ];
}

function shouldFlagUnread(
  conversationId: string,
  activeConversationId: string,
  isChatVisible: boolean,
): boolean {
  return !isChatVisible || conversationId !== activeConversationId;
}

function isUnreadWorthyEvent(eventType: string): boolean {
  return (
    eventType === 'message.completed' ||
    eventType === 'delegate.entered' ||
    eventType === 'delegate.exited' ||
    eventType === 'tool.started' ||
    eventType === 'tool.completed' ||
    eventType === 'approval.request' ||
    eventType === 'clarify.request'
  );
}

function findLastHtmlModifiedFile(paths: string[] | undefined): string | undefined {
  if (!Array.isArray(paths)) {
    return undefined;
  }
  return [...paths]
    .reverse()
    .find((path) => typeof path === 'string' && /\.html?$/i.test(path.trim()));
}

export function AisocChatProvider({
  children,
  isChatVisible,
}: {
  children: React.ReactNode;
  isChatVisible: boolean;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string>(() => loadStoredActiveConvId());
  const [transportErrors, setTransportErrors] = useState<Record<string, string>>({});
  const [rejectedInput, setRejectedInput] = useState<RejectedChatInput>();
  const [pendingHtmlPreviews, setPendingHtmlPreviews] = useState<Record<string, PendingHtmlPreview>>({});
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const conversationsRef = useRef<Conversation[]>(conversations);
  const activeConvIdRef = useRef(activeConvId);
  const isChatVisibleRef = useRef(isChatVisible);
  const socketsRef = useRef<Record<string, SocketEntry>>({});
  const pendingBoundActionsRef = useRef<Record<string, PendingBoundAction[]>>({});
  const hydratingRef = useRef<Set<string>>(new Set());
  const restoredWorkflowConvIdsRef = useRef<Set<string>>(new Set());
  const skipNextWorkflowSaveRef = useRef(false);
  const restoredMessageConvIdsRef = useRef<Set<string>>(new Set());
  const skipNextMessageSaveRef = useRef(false);

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeConvId),
    [activeConvId, conversations],
  );
  const transportError = transportErrors[activeConvId] || '';

  const chatAttentionCount = useMemo(
    () =>
      conversations.filter(
        (conversation) =>
          conversation.hasUnread || conversation.pendingApproval || conversation.pendingClarify,
      ).length,
    [conversations],
  );

  function updateConversations(
    updater: (current: Conversation[]) => Conversation[],
  ): Conversation[] {
    let nextSnapshot: Conversation[] = conversationsRef.current;
    setConversations((current) => {
      const next = updater(current);
      conversationsRef.current = next;
      nextSnapshot = next;
      return next;
    });
    return nextSnapshot;
  }

  function markConversationRead(conversationId: string) {
    updateConversations((current) => {
      let changed = false;
      const next = current.map((conversation) => {
        if (conversation.id !== conversationId || !conversation.hasUnread) {
          return conversation;
        }
        changed = true;
        return {
          ...conversation,
          hasUnread: false,
        };
      });
      return changed ? next : current;
    });
  }

  function setConversationTransportState(
    conversationId: string,
    state: Conversation['transportState'],
  ) {
    updateConversations((current) => {
      let changed = false;
      const next = current.map((conversation) => {
        if (conversation.id !== conversationId || conversation.transportState === state) {
          return conversation;
        }
        changed = true;
        return {
          ...conversation,
          transportState: state,
        };
      });
      return changed ? next : current;
    });
  }

  function setConversationTransportError(conversationId: string, message: string) {
    if (!conversationId) {
      return;
    }
    setTransportErrors((current) => {
      if (!message) {
        if (!(conversationId in current)) {
          return current;
        }
        const { [conversationId]: _cleared, ...remaining } = current;
        return remaining;
      }
      if (current[conversationId] === message) {
        return current;
      }
      return { ...current, [conversationId]: message };
    });
  }

  function setTransportError(message: string) {
    setConversationTransportError(activeConvIdRef.current, message);
  }

  function closeSocketForConversation(conversationId: string) {
    const entry = socketsRef.current[conversationId];
    if (!entry) {
      return;
    }
    delete socketsRef.current[conversationId];
    entry.socket.close();
  }

  function closeAllSockets() {
    const entries = Object.values(socketsRef.current) as SocketEntry[];
    socketsRef.current = {};
    entries.forEach((entry) => {
      entry.socket.close();
    });
  }

  async function refreshSessions(): Promise<void> {
    setSessionsLoading(true);
    try {
      const response = await fetchJSON<SessionListResponse>(
        `/api/sessions?limit=${SESSION_LIST_LIMIT}&source=${encodeURIComponent(SESSION_SOURCE)}&mine=true`,
      );
      const items = Array.isArray(response.sessions) ? response.sessions : [];
      updateConversations((current) => {
        const known = new Map<string, Conversation>();
        current.forEach((conversation) => {
          if (conversation.sessionId) {
            known.set(conversation.sessionId, conversation);
          }
        });
        const merged: Conversation[] = [];
        const seen = new Set<string>();
        items.forEach((item) => {
          const sessionId = String(item.id);
          seen.add(sessionId);
          const existing = known.get(sessionId);
          if (existing) {
            const lastActive = item.last_active ?? item.started_at;
            merged.push({
              ...existing,
              title: existing.messages.length > 0 ? existing.title : sessionItemTitle(item),
              timestamp: formatEpochClock(lastActive) || existing.timestamp,
              lastUpdatedAt: lastActive ? new Date(lastActive * 1000).toISOString() : existing.lastUpdatedAt,
            });
            return;
          }
          merged.push(conversationFromSessionItem(item));
        });
        // Keep local conversations that haven't landed in the backend list
        // yet (brand-new sessions, or in-flight ones outside the window).
        current.forEach((conversation) => {
          if (!conversation.sessionId || !seen.has(conversation.sessionId)) {
            merged.unshift(conversation);
          }
        });
        return merged;
      });
    } catch {
      // Session list is best effort; live chat still works without it.
    } finally {
      setSessionsLoading(false);
    }
  }

  async function hydrateConversation(conversationId: string): Promise<void> {
    const conversation = conversationsRef.current.find((item) => item.id === conversationId);
    if (
      !conversation ||
      !conversation.sessionId ||
      conversation.hydrated ||
      hydratingRef.current.has(conversationId)
    ) {
      return;
    }
    // Snapshot how many messages this conversation had *before* the fetch —
    // anything already there at this point came from the local cache (never
    // backend-verified, see the restore effect above) or is otherwise
    // unconfirmed, so it gets replaced by the authoritative fetch below, not
    // blindly kept. Only messages that land *after* this point (live socket
    // events arriving while the fetch is in flight) are preserved. The cache
    // restore effect checks `hydratingRef` before injecting anything, so it
    // can't race this snapshot once `hydratingRef.current.add()` below runs.
    const messageCountAtFetchStart = conversation.messages.length;
    hydratingRef.current.add(conversationId);
    try {
      const detail = await fetchJSON<SessionDetailResponse>(
        `/api/sessions/${encodeURIComponent(conversation.sessionId)}/detail`,
      );
      const history = messagesFromSessionDetail(detail);
      updateConversations((current) =>
        current.map((item) => {
          if (item.id !== conversationId) {
            return item;
          }
          return {
            ...item,
            hydrated: true,
            messages: [...history, ...item.messages.slice(messageCountAtFetchStart)],
          };
        }),
      );
    } catch {
      setConversationTransportError(conversationId, 'Failed to load conversation history.');
    } finally {
      hydratingRef.current.delete(conversationId);
    }
  }

  function queueBoundAction(localConversationId: string, action: PendingBoundAction) {
    const queued = pendingBoundActionsRef.current[localConversationId] || [];
    pendingBoundActionsRef.current[localConversationId] = [...queued, action];
  }

  function sendBoundAction(localConversationId: string, sessionId: string, action: PendingBoundAction) {
    const entry = socketsRef.current[localConversationId];
    const socket = entry?.socket;
    if (!socket || socket.readyState !== getOpenReadyState()) {
      setConversationTransportError(localConversationId, 'Chat transport is not connected.');
      return;
    }
    if (action.type === 'message.send') {
      socket.send(
        JSON.stringify({
          type: 'message.send',
          session_id: sessionId,
          text: action.text,
          client_msg_id: action.clientMsgId,
          attachments: action.attachments,
          args: action.args,
        }),
      );
      return;
    }
    if (action.type === 'approval.respond') {
      socket.send(
        JSON.stringify({
          type: 'approval.respond',
          session_id: sessionId,
          choice: action.choice,
        }),
      );
      return;
    }
    if (action.type === 'clarify.respond') {
      socket.send(
        JSON.stringify({
          type: 'clarify.respond',
          session_id: sessionId,
          answer: action.answer,
        }),
      );
      return;
    }
    socket.send(
      JSON.stringify({
        type: 'session.resume',
        session_id: sessionId,
      }),
    );
  }

  function flushBoundActions(localConversationId: string, sessionId: string) {
    const queued = pendingBoundActionsRef.current[localConversationId] || [];
    delete pendingBoundActionsRef.current[localConversationId];
    queued.forEach((action) => sendBoundAction(localConversationId, sessionId, action));
  }

  function handleSocketEvent(localConversationId: string, payload: ChatSocketEvent) {
    const payloadSessionId = payload.session_id;
    if (payload.type === 'error') {
      const rejectedClientMsgId = payload.client_msg_id;
      if (rejectedClientMsgId) {
        const rejectedMessage = conversationsRef.current
          .find((conversation) =>
            conversation.id === localConversationId ||
            (!!payloadSessionId && conversation.sessionId === payloadSessionId),
          )
          ?.messages.find((message) => message.clientMsgId === rejectedClientMsgId);
        if (rejectedMessage) {
          updateConversations((current) =>
            current.map((conversation) => {
              const matched =
                conversation.id === localConversationId ||
                (!!payloadSessionId && conversation.sessionId === payloadSessionId);
              if (!matched) return conversation;
              return {
                ...conversation,
                messages: conversation.messages.filter(
                  (message) => message.clientMsgId !== rejectedClientMsgId,
                ),
              };
            }),
          );
          setRejectedInput({
            clientMsgId: rejectedClientMsgId,
            text: rejectedMessage.text,
          });
        }
      }
      setConversationTransportError(localConversationId, payload.message || 'Chat transport error.');
      return;
    }

    if (payload.type === 'message.accepted') {
      setConversationTransportError(localConversationId, '');
    }

    const completedSource = payload.source || 'main';
    const autoPreviewPath =
      payload.type === 'message.completed' &&
      completedSource === 'main' &&
      getFrontendSettings().chatAutoOpenHtmlOnTaskComplete
        ? findLastHtmlModifiedFile(payload.modified_files)
        : undefined;
    if (autoPreviewPath) {
      const messageId =
        payload.message_id ||
        `${completedSource}:${payload.turn_id || 'unknown'}:${payload.srcagent || 'main'}`;
      setPendingHtmlPreviews((current) => ({
        ...current,
        [localConversationId]: {
          conversationId: localConversationId,
          messageId,
          path: autoPreviewPath,
        },
      }));
    }

    updateConversations((current) =>
      current.map((conversation) => {
        const matched =
          conversation.id === localConversationId ||
          (!!payloadSessionId && conversation.sessionId === payloadSessionId);
        if (!matched) {
          return conversation;
        }

        let nextConversation: Conversation = {
          ...conversation,
          lastUpdatedAt: new Date().toISOString(),
          timestamp: formatClockWithDate(),
          hasUnread:
            conversation.hasUnread ||
            (isUnreadWorthyEvent(payload.type) &&
              shouldFlagUnread(
                conversation.id,
                activeConvIdRef.current,
                isChatVisibleRef.current,
              )),
        };
        nextConversation = applyWorkflowSocketEvent(nextConversation, payload);

        if (payload.type === 'session.bound') {
          nextConversation.sessionId = payload.session_id || nextConversation.sessionId;
          nextConversation.title =
            nextConversation.title !== DEFAULT_TITLE
              ? nextConversation.title
              : payload.title || nextConversation.title;
          nextConversation.transportState = 'connected';
          // A session bound for the first time from this tab has no server
          // history to fetch.
          if (!payload.resumed) {
            nextConversation.hydrated = true;
          }
          return nextConversation;
        }

        if (payload.type === 'message.accepted') {
          if ((payload.source || 'main') === 'main') {
            nextConversation.liveChainTurnId = payload.turn_id || nextConversation.liveChainTurnId;
            nextConversation.liveChainSteps = [];
          }
          return nextConversation;
        }

        if (payload.type === 'message.stream.completed') {
          const messageId = payload.message_id;
          if (!messageId) {
            return nextConversation;
          }
          const existingIndex = nextConversation.messages.findIndex((message) => message.id === messageId);
          if (existingIndex < 0) {
            return nextConversation;
          }
          const updatedMessages = [...nextConversation.messages];
          updatedMessages[existingIndex] = {
            ...updatedMessages[existingIndex],
            pending: false,
          };
          nextConversation.messages = updatedMessages;
          return nextConversation;
        }

        if (payload.type === 'message.delta' || payload.type === 'message.completed') {
          const source = payload.source || 'main';
          const messageId =
            payload.message_id || `${source}:${payload.turn_id || 'unknown'}:${payload.srcagent || 'main'}`;
          const existingIndex = findMessageIndex(nextConversation.messages, {
            messageId,
            turnId: payload.turn_id,
            source,
            srcagent: payload.srcagent,
          });
          const existingMessage = existingIndex >= 0 ? nextConversation.messages[existingIndex] : null;
          const nextText =
            payload.type === 'message.delta'
              ? `${existingMessage?.text || ''}${payload.delta || ''}`
              : payload.content || '';
          const nextMessage: Message = {
            id: messageId,
            sender: 'assistant',
            kind: 'chat',
            text: nextText,
            timestamp: formatClock(),
            source,
            srcagent: payload.srcagent,
            turnId: payload.turn_id,
            pending: payload.type === 'message.delta',
            modifiedFiles:
              payload.type === 'message.completed' && Array.isArray(payload.modified_files)
                ? payload.modified_files
                : existingMessage?.modifiedFiles,
          };

          if (existingIndex >= 0) {
            const updatedMessages = [...nextConversation.messages];
            updatedMessages[existingIndex] = {
              ...updatedMessages[existingIndex],
              ...nextMessage,
            };
            nextConversation.messages = updatedMessages;
          } else {
            nextConversation.messages = [...nextConversation.messages, nextMessage];
          }
          return nextConversation;
        }

        if (payload.type === 'run.state') {
          nextConversation.lastKnownRunState = payload.state || nextConversation.lastKnownRunState;
          nextConversation.foregroundSource = payload.source || nextConversation.foregroundSource;
          nextConversation.foregroundAgentName = payload.srcagent || '';
          return nextConversation;
        }

        if (payload.type === 'delegate.entered' || payload.type === 'delegate.exited') {
          nextConversation.messages = [
            ...nextConversation.messages,
            {
              id: createMessageId('delegate-event'),
              sender: 'assistant',
              kind: 'delegate-event',
              text: buildDelegateEventText(payload),
              timestamp: formatClock(),
              source: 'delegate',
              srcagent: payload.srcagent,
              turnId: payload.turn_id,
            },
          ];
          return nextConversation;
        }

        if (payload.type === 'tool.started' || payload.type === 'tool.completed') {
          if ((payload.source || 'main') === 'delegate') {
            nextConversation.messages = upsertDelegateToolMessage(nextConversation, payload);
            return nextConversation;
          }
          nextConversation.messages = upsertMainToolMessage(nextConversation, payload);
          return nextConversation;
        }

        if (payload.type === 'approval.request') {
          nextConversation.pendingApproval = {
            approvalId: payload.approval_id || '',
            command: payload.command || '',
            description: payload.description || '',
            choices: payload.choices || ['once', 'session', 'always', 'deny'],
          };
          nextConversation.lastKnownRunState = 'waiting_for_approval';
          return nextConversation;
        }

        if (payload.type === 'approval.resolved') {
          nextConversation.pendingApproval = null;
          return nextConversation;
        }

        if (payload.type === 'clarify.request') {
          const nextChoices = payload.choices || [];
          nextConversation.pendingClarify = {
            clarifyId: payload.clarify_id || '',
            question: payload.question || '',
            choices: nextChoices,
            awaitingText: nextChoices.length === 0,
          };
          nextConversation.lastKnownRunState = 'waiting_for_clarify';
          return nextConversation;
        }

        if (payload.type === 'clarify.resolved') {
          nextConversation.pendingClarify = null;
          return nextConversation;
        }

        return nextConversation;
      }),
    );
  }

  function connectSocketForConversation(
    targetConversation: Conversation,
    action?: PendingBoundAction,
  ): boolean {
    const token = getStoredToken();
    if (!token) {
      setConversationTransportError(targetConversation.id, 'Missing access token for chat transport.');
      return false;
    }

    if (action) {
      queueBoundAction(targetConversation.id, action);
    }

    const existingEntry = socketsRef.current[targetConversation.id];
    if (existingEntry) {
      if (existingEntry.socket.readyState === getOpenReadyState()) {
        const sessionId = targetConversation.sessionId || existingEntry.sessionId;
        if (sessionId) {
          flushBoundActions(targetConversation.id, sessionId);
        }
        return true;
      }
      if (existingEntry.socket.readyState === getConnectingReadyState()) {
        return true;
      }
      delete socketsRef.current[targetConversation.id];
    }

    const socket = new WebSocket(buildSocketUrl(token));
    const nextEntry: SocketEntry = {
      socket,
      localConversationId: targetConversation.id,
      sessionId: targetConversation.sessionId || '',
    };
    socketsRef.current[targetConversation.id] = nextEntry;
    setConversationTransportState(targetConversation.id, 'connecting');

    socket.onopen = () => {
      setConversationTransportError(targetConversation.id, '');
      setConversationTransportState(targetConversation.id, 'connected');
      socket.send(
        JSON.stringify({
          type: 'session.bind',
          session_id: targetConversation.sessionId,
          title: targetConversation.title === DEFAULT_TITLE ? undefined : targetConversation.title,
        }),
      );
    };

    socket.onmessage = (event) => {
      let payload: ChatSocketEvent;
      try {
        payload = JSON.parse(String(event.data));
      } catch {
        return;
      }
      handleSocketEvent(targetConversation.id, payload);
      if (payload.type === 'session.bound' && payload.session_id) {
        const currentEntry = socketsRef.current[targetConversation.id];
        if (currentEntry?.socket === socket) {
          currentEntry.sessionId = payload.session_id;
        }
        flushBoundActions(targetConversation.id, payload.session_id);
      }
    };

    socket.onerror = () => {
      setConversationTransportError(
        targetConversation.id,
        'Chat transport degraded. Reconnect or open a new conversation.',
      );
      setConversationTransportState(targetConversation.id, 'error');
    };

    socket.onclose = () => {
      const currentEntry = socketsRef.current[targetConversation.id];
      if (currentEntry?.socket === socket) {
        delete socketsRef.current[targetConversation.id];
      }
      setConversationTransportState(targetConversation.id, 'closed');
    };

    return true;
  }

  function ensureConversation(): Conversation {
    const existing =
      conversationsRef.current.find((conversation) => conversation.id === activeConvIdRef.current) ||
      conversationsRef.current[0];
    if (existing) {
      return existing;
    }
    const created = createConversationRecord();
    updateConversations((current) => [created, ...current]);
    setActiveConvId(created.id);
    activeConvIdRef.current = created.id;
    saveStoredActiveConvId(created.id);
    return created;
  }

  function createConversation() {
    const created = createConversationRecord();
    updateConversations((current) => [created, ...current]);
    setActiveConvId(created.id);
    activeConvIdRef.current = created.id;
    saveStoredActiveConvId(created.id);
  }

  function deleteConversation(conversationId: string) {
    closeSocketForConversation(conversationId);
    delete pendingBoundActionsRef.current[conversationId];
    setConversationTransportError(conversationId, '');
    setPendingHtmlPreviews((current) => {
      if (!current[conversationId]) return current;
      const { [conversationId]: _removed, ...remaining } = current;
      return remaining;
    });
    const target = conversationsRef.current.find((conversation) => conversation.id === conversationId);
    const updated = conversationsRef.current.filter((conversation) => conversation.id !== conversationId);
    setConversations(updated);
    conversationsRef.current = updated;
    if (activeConvIdRef.current === conversationId) {
      const nextActiveId = updated[0]?.id || '';
      setActiveConvId(nextActiveId);
      activeConvIdRef.current = nextActiveId;
    }
    if (target?.sessionId && target.remote) {
      fetchJSON(`/api/sessions/${encodeURIComponent(target.sessionId)}`, { method: 'DELETE' }).catch(
        () => {
          // Deletion failures resurface on the next session list refresh.
        },
      );
    }
  }

  function setActiveConversation(conversationId: string) {
    setActiveConvId(conversationId);
    activeConvIdRef.current = conversationId;
    saveStoredActiveConvId(conversationId);
    markConversationRead(conversationId);
    void hydrateConversation(conversationId);
  }

  function openSession(sessionId: string) {
    const normalized = String(sessionId || '').trim();
    if (!normalized) {
      return;
    }
    const existing = conversationsRef.current.find(
      (conversation) => conversation.sessionId === normalized || conversation.id === normalized,
    );
    if (existing) {
      setActiveConversation(existing.id);
      return;
    }
    const created: Conversation = {
      ...createConversationRecord(),
      id: normalized,
      sessionId: normalized,
      title: `Session ${normalized.slice(0, 8)}`,
      hydrated: false,
      remote: true,
    };
    updateConversations((current) => [created, ...current]);
    setActiveConversation(created.id);
  }

  function consumePendingHtmlPreview(conversationId: string, messageId: string) {
    setPendingHtmlPreviews((current) => {
      const pending = current[conversationId];
      if (!pending || pending.messageId !== messageId) {
        return current;
      }
      const { [conversationId]: _removed, ...remaining } = current;
      return remaining;
    });
  }

  function clearPendingHtmlPreviews() {
    setPendingHtmlPreviews({});
  }

  function submitInput(
    text: string,
    attachments: ChatAttachment[] = [],
    args?: ChatMessageArgs,
  ) {
    const trimmedText = text.trim();
    if (!trimmedText && attachments.length === 0) {
      return;
    }
    const sentText = text.endsWith('\n') ? `${text.trimEnd()}\n` : trimmedText;
    const conversation =
      conversationsRef.current.find((item) => item.id === activeConvIdRef.current) ||
      ensureConversation();
    const isClarifyReply = Boolean(
      conversation.pendingClarify?.awaitingText && conversation.sessionId,
    );
    const title =
      !isClarifyReply && conversation.messages.length === 0
        ? trimmedText.length > 24
          ? `${trimmedText.slice(0, 24)}...`
          : trimmedText || (attachments.length ? 'Attachment analysis' : conversation.title)
        : conversation.title;
    const clientMsgId = createMessageId('client');
    const userMessage: Message = {
      id: createMessageId('user'),
      sender: 'user',
      kind: 'chat',
      text: sentText,
      timestamp: formatClock(),
      clientMsgId,
      attachments: attachments.map(({ cache_path: _cachePath, ...summary }): ChatAttachmentSummary => summary),
    };
    const targetConversation = {
      ...conversation,
      title,
    };
    updateConversations((current) =>
      current.map((item) =>
        item.id === conversation.id
          ? {
              ...item,
              title,
              timestamp: formatClockWithDate(),
              lastUpdatedAt: new Date().toISOString(),
              liveChainTurnId: undefined,
              liveChainSteps: [],
              messages: [...item.messages, userMessage],
              hasUnread: false,
            }
          : item,
      ),
    );

    connectSocketForConversation(targetConversation, {
      ...(isClarifyReply
        ? {
            type: 'clarify.respond' as const,
            answer: trimmedText,
          }
        : {
            type: 'message.send' as const,
            text: sentText,
            clientMsgId,
            attachments,
            args,
          }),
    });
  }

  function respondApproval(choice: 'once' | 'session' | 'always' | 'deny') {
    const conversation = conversationsRef.current.find(
      (item) => item.id === activeConvIdRef.current,
    );
    if (!conversation?.sessionId) {
      return;
    }
    connectSocketForConversation(conversation, {
      type: 'approval.respond',
      choice,
    });
  }

  function clearRejectedInput() {
    setRejectedInput(undefined);
  }

  function respondClarify(answer: string) {
    const conversation = conversationsRef.current.find(
      (item) => item.id === activeConvIdRef.current,
    );
    if (!conversation?.sessionId) {
      return;
    }
    connectSocketForConversation(conversation, {
      type: 'clarify.respond',
      answer,
    });
  }

  function markClarifyAwaitingText() {
    const conversation = conversationsRef.current.find(
      (item) => item.id === activeConvIdRef.current,
    );
    if (!conversation) {
      return;
    }
    updateConversations((current) =>
      current.map((item) =>
        item.id === conversation.id
          ? {
              ...item,
              pendingClarify: item.pendingClarify
                ? {
                    ...item.pendingClarify,
                    awaitingText: true,
                  }
                : item.pendingClarify,
            }
          : item,
      ),
    );
  }

  function interruptActiveConversation() {
    const conversation = conversationsRef.current.find(
      (item) => item.id === activeConvIdRef.current,
    );
    if (!conversation) {
      return;
    }
    const entry = socketsRef.current[conversation.id];
    const socket = entry?.socket;
    if (!socket || socket.readyState !== getOpenReadyState() || !entry.sessionId) {
      return;
    }
    socket.send(
      JSON.stringify({
        type: 'session.interrupt',
        session_id: entry.sessionId,
      }),
    );
  }

  function resumeActiveConversation() {
    const conversation = conversationsRef.current.find(
      (item) => item.id === activeConvIdRef.current,
    );
    if (!conversation?.sessionId) {
      return;
    }
    connectSocketForConversation(conversation, {
      type: 'session.resume',
    });
  }

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  useEffect(() => {
    activeConvIdRef.current = activeConvId;
  }, [activeConvId]);

  useEffect(() => {
    isChatVisibleRef.current = isChatVisible;
    if (isChatVisible && activeConvIdRef.current) {
      markConversationRead(activeConvIdRef.current);
    }
  }, [isChatVisible]);

  useEffect(() => {
    if (!activeConvId && conversations.length > 0) {
      setActiveConvId(conversations[0].id);
      activeConvIdRef.current = conversations[0].id;
      return;
    }
    if (isChatVisible && activeConvId) {
      markConversationRead(activeConvId);
    }
  }, [activeConvId, conversations, isChatVisible]);

  // 恢复每个会话缓存的完整 messages（含内嵌的 main-tools/delegate-tools 气泡，
  // 见文件顶部注释）。只在该会话自己还没 hydrated、messages 还是空的时候合并，
  // 避免覆盖已经在跑的实时数据。
  //
  // 之所以是一个独立、对所有会话生效的 effect，而不是塞进 hydrateConversation
  // 内部只在拿到 activeConvId 之后才检查缓存：下面的持久化 effect 只依赖
  // [conversations]，一旦 conversations 从 /api/sessions 填充（此时每个会话的
  // messages 都还是初始的 []），它会在*同一次* commit 里立刻跑一遍——如果这时
  // 缓存还没被恢复（因为 hydrateConversation 要等 activeConvId 就位，那是下一
  // 次 commit 才会发生的事），持久化 effect 会把这次读到的"messages 全是空"
  // 原样写回 localStorage，抹掉刚保存的缓存，之后 hydrateConversation 再也
  // 读不到东西，只能转去后端拉纯文字、把工具调用气泡永久丢掉。
  useEffect(() => {
    const ids = conversations.map((conversation) => durableConversationCacheKey(conversation));
    const newIds = ids.filter((id) => !restoredMessageConvIdsRef.current.has(id));
    if (newIds.length === 0) {
      return;
    }
    newIds.forEach((id) => restoredMessageConvIdsRef.current.add(id));
    const restored = loadCachedMessages(LOCAL_CACHE_USER_ID, newIds);
    if (Object.keys(restored).length === 0) {
      return;
    }
    skipNextMessageSaveRef.current = true;
    updateConversations((current) =>
      current.map((conversation) => {
        if (
          conversation.hydrated ||
          conversation.messages.length > 0 ||
          // A hydrateConversation() call already in flight for this
          // conversation snapshotted `messages.length` at call time and will
          // treat anything appended after that snapshot as live activity to
          // preserve. Injecting the cache here, mid-flight, would get
          // mistaken for exactly that and appended after the fresh backend
          // history instead of being discarded — this is the confirmed
          // mechanism behind a real content-bleed repro (traced live via
          // temporary console logging: a stale/duplicated cache entry landed
          // in a conversation's `messages` between hydrateConversation()'s
          // snapshot and its fetch resolving, and got kept instead of
          // discarded, compounding on every repeat).
          hydratingRef.current.has(conversation.id)
        ) {
          return conversation;
        }
        const cached = restored[durableConversationCacheKey(conversation)];
        if (!cached || cached.length === 0) {
          return conversation;
        }
        // Deliberately NOT marking `hydrated: true` here — this is a local
        // cache, never verified against the backend. hydrateConversation()
        // still needs to run for this conversation once it becomes active,
        // both to pick up messages sent since the cache was written and to
        // self-correct if the cache ever ends up holding the wrong
        // conversation's content (see hydrateConversation's reconciliation).
        return { ...conversation, messages: cached };
      }),
    );
  }, [conversations]);

  // setActiveConversation() 是唯一调用 hydrateConversation 的地方，但页面刷新后
  // activeConvId 是从 localStorage 直接恢复（或由上面的 effect 自动选中第一个
  // 会话），从未经过 setActiveConversation，因此历史消息永远不会被拉取，直到
  // 用户手动点击会话列表。这里补一个 effect，让 activeConvId 一旦就位就尝试
  // hydrate（hydrateConversation 内部已对已 hydrated / 已有消息 / 正在拉取的
  // 情况做了幂等判断，重复调用是安全的；上面的缓存恢复一旦命中就会把 hydrated
  // 设成 true，这里自然会跳过网络请求，不会把工具调用气泡替换成纯文字历史）。
  useEffect(() => {
    if (activeConvId) {
      void hydrateConversation(activeConvId);
    }
  }, [activeConvId, conversations]);

  // 恢复每个会话缓存的 workflowTrace（见文件顶部注释）。只在该会话自己的
  // workflowTrace 还是空的时候合并，避免覆盖已经在跑的实时数据。
  useEffect(() => {
    const ids = conversations.map((conversation) => durableConversationCacheKey(conversation));
    const newIds = ids.filter((id) => !restoredWorkflowConvIdsRef.current.has(id));
    if (newIds.length === 0) {
      return;
    }
    newIds.forEach((id) => restoredWorkflowConvIdsRef.current.add(id));
    const restored = loadCachedWorkflowTrace(LOCAL_CACHE_USER_ID, newIds);
    if (Object.keys(restored).length === 0) {
      return;
    }
    // 同一坑同一解法：下面的持久化 effect 这一次 commit 里还会读到合并前的
    // conversations，不跳过就会把刚读出来的缓存原样写回去，抹掉 localStorage。
    skipNextWorkflowSaveRef.current = true;
    updateConversations((current) =>
      current.map((conversation) => {
        if (conversation.workflowTrace && conversation.workflowTrace.length > 0) {
          return conversation;
        }
        const cached = restored[durableConversationCacheKey(conversation)];
        if (!cached || cached.length === 0) {
          return conversation;
        }
        return { ...conversation, workflowTrace: cached, workflowTraceVersion: 1 };
      }),
    );
  }, [conversations]);

  // 持久化 workflowTrace（按当前会话集合裁剪）。会话列表还没加载完成时
  // （刚 mount，conversations 为空）不写入，否则会把 localStorage 里已有的
  // 缓存当成"用户没有任何 trace"直接删掉，抢在恢复 effect 读取之前就清空数据
  // ——跟 ChatPage.tsx 里 drawer tab 持久化踩过的坑一模一样。
  useEffect(() => {
    if (conversations.length === 0) {
      return;
    }
    if (skipNextWorkflowSaveRef.current) {
      skipNextWorkflowSaveRef.current = false;
      return;
    }
    const traces: Record<string, WorkflowTraceEvent[]> = {};
    conversations.forEach((conversation) => {
      if (conversation.workflowTrace && conversation.workflowTrace.length > 0) {
        traces[durableConversationCacheKey(conversation)] = conversation.workflowTrace;
      }
    });
    saveCachedWorkflowTrace(
      LOCAL_CACHE_USER_ID,
      traces,
      conversations.map((conversation) => durableConversationCacheKey(conversation)),
    );
  }, [conversations]);

  // 持久化完整 messages（含内嵌的 main-tools/delegate-tools 气泡），同样的
  // mount-空数组 / restore-同一 commit 两个坑，同样的两个 guard。恢复这一侧
  // 不需要单独的 effect——hydrateConversation 本身就是"恢复入口"，命中缓存时
  // 直接把 messages 灌回去（见上面），这里只负责把它们持续写回 localStorage。
  useEffect(() => {
    if (conversations.length === 0) {
      return;
    }
    if (skipNextMessageSaveRef.current) {
      skipNextMessageSaveRef.current = false;
      return;
    }
    const messagesByKey: Record<string, Message[]> = {};
    conversations.forEach((conversation) => {
      if (conversation.messages.length > 0) {
        messagesByKey[durableConversationCacheKey(conversation)] = conversation.messages;
      }
    });
    saveCachedMessages(
      LOCAL_CACHE_USER_ID,
      messagesByKey,
      conversations.map((conversation) => durableConversationCacheKey(conversation)),
    );
  }, [conversations]);

  useEffect(() => {
    void refreshSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => {
    closeAllSockets();
  }, []);

  const value = useMemo<ChatRuntimeContextValue>(
    () => ({
      conversations,
      activeConvId,
      activeConversation,
      transportError,
      rejectedInput,
      pendingHtmlPreviews,
      chatAttentionCount,
      sessionsLoading,
      setActiveConversation,
      createConversation,
      deleteConversation,
      refreshSessions,
      openSession,
      submitInput,
      respondApproval,
      respondClarify,
      markClarifyAwaitingText,
      interruptActiveConversation,
      resumeActiveConversation,
      setTransportError,
      clearRejectedInput,
      consumePendingHtmlPreview,
      clearPendingHtmlPreviews,
    }),
    [
      activeConvId,
      activeConversation,
      chatAttentionCount,
      conversations,
      transportError,
      rejectedInput,
      pendingHtmlPreviews,
      sessionsLoading,
    ],
  );

  return (
    <ChatRuntimeContext.Provider value={value}>
      {children}
    </ChatRuntimeContext.Provider>
  );
}

export function useChatRuntime(): ChatRuntimeContextValue {
  const value = useContext(ChatRuntimeContext);
  if (!value) {
    throw new Error('AISOC chat runtime is not available.');
  }
  return value;
}

export function useOptionalChatRuntime(): ChatRuntimeContextValue | null {
  return useContext(ChatRuntimeContext);
}
