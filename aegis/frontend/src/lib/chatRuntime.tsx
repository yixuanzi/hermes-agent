import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { ChainStep, ChatAttachment, ChatAttachmentSummary, Conversation, DelegateToolCall, Message } from '../types';
import { getStoredToken } from './auth';
import { getFrontendSettings } from './frontendSettings';
import { applyWorkflowSocketEvent } from './sessionWorkflow';

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
  awaiting_text?: boolean;
  multi_select?: boolean;
  remote_interaction_id?: string | null;
  allow_session?: boolean;
  allow_permanent?: boolean;
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
  | { type: 'approval.respond'; choice: 'once' | 'session' | 'always' | 'deny'; approvalId?: string }
  | { type: 'clarify.respond'; answer: string; clarifyId?: string }
  | { type: 'session.resume' };

type SocketEntry = {
  socket: WebSocket;
  localConversationId: string;
  sessionId: string;
};

type ChatRuntimeContextValue = {
  conversations: Conversation[];
  activeConvId: string;
  activeConversation?: Conversation;
  transportError: string;
  rejectedInput?: RejectedChatInput;
  pendingHtmlPreviews: Record<string, PendingHtmlPreview>;
  chatAttentionCount: number;
  setActiveConversation: (conversationId: string) => void;
  createConversation: () => void;
  clearHistory: () => boolean;
  deleteConversation: (conversationId: string) => void;
  submitInput: (text: string, attachments?: ChatAttachment[], args?: ChatMessageArgs) => void;
  respondApproval: (choice: 'once' | 'session' | 'always' | 'deny') => void;
  respondClarify: (answer: string) => void;
  markClarifyAwaitingText: () => void;
  resumeActiveConversation: () => void;
  setTransportError: (message: string) => void;
  clearRejectedInput: () => void;
  consumePendingHtmlPreview: (conversationId: string, messageId: string) => void;
  clearPendingHtmlPreviews: () => void;
};

const STORAGE_KEY = 'aegis_convs';

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

function loadCachedConversations(): Conversation[] {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return [];
  }
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveCachedConversations(conversations: Conversation[]): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
}

function buildSocketUrl(token: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/chat/ws?token=${encodeURIComponent(token)}`;
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
    title: 'New Investigation',
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
  };
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
      sender: 'aegis',
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
      sender: 'aegis',
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

export function AegisChatProvider({
  children,
  isChatVisible,
}: {
  children: React.ReactNode;
  isChatVisible: boolean;
}) {
  const [conversations, setConversations] = useState<Conversation[]>(() => loadCachedConversations());
  const [activeConvId, setActiveConvId] = useState<string>(() => loadCachedConversations()[0]?.id || '');
  const [transportErrors, setTransportErrors] = useState<Record<string, string>>({});
  const [rejectedInput, setRejectedInput] = useState<RejectedChatInput>();
  const [pendingHtmlPreviews, setPendingHtmlPreviews] = useState<Record<string, PendingHtmlPreview>>({});
  const conversationsRef = useRef<Conversation[]>(conversations);
  const activeConvIdRef = useRef(activeConvId);
  const isChatVisibleRef = useRef(isChatVisible);
  const socketsRef = useRef<Record<string, SocketEntry>>({});
  const pendingBoundActionsRef = useRef<Record<string, PendingBoundAction[]>>({});

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
          approval_id: action.approvalId,
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
          clarify_id: action.clarifyId,
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
          timestamp: formatClock(),
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
          nextConversation.title = payload.title || nextConversation.title;
          nextConversation.transportState = 'connected';
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
            sender: 'aegis',
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
              sender: 'aegis',
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
            source: payload.source === 'delegate' ? 'delegate' : 'main',
            remoteInteractionId: payload.remote_interaction_id || null,
            allowSession: payload.allow_session !== false,
            allowPermanent: payload.allow_permanent !== false,
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
            awaitingText: Boolean(payload.awaiting_text) || nextChoices.length === 0,
            multiSelect: Boolean(payload.multi_select),
            source: payload.source === 'delegate' ? 'delegate' : 'main',
            remoteInteractionId: payload.remote_interaction_id || null,
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
          title: targetConversation.title,
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
    return created;
  }

  function createConversation() {
    const created = createConversationRecord();
    updateConversations((current) => [created, ...current]);
    setActiveConvId(created.id);
    activeConvIdRef.current = created.id;
  }

  function clearHistory(): boolean {
    if (!window.confirm('确定要清除所有对话和本地缓存吗？')) {
      return false;
    }
    window.localStorage.removeItem(STORAGE_KEY);
    pendingBoundActionsRef.current = {};
    closeAllSockets();
    setConversations([]);
    conversationsRef.current = [];
    setActiveConvId('');
    activeConvIdRef.current = '';
    setTransportErrors({});
    setPendingHtmlPreviews({});
    return true;
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
    const updated = conversationsRef.current.filter((conversation) => conversation.id !== conversationId);
    setConversations(updated);
    conversationsRef.current = updated;
    if (activeConvIdRef.current === conversationId) {
      const nextActiveId = updated[0]?.id || '';
      setActiveConvId(nextActiveId);
      activeConvIdRef.current = nextActiveId;
    }
  }

  function setActiveConversation(conversationId: string) {
    setActiveConvId(conversationId);
    activeConvIdRef.current = conversationId;
    markConversationRead(conversationId);
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
              timestamp: formatClock(),
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
            clarifyId: targetConversation.pendingClarify?.clarifyId,
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
      approvalId: conversation.pendingApproval?.approvalId,
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
      clarifyId: conversation.pendingClarify?.clarifyId,
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
    saveCachedConversations(conversations);
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
    ],
  );

  return (
    <ChatRuntimeContext.Provider value={value}>
      {children}
    </ChatRuntimeContext.Provider>
  );
}

export function useAegisChatRuntime(): ChatRuntimeContextValue {
  const value = useContext(ChatRuntimeContext);
  if (!value) {
    throw new Error('Aegis chat runtime is not available.');
  }
  return value;
}

export function useOptionalAegisChatRuntime(): ChatRuntimeContextValue | null {
  return useContext(ChatRuntimeContext);
}
