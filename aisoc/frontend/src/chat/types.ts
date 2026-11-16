/**
 * Chat-domain types for the unified AISOC conversation module.
 *
 * Forked from the chat subset of aegis/frontend/src/types.ts so the ported
 * runtime (chatRuntime, sessionWorkflow) keeps its structural contract with
 * the backend WebSocket protocol.
 */

export interface ChainStep {
  id?: string;
  agentName: string;
  type: 'agent' | 'vip_tool';
  status: 'Completed' | 'Processing' | 'Pending' | 'Failed';
  message: string;
  timestamp: string;
}

export interface DelegateToolCall {
  id: string;
  toolName: string;
  argsPreview: string;
  resultPreview?: string;
  status: 'running' | 'completed';
}

export type WorkflowTraceEventType =
  | 'message.accepted'
  | 'message.completed'
  | 'message.stream.completed'
  | 'tool.started'
  | 'tool.completed'
  | 'run.state'
  | 'delegate.entered'
  | 'delegate.exited';

export interface WorkflowTraceEvent {
  id: string;
  type: WorkflowTraceEventType;
  timestamp: number;
  turnId?: string;
  parentTurnId?: string;
  source: 'main' | 'delegate';
  srcagent?: string;
  clientMsgId?: string;
  messageId?: string;
  content?: string;
  toolName?: string;
  toolCallId?: string;
  argsPreview?: string;
  resultPreview?: string;
  childSessionId?: string;
  delegateId?: string;
  state?: string;
  reason?: string;
}

export type WorkflowGraphNodeKind =
  | 'root'
  | 'input'
  | 'delegate'
  | 'tool'
  | 'tool-group'
  | 'end';
export type WorkflowGraphStatus = 'empty' | 'partial' | 'live' | 'complete';

export interface WorkflowGraphNode {
  id: string;
  kind: WorkflowGraphNodeKind;
  label: string;
  detail: string;
  status: string;
  source: 'main' | 'delegate';
  turnId?: string;
  parentId?: string;
  agent?: string;
  timestamp?: number;
  argsPreview?: string;
  resultPreview?: string;
  finalMessage?: string;
  toolRunId?: string;
  hiddenToolCount?: number;
  x: number;
  y: number;
  depth: number;
}

export interface WorkflowGraphEdge {
  id: string;
  from: string;
  to: string;
  source: 'main' | 'delegate';
  label?: string;
}

export interface WorkflowGraph {
  rootId: string;
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
  status: WorkflowGraphStatus;
  width: number;
  height: number;
}

export interface Message {
  id: string;
  sender: 'user' | 'assistant' | string;
  text: string;
  timestamp: string;
  kind?: 'chat' | 'delegate-event' | 'delegate-tools' | 'main-tools';
  chainSteps?: ChainStep[];
  delegateTools?: DelegateToolCall[];
  source?: 'main' | 'delegate';
  srcagent?: string;
  turnId?: string;
  pending?: boolean;
  clientMsgId?: string;
  workflowParentId?: string;
  attachments?: ChatAttachmentSummary[];
  modifiedFiles?: string[];
}

export interface ChatAttachmentSummary {
  id: string;
  kind: 'image' | 'document';
  media_type: string;
  display_name: string;
  size: number;
}

export interface ChatAttachment extends ChatAttachmentSummary {
  cache_path: string;
}

export interface Conversation {
  id: string;
  sessionId?: string;
  title: string;
  messages: Message[];
  timestamp: string;
  lastUpdatedAt?: string;
  lastKnownRunState?: string;
  foregroundSource?: 'main' | 'delegate';
  foregroundAgentName?: string;
  liveChainTurnId?: string;
  liveChainSteps?: ChainStep[];
  pendingApproval?: {
    approvalId: string;
    command: string;
    description: string;
    choices: string[];
  } | null;
  pendingClarify?: {
    clarifyId: string;
    question: string;
    choices: string[];
    awaitingText: boolean;
  } | null;
  hasUnread?: boolean;
  transportState?: 'idle' | 'connecting' | 'connected' | 'error' | 'closed';
  /** Backend session list metadata: message history not yet fetched. */
  hydrated?: boolean;
  /** Session originates from the backend list (persisted), not this tab. */
  remote?: boolean;
  workflowTraceVersion?: 1;
  workflowTrace?: WorkflowTraceEvent[];
}

export interface QuickCommand {
  type: 'agent' | 'prompt' | 'instruct';
  name: string;
  desc: string;
  content: string;
}
