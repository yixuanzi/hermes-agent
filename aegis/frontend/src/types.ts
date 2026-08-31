export type AgentStatus = 'Active' | 'Idle' | 'Offline';
export type RoutingRuleStatus = 'Enabled' | 'Disabled';
export type UserStatus = 'enabled' | 'disabled';
export type AgentPolicyStatus = 'allow' | 'deny';
export type DelegateAuditStatus = 'succ' | 'fail' | 'auth_denied';

export interface AuthenticatedUser {
  uid: string;
  username: string;
  email: string;
  status: UserStatus;
  create_time: string;
  last_login?: string | null;
  is_admin: boolean;
}

export interface UserDraft {
  username: string;
  password: string;
  email: string;
  status: UserStatus;
}

export interface PromptTemplate {
  id: string;
  tag: string;
  desc: string;
  prompt: string;
  create_time: string;
  update_time: string;
}

export interface PromptTemplateDraft {
  tag: string;
  desc: string;
  prompt: string;
}

export interface UserManualSummary {
  id: string;
  title: string;
}

export interface UserManual extends UserManualSummary {
  content: string;
}

export type AppEntrySubscriptionStatus = 'active' | 'expiring';
export type AppEntrySource = 'app' | 'subscription';

export interface AppEntry {
  subscription_id: string;
  subscription_no: string;
  subscription_status: AppEntrySubscriptionStatus;
  effective_to: string;
  product_service_code: string;
  product_service_name: string;
  entry_url: string | null;
  entry_source: AppEntrySource | null;
}

export interface AppEntryList {
  organization_code: string;
  entries: AppEntry[];
}

export interface A2AContextAgent {
  name: string;
  url: string | null;
  status: string | null;
  available: boolean;
  description: string | null;
  capabilities: string[];
  error: string | null;
}

export interface A2AContext {
  agents: A2AContextAgent[];
  global_routing: Array<{ id: string; name: string; policy: string; status: string }>;
  refreshed_at: string | null;
  stale: boolean;
  refresh_error: string | null;
}

export interface Agent {
  id: string;
  name: string;
  type: 'agent' | 'vip_tool';
  description: string;
  status: AgentStatus;
  tasksCount: number;
  lastUpdated: string;
  skillDescription?: string;
  a2aAddr?: string;
  authHeaderKey?: string;
  authHeaderValue?: string;
  extCapabilities?: string[];
}

export interface RoutingRule {
  id: string;
  priority: number;
  ruleName: string;
  agentId: string;
  conditions: string;
  actions: string;
  status: RoutingRuleStatus;
  updateTime: string;
}

export interface AgentDraft {
  agentId: string;
  url: string;
  description: string;
  status: AgentStatus;
  authHeaderKey: string;
  authHeaderValue: string;
  extCapabilities: string[];
}

export interface RoutingRuleDraft {
  name: string;
  policy: string;
  status: RoutingRuleStatus;
}

export interface AgentPolicy {
  rank_id: number;
  platform: string;
  user_id: string;
  agent_name: string;
  status: AgentPolicyStatus;
}

export interface DelegateAuditLog {
  id: string;
  timestamp: string;
  platform: string;
  user_id: string;
  user_name: string;
  agent_name: string;
  goal: string;
  session_id: string;
  is_loop: boolean;
  is_delegate_output: boolean;
  status: DelegateAuditStatus;
}

export interface DelegateAuditPage {
  logs: DelegateAuditLog[];
  total: number;
  page: number;
  page_size: number;
}

export interface OverviewStatusCounts {
  succ: number;
  fail: number;
  auth_denied: number;
}

export interface OverviewStatsComparison {
  previous_delegation_total: number;
  delegation_volume_change_percent: number | null;
  previous_success_rate: number | null;
  success_rate_change_percentage_points: number | null;
}

export interface OverviewStats {
  window_start: string;
  window_end: string;
  executing_agent_count: number;
  source_platform_count: number;
  active_user_count: number;
  delegation_total: number;
  success_count: number;
  success_rate: number | null;
  status_counts: OverviewStatusCounts;
  comparison: OverviewStatsComparison;
}

export type TopologyRuntimeStatus = 'active' | 'idle' | 'offline' | 'planned';
export type TopologyStarKind = 'tool' | 'api' | 'data';

export interface TopologyCenter {
  id: string;
  layer: 'center';
  name: string;
  symbol: string;
  role: string;
  description: string;
  capabilities: string[];
  layout: { x: number; y: number; radius: string };
}

export interface TopologyStarNode {
  id: string;
  layer: 'star_field';
  kind: TopologyStarKind;
  name: string;
  integration: string;
  purpose: string;
  required: boolean;
}

export interface TopologyAgentNode {
  id: string;
  layer: 'agent_ring';
  business_domain: string;
  product_service_code: string;
  name: string;
  display_name: string;
  marketing_name: string;
  symbol: string;
  cultural_origin: string;
  business_fit: string;
  role: string;
  layout: { ring_position: number; angle_degrees: number; radius: string };
  star_nodes: TopologyStarNode[];
  runtime: { status: TopologyRuntimeStatus; source: string };
}

export interface TopologyEdge {
  source: string;
  target: string;
  mode: 'orchestrates' | 'requires';
}

export interface StarmappingTopology {
  schema_version: string;
  updated_at: string;
  center: TopologyCenter;
  agents: TopologyAgentNode[];
  edges: TopologyEdge[];
}

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
  sender: 'user' | 'aegis' | string;
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
    source?: 'main' | 'delegate';
    remoteInteractionId?: string | null;
    allowSession?: boolean;
    allowPermanent?: boolean;
  } | null;
  pendingClarify?: {
    clarifyId: string;
    question: string;
    choices: string[];
    awaitingText: boolean;
    multiSelect?: boolean;
    source?: 'main' | 'delegate';
    remoteInteractionId?: string | null;
  } | null;
  hasUnread?: boolean;
  transportState?: 'idle' | 'connecting' | 'connected' | 'error' | 'closed';
  workflowTraceVersion?: 1;
  workflowTrace?: WorkflowTraceEvent[];
}
