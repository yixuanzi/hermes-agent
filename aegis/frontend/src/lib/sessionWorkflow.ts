import {
  Conversation,
  Message,
  WorkflowGraph,
  WorkflowGraphEdge,
  WorkflowGraphNode,
  WorkflowTraceEvent,
} from '../types';

export interface WorkflowSocketEvent {
  type: string;
  server_event_id?: string;
  ts?: number;
  turn_id?: string;
  source?: 'main' | 'delegate';
  srcagent?: string;
  client_msg_id?: string;
  message_id?: string;
  content?: string;
  completed?: boolean;
  tool_name?: string;
  tool_call_id?: string;
  args_preview?: string;
  result_preview?: string;
  child_session_id?: string;
  state?: string;
  reason?: string;
}

const WORKFLOW_EVENT_TYPES = new Set<WorkflowTraceEvent['type']>([
  'message.accepted',
  'message.completed',
  'message.stream.completed',
  'tool.started',
  'tool.completed',
  'run.state',
  'delegate.entered',
  'delegate.exited',
]);

function workflowEventId(payload: WorkflowSocketEvent): string {
  if (payload.server_event_id) {
    return payload.server_event_id;
  }
  return [
    payload.type,
    payload.turn_id || 'none',
    payload.tool_call_id || payload.state || payload.client_msg_id || 'event',
    payload.ts ?? 'local',
  ].join(':');
}

function delegateAgentFromArgs(argsPreview?: string): string | undefined {
  if (!argsPreview) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(argsPreview) as Record<string, unknown>;
    const candidate = parsed.agent_name || parsed.type;
    return typeof candidate === 'string' && candidate.trim() ? candidate.trim() : undefined;
  } catch {
    return undefined;
  }
}

interface DelegateContext {
  delegateId: string;
  parentTurnId?: string;
  srcagent?: string;
  childSessionId?: string;
  open: boolean;
  sequence: number;
}

function contextMatches(
  context: DelegateContext,
  srcagent?: string,
  childSessionId?: string,
): boolean {
  if (srcagent && context.srcagent && context.srcagent !== srcagent) {
    return false;
  }
  if (childSessionId && context.childSessionId && context.childSessionId !== childSessionId) {
    return false;
  }
  return true;
}

function findActiveDelegate(
  trace: WorkflowTraceEvent[],
  srcagent?: string,
  childSessionId?: string,
): DelegateContext | undefined {
  const contexts = new Map<string, DelegateContext>();
  let sequence = 0;
  trace.forEach((event) => {
    const isDelegateStart =
      event.type === 'tool.started' && event.toolName === 'aegis_a2a_delegate' && event.delegateId;
    if ((isDelegateStart || event.type === 'delegate.entered') && event.delegateId) {
      const existing = contexts.get(event.delegateId);
      contexts.set(event.delegateId, {
        delegateId: event.delegateId,
        parentTurnId: event.parentTurnId || existing?.parentTurnId || event.turnId,
        srcagent: event.srcagent || existing?.srcagent,
        childSessionId: event.childSessionId || existing?.childSessionId,
        open: true,
        sequence: sequence++,
      });
      return;
    }
    if (event.type !== 'delegate.exited') {
      return;
    }
    const matched = event.delegateId
      ? contexts.get(event.delegateId)
      : [...contexts.values()]
          .filter((context) => context.open && contextMatches(context, event.srcagent, event.childSessionId))
          .sort((left, right) => right.sequence - left.sequence)[0];
    if (matched) {
      matched.open = false;
    }
  });
  return [...contexts.values()]
    .filter((context) => context.open && contextMatches(context, srcagent, childSessionId))
    .sort((left, right) => right.sequence - left.sequence)[0];
}

export function applyWorkflowSocketEvent(
  conversation: Conversation,
  payload: WorkflowSocketEvent,
): Conversation {
  if (!WORKFLOW_EVENT_TYPES.has(payload.type as WorkflowTraceEvent['type'])) {
    return conversation;
  }
  const id = workflowEventId(payload);
  const trace = conversation.workflowTrace || [];
  if (trace.some((event) => event.id === id)) {
    return conversation;
  }
  const source = payload.source || 'main';
  const activeDelegate = findActiveDelegate(trace, payload.srcagent, payload.child_session_id);
  const isDelegateTool = payload.tool_name === 'aegis_a2a_delegate' && payload.turn_id;
  const delegateId = isDelegateTool
    ? `delegate:${payload.turn_id}:${payload.tool_call_id || id}`
    : source === 'delegate'
      ? activeDelegate?.delegateId ||
        (payload.type === 'delegate.entered' && payload.turn_id
          ? `delegate:${payload.turn_id}:${payload.child_session_id || id}`
          : undefined)
      : undefined;
  const parentTurnId = isDelegateTool
    ? payload.turn_id
    : source === 'delegate'
      ? activeDelegate?.parentTurnId ||
        (payload.type === 'delegate.entered' ? payload.turn_id : undefined)
      : undefined;
  const srcagent = payload.srcagent || (isDelegateTool ? delegateAgentFromArgs(payload.args_preview) : undefined) || activeDelegate?.srcagent;
  const event: WorkflowTraceEvent = {
    id,
    type: payload.type as WorkflowTraceEvent['type'],
    timestamp: payload.ts ?? Date.now() / 1000,
    turnId: payload.turn_id,
    source,
    parentTurnId,
    srcagent,
    clientMsgId: payload.client_msg_id,
    messageId: payload.message_id,
    content: payload.content,
    toolName: payload.tool_name,
    toolCallId: payload.tool_call_id,
    argsPreview: payload.args_preview,
    resultPreview: payload.result_preview,
    childSessionId: payload.child_session_id,
    delegateId,
    state: payload.state,
    reason: payload.reason,
  };
  const messages = payload.type === 'message.accepted' && payload.client_msg_id
    ? conversation.messages.map((message) =>
        message.clientMsgId === payload.client_msg_id
          ? {
              ...message,
              turnId: payload.turn_id,
              source,
              srcagent,
              workflowParentId: delegateId,
            }
          : message,
      )
    : conversation.messages;
  return {
    ...conversation,
    messages,
    workflowTraceVersion: 1,
    workflowTrace: [...trace, event],
  };
}

export interface ProjectSessionWorkflowInput {
  conversationId: string;
  title: string;
  messages: Message[];
  trace: WorkflowTraceEvent[];
  partial?: boolean;
}

export type WorkflowToolRunExpansion = Readonly<Record<string, number>>;

const MIN_TOOL_RUN_TO_COLLAPSE = 4;
const TOOL_HORIZONTAL_GAP = 196;
const COMPACT_GROUP_HORIZONTAL_GAP = 172;
const END_HORIZONTAL_GAP = 172;

interface ToolRun {
  id: string;
  nodes: WorkflowGraphNode[];
}

function findLinearToolRuns(graph: WorkflowGraph): ToolRun[] {
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const childrenById = new Map<string, WorkflowGraphNode[]>();
  graph.nodes.forEach((node) => {
    if (!node.parentId) {
      return;
    }
    const children = childrenById.get(node.parentId) || [];
    children.push(node);
    childrenById.set(node.parentId, children);
  });

  const visited = new Set<string>();
  const runs: ToolRun[] = [];
  const sameSourceChildren = (node: WorkflowGraphNode) =>
    (childrenById.get(node.id) || []).filter((child) => child.source === node.source);
  graph.nodes.forEach((node) => {
    if (node.kind !== 'tool' || visited.has(node.id)) {
      return;
    }
    const parent = node.parentId ? nodeById.get(node.parentId) : undefined;
    const parentChildren = parent ? sameSourceChildren(parent) : [];
    const parentContinuesRun =
      parent?.kind === 'tool' &&
      parent.source === node.source &&
      parentChildren.length === 1 &&
      parentChildren[0].id === node.id;
    if (parentContinuesRun) {
      return;
    }

    const runNodes = [node];
    visited.add(node.id);
    let cursor = node;
    while (true) {
      const children = sameSourceChildren(cursor);
      if (children.length !== 1 || children[0].kind !== 'tool') {
        break;
      }
      cursor = children[0];
      runNodes.push(cursor);
      visited.add(cursor.id);
    }
    runs.push({ id: `tool-run:${runNodes[0].id}`, nodes: runNodes });
  });
  return runs;
}

/**
 * Derives a compact, render-only graph without mutating the trace-backed graph.
 * Long linear tool runs keep their first/last nodes and progressively reveal
 * hidden tools from the execution-order side of the run.
 */
export function compactWorkflowGraph(
  graph: WorkflowGraph,
  revealedByRun: WorkflowToolRunExpansion = {},
): WorkflowGraph {
  const collapsibleRuns = findLinearToolRuns(graph).filter(
    (run) => run.nodes.length >= MIN_TOOL_RUN_TO_COLLAPSE,
  );
  if (collapsibleRuns.length === 0) {
    return graph;
  }

  const replacementByNodeId = new Map<string, string>();
  const groupByFirstHiddenId = new Map<string, WorkflowGraphNode>();
  collapsibleRuns.forEach((run) => {
    const hiddenTools = run.nodes.slice(1, -1);
    const requestedRevealCount = revealedByRun[run.id] || 0;
    const revealCount = Math.min(hiddenTools.length, Math.max(0, Math.floor(requestedRevealCount)));
    const remainingTools = hiddenTools.slice(revealCount);
    if (remainingTools.length === 0) {
      return;
    }

    const first = run.nodes[0];
    const last = run.nodes.at(-1) as WorkflowGraphNode;
    const firstHidden = remainingTools[0];
    const groupId = `tool-group:${first.id}:${last.id}`;
    remainingTools.forEach((node) => replacementByNodeId.set(node.id, groupId));
    groupByFirstHiddenId.set(firstHidden.id, {
      id: groupId,
      kind: 'tool-group',
      label: `+${remainingTools.length} tools`,
      detail: '',
      status: remainingTools.some((node) => node.status === 'running') ? 'running' : 'collapsed',
      source: firstHidden.source,
      turnId: firstHidden.turnId,
      parentId: firstHidden.parentId,
      agent: firstHidden.agent,
      timestamp: firstHidden.timestamp,
      toolRunId: run.id,
      hiddenToolCount: remainingTools.length,
      x: firstHidden.x,
      y: firstHidden.y,
      depth: firstHidden.depth,
    });
  });

  if (replacementByNodeId.size === 0) {
    return graph;
  }

  const originalNodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const edgeByTargetId = new Map(graph.edges.map((edge) => [edge.to, edge]));
  const visibleNodes: WorkflowGraphNode[] = [];
  graph.nodes.forEach((node) => {
    const group = groupByFirstHiddenId.get(node.id);
    if (group) {
      visibleNodes.push(group);
    }
    if (!replacementByNodeId.has(node.id)) {
      visibleNodes.push({ ...node });
    }
  });

  const visibleNodeById = new Map(visibleNodes.map((node) => [node.id, node]));
  const resolveVisibleParentId = (parentId?: string): string | undefined => {
    let cursorId = parentId;
    while (cursorId) {
      const replacementId = replacementByNodeId.get(cursorId);
      if (replacementId) {
        return replacementId;
      }
      if (visibleNodeById.has(cursorId)) {
        return cursorId;
      }
      cursorId = originalNodeById.get(cursorId)?.parentId;
    }
    return undefined;
  };

  visibleNodes.forEach((node) => {
    node.parentId = resolveVisibleParentId(node.parentId);
    if (!node.parentId) {
      node.depth = 0;
      return;
    }
    const parent = visibleNodeById.get(node.parentId);
    if (!parent) {
      return;
    }
    node.depth = parent.depth + 1;
    const horizontalGap =
      node.kind === 'end'
        ? END_HORIZONTAL_GAP
        : node.kind === 'tool-group'
          ? COMPACT_GROUP_HORIZONTAL_GAP
          : TOOL_HORIZONTAL_GAP;
    node.x = parent.x + horizontalGap;
  });

  const edges: WorkflowGraphEdge[] = visibleNodes.flatMap((node) => {
    if (!node.parentId) {
      return [];
    }
    const originalEdge = edgeByTargetId.get(node.id);
    return [{
      id: `${node.parentId}->${node.id}`,
      from: node.parentId,
      to: node.id,
      source: node.source,
      label: originalEdge?.label,
    }];
  });
  const maxX = visibleNodes.reduce((maximum, node) => Math.max(maximum, node.x), 0);
  const maxY = visibleNodes.reduce((maximum, node) => Math.max(maximum, node.y), 0);

  return {
    ...graph,
    nodes: visibleNodes,
    edges,
    width: Math.max(520, maxX + 220),
    height: Math.max(graph.height, maxY + 80),
  };
}

function deriveLegacyWorkflowTrace(messages: Message[]): WorkflowTraceEvent[] {
  const synthetic: WorkflowTraceEvent[] = [];
  const turnByMessageId = new Map<string, string>();
  const sourceByMessageId = new Map<string, 'main' | 'delegate'>();
  let sequence = 1;

  messages.forEach((message, index) => {
    if (message.sender !== 'user') {
      return;
    }
    const followingTurn = messages
      .slice(index + 1)
      .find((candidate) => candidate.sender === 'user' || candidate.turnId);
    const turnId = message.turnId ||
      (followingTurn?.sender === 'user' ? undefined : followingTurn?.turnId) ||
      `legacy-turn-${index + 1}`;
    const source = message.source ||
      (followingTurn?.sender === 'user' ? undefined : followingTurn?.source) ||
      'main';
    turnByMessageId.set(message.id, turnId);
    sourceByMessageId.set(message.id, source);
    synthetic.push({
      id: `legacy:accepted:${message.id}`,
      type: 'message.accepted',
      timestamp: sequence++,
      turnId,
      source,
      srcagent: message.srcagent,
      clientMsgId: message.id,
      delegateId: message.workflowParentId,
    });
  });

  let latestTurnId: string | undefined;
  let latestSource: 'main' | 'delegate' = 'main';
  let latestMainTurnId: string | undefined;
  const createdDelegateIds = new Set<string>();
  messages.forEach((message) => {
    if (message.sender === 'user') {
      latestTurnId = turnByMessageId.get(message.id);
      latestSource = sourceByMessageId.get(message.id) || message.source || 'main';
      if (latestSource === 'main') {
        latestMainTurnId = latestTurnId;
      }
      return;
    }
    const turnId = message.turnId || latestTurnId;
    const source = message.source || latestSource;
    if (!turnId) {
      return;
    }
    if (message.kind === 'main-tools') {
      latestMainTurnId = turnId;
      message.chainSteps?.forEach((step, index) => {
        synthetic.push({
          id: `legacy:tool:${turnId}:${step.id || index}`,
          type: 'tool.completed',
          timestamp: sequence++,
          turnId,
          source: 'main',
          toolName: step.agentName,
          toolCallId: step.id || `step-${index}`,
          resultPreview: step.message,
        });
      });
    }
    if (message.kind === 'delegate-tools') {
      const parentTurnId = latestMainTurnId || turnId;
      const agent = message.srcagent || 'delegate';
      const delegateId =
        message.workflowParentId ||
        `delegate:${parentTurnId}:legacy-${agent.replace(/[^a-zA-Z0-9_-]+/g, '-')}`;
      const acceptedDelegateTurn = synthetic.find(
        (event) =>
          event.type === 'message.accepted' &&
          event.source === 'delegate' &&
          event.turnId === turnId,
      );
      if (acceptedDelegateTurn) {
        acceptedDelegateTurn.delegateId = delegateId;
        acceptedDelegateTurn.parentTurnId = parentTurnId;
        acceptedDelegateTurn.srcagent = acceptedDelegateTurn.srcagent || message.srcagent;
      }
      if (!createdDelegateIds.has(delegateId)) {
        createdDelegateIds.add(delegateId);
        synthetic.push({
          id: `legacy:delegate:${delegateId}`,
          type: 'delegate.entered',
          timestamp: sequence++,
          turnId: parentTurnId,
          parentTurnId,
          source: 'delegate',
          srcagent: message.srcagent,
          delegateId,
        });
      }
      message.delegateTools?.forEach((tool, index) => {
        synthetic.push({
          id: `legacy:delegate-tool:${turnId}:${tool.id || index}`,
          type: tool.status === 'completed' ? 'tool.completed' : 'tool.started',
          timestamp: sequence++,
          turnId: acceptedDelegateTurn ? turnId : parentTurnId,
          source: 'delegate',
          srcagent: message.srcagent,
          delegateId,
          toolName: tool.toolName,
          toolCallId: tool.id || `delegate-step-${index}`,
          argsPreview: tool.argsPreview,
          resultPreview: tool.resultPreview,
        });
      });
    }
  });
  return synthetic;
}

function recoveredDelegateId(
  parentTurnId: string,
  event: WorkflowTraceEvent,
  lifecycleBoundary = false,
): string {
  const identity = event.childSessionId || (lifecycleBoundary ? event.id : event.srcagent) || event.id;
  return `delegate:${parentTurnId}:recovered-${identity.replace(/[^a-zA-Z0-9_-]+/g, '-')}`;
}

function normalizeDelegateTrace(
  trace: WorkflowTraceEvent[],
  messages: Message[],
): WorkflowTraceEvent[] {
  const messageByTurn = new Map(
    messages
      .filter((message) => message.sender === 'user' && message.turnId)
      .map((message) => [message.turnId as string, message]),
  );
  const contexts = new Map<string, DelegateContext>();
  let latestMainTurnId: string | undefined;
  let sequence = 0;

  const latestContext = (event: WorkflowTraceEvent, includeClosed = false) =>
    [...contexts.values()]
      .filter(
        (context) =>
          (includeClosed || context.open) &&
          contextMatches(context, event.srcagent, event.childSessionId),
      )
      .sort((left, right) => right.sequence - left.sequence)[0];

  return trace.map((original) => {
    let event = { ...original };
    if (event.type === 'message.accepted' && event.source === 'main' && event.turnId) {
      latestMainTurnId = event.turnId;
    }

    const isDelegateStart =
      event.type === 'tool.started' && event.toolName === 'aegis_a2a_delegate' && event.turnId;
    if (isDelegateStart) {
      const delegateId = event.delegateId || `delegate:${event.turnId}:${event.toolCallId || event.id}`;
      event = {
        ...event,
        delegateId,
        parentTurnId: event.parentTurnId || event.turnId,
      };
      contexts.set(delegateId, {
        delegateId,
        parentTurnId: event.parentTurnId,
        srcagent: event.srcagent,
        childSessionId: event.childSessionId,
        open: true,
        sequence: sequence++,
      });
      return event;
    }

    if (event.source !== 'delegate' && event.type !== 'delegate.entered' && event.type !== 'delegate.exited') {
      return event;
    }

    const messageDelegateId = event.turnId ? messageByTurn.get(event.turnId)?.workflowParentId : undefined;
    let context = event.delegateId ? contexts.get(event.delegateId) : undefined;
    if (!context && messageDelegateId) {
      context = contexts.get(messageDelegateId);
    }
    if (!context) {
      context = latestContext(event, event.type === 'delegate.exited');
    }

    if (event.type === 'delegate.entered') {
      const parentTurnId =
        event.parentTurnId || context?.parentTurnId || latestMainTurnId || event.turnId;
      if (!context && parentTurnId) {
        const delegateId = event.delegateId || recoveredDelegateId(parentTurnId, event, true);
        context = {
          delegateId,
          parentTurnId,
          srcagent: event.srcagent,
          childSessionId: event.childSessionId,
          open: true,
          sequence: sequence++,
        };
        contexts.set(delegateId, context);
      } else if (context) {
        context.open = true;
        context.sequence = sequence++;
        context.parentTurnId = parentTurnId || context.parentTurnId;
        context.srcagent = event.srcagent || context.srcagent;
        context.childSessionId = event.childSessionId || context.childSessionId;
      }
    }

    if (!context) {
      const parentTurnId = event.parentTurnId || latestMainTurnId;
      if (parentTurnId) {
        const delegateId = messageDelegateId || recoveredDelegateId(parentTurnId, event);
        context = contexts.get(delegateId) || {
          delegateId,
          parentTurnId,
          srcagent: event.srcagent,
          childSessionId: event.childSessionId,
          open: event.type !== 'delegate.exited',
          sequence: sequence++,
        };
        contexts.set(delegateId, context);
      }
    }

    if (context) {
      event = {
        ...event,
        delegateId: context.delegateId,
        parentTurnId: event.parentTurnId || context.parentTurnId,
        srcagent: event.srcagent || context.srcagent,
        childSessionId: event.childSessionId || context.childSessionId,
      };
      if (event.type === 'delegate.exited') {
        context.open = false;
      }
    }
    return event;
  });
}

function truncateLabel(value: string, fallback: string): string {
  const normalized = value.trim();
  if (!normalized) {
    return fallback;
  }
  return normalized.length > 24 ? `${normalized.slice(0, 24)}…` : normalized;
}

export function projectSessionWorkflow({
  conversationId,
  title,
  messages,
  trace,
  partial = false,
}: ProjectSessionWorkflowInput): WorkflowGraph {
  const rootId = `session:${conversationId}`;
  const effectiveTrace = trace.length ? trace : partial ? deriveLegacyWorkflowTrace(messages) : [];
  const orderedTrace = normalizeDelegateTrace(effectiveTrace
    .map((event, index) => ({ event, index }))
    .sort((left, right) => left.event.timestamp - right.event.timestamp || left.index - right.index)
    .map(({ event }) => event), messages);
  const acceptedMainTurns = orderedTrace.filter(
    (event, index, events) =>
      event.type === 'message.accepted' &&
      event.source === 'main' &&
      event.turnId &&
      events.findIndex(
        (candidate) =>
          candidate.type === 'message.accepted' &&
          candidate.source === 'main' &&
          candidate.turnId === event.turnId,
      ) === index,
  );
  const delegateTurns = orderedTrace.filter(
    (event, index, events) =>
      event.type === 'message.accepted' &&
      event.source === 'delegate' &&
      event.turnId &&
      events.findIndex(
        (candidate) =>
          candidate.type === 'message.accepted' &&
          candidate.source === 'delegate' &&
          candidate.turnId === event.turnId,
      ) === index,
  );
  const mainLaneByTurn = new Map<string, number>();
  let laneCursor = 44;
  acceptedMainTurns.forEach((event) => {
    const childCount = delegateTurns.filter(
      (delegateTurn) => delegateTurn.parentTurnId === event.turnId,
    ).length;
    const initialDelegateCount = new Set(
      orderedTrace
        .filter(
          (candidate) =>
            candidate.delegateId &&
            (candidate.parentTurnId === event.turnId || candidate.turnId === event.turnId) &&
            ((candidate.type === 'tool.started' && candidate.toolName === 'aegis_a2a_delegate') ||
              candidate.type === 'delegate.entered'),
        )
        .map((candidate) => candidate.delegateId as string),
    ).size;
    const branchCount = childCount + initialDelegateCount;
    const bandHeight = Math.max(116, branchCount * 72 + 68);
    mainLaneByTurn.set(event.turnId as string, laneCursor + bandHeight / 2);
    laneCursor += bandHeight;
  });
  const height = Math.max(360, laneCursor + 44);
  const rootY = acceptedMainTurns.length
    ? ((mainLaneByTurn.get(acceptedMainTurns[0].turnId as string) || height / 2) +
        (mainLaneByTurn.get(acceptedMainTurns[acceptedMainTurns.length - 1].turnId as string) || height / 2)) /
      2
    : height / 2;
  const root: WorkflowGraphNode = {
    id: rootId,
    kind: 'root',
    label: 'AEGIS',
    detail: title,
    status: acceptedMainTurns.length ? 'active' : 'idle',
    source: 'main',
    x: 72,
    y: rootY,
    depth: 0,
  };
  const nodes: WorkflowGraphNode[] = [root];
  const edges: WorkflowGraphEdge[] = [];
  const nodeById = new Map<string, WorkflowGraphNode>([[root.id, root]]);

  const addNode = (
    node: Omit<WorkflowGraphNode, 'x' | 'depth'>,
    edgeLabel?: string,
  ): WorkflowGraphNode | undefined => {
    if (nodeById.has(node.id)) {
      return nodeById.get(node.id);
    }
    const parent = node.parentId ? nodeById.get(node.parentId) : undefined;
    if (!parent) {
      return undefined;
    }
    const nextNode: WorkflowGraphNode = {
      ...node,
      x: parent.x + (node.kind === 'end' ? 172 : 196),
      depth: parent.depth + 1,
    };
    nodes.push(nextNode);
    nodeById.set(nextNode.id, nextNode);
    edges.push({
      id: `${parent.id}->${nextNode.id}`,
      from: parent.id,
      to: nextNode.id,
      source: nextNode.source,
      label: edgeLabel,
    });
    return nextNode;
  };

  const messageForTurn = (turnId?: string) => {
    const direct = messages.find(
      (candidate) => candidate.turnId === turnId && candidate.sender === 'user',
    );
    if (direct) {
      return direct;
    }
    const accepted = orderedTrace.find(
      (event) => event.type === 'message.accepted' && event.turnId === turnId,
    );
    return messages.find(
      (candidate) =>
        candidate.sender === 'user' &&
        (candidate.id === accepted?.clientMsgId || candidate.clientMsgId === accepted?.clientMsgId),
    );
  };
  const messageById = new Map(messages.map((message) => [message.id, message]));

  type ToolAggregate = {
    turnId: string;
    source: 'main' | 'delegate';
    toolCallId: string;
    toolName: string;
    firstEvent: WorkflowTraceEvent;
    argsPreview?: string;
    resultPreview?: string;
    completed: boolean;
    delegateId?: string;
    srcagent?: string;
  };
  const toolAggregates = new Map<string, ToolAggregate>();
  orderedTrace.forEach((event) => {
    if (
      (event.type !== 'tool.started' && event.type !== 'tool.completed') ||
      !event.turnId
    ) {
      return;
    }
    const toolCallId = event.toolCallId || event.id;
    const key = `${event.source}:${event.turnId}:${toolCallId}`;
    const existing = toolAggregates.get(key);
    toolAggregates.set(key, {
      turnId: event.turnId,
      source: event.source,
      toolCallId,
      toolName: event.toolName || existing?.toolName || 'Tool',
      firstEvent:
        existing && existing.firstEvent.timestamp <= event.timestamp ? existing.firstEvent : event,
      argsPreview: event.argsPreview || existing?.argsPreview,
      resultPreview: event.resultPreview || existing?.resultPreview,
      completed: Boolean(existing?.completed || event.type === 'tool.completed'),
      delegateId: event.delegateId || existing?.delegateId,
      srcagent: event.srcagent || existing?.srcagent,
    });
  });

  const aggregatesForTurn = (turnId: string, source: 'main' | 'delegate') =>
    [...toolAggregates.values()]
      .filter((tool) => tool.turnId === turnId && tool.source === source)
      .sort((left, right) => left.firstEvent.timestamp - right.firstEvent.timestamp);

  const terminalForTurn = (
    turnId: string,
    source: 'main' | 'delegate',
    delegateId?: string,
  ) =>
    orderedTrace
      .filter((event) => {
        if (event.type !== 'run.state' || event.turnId !== turnId || event.source !== source || !event.state) {
          return false;
        }
        if (source === 'delegate' && delegateId && event.delegateId !== delegateId) {
          return false;
        }
        return source === 'main'
          ? ['idle', 'error', 'interrupted'].includes(event.state)
          : ['waiting_for_delegate_input', 'error', 'interrupted'].includes(event.state);
      })
      .at(-1);

  const responseForTurn = (
    turnId: string,
    source: 'main' | 'delegate',
    delegateId?: string,
  ) =>
    orderedTrace
      .filter(
        (event) =>
          (event.type === 'message.completed' || event.type === 'message.stream.completed') &&
          event.turnId === turnId &&
          event.source === source &&
          (source === 'main' || !delegateId || event.delegateId === delegateId),
      )
      .at(-1);

  const finalMessageForTurn = (
    turnId: string,
    source: 'main' | 'delegate',
    delegateId?: string,
  ): string | undefined => {
    const response = responseForTurn(turnId, source, delegateId);
    const responseText = response?.content || (response?.messageId ? messageById.get(response.messageId)?.text : undefined);
    if (responseText?.trim()) {
      return responseText;
    }
    const matchingMessages = messages.filter(
      (message) =>
        message.sender !== 'user' &&
        (message.kind || 'chat') === 'chat' &&
        message.turnId === turnId &&
        message.source === source &&
        Boolean(message.text.trim()) &&
        (source === 'main' || !message.workflowParentId || message.workflowParentId === delegateId),
    );
    return matchingMessages.at(-1)?.text;
  };

  const endEventForTurn = (
    turnId: string,
    source: 'main' | 'delegate',
    delegateId?: string,
  ): WorkflowTraceEvent | undefined => {
    const terminal = terminalForTurn(turnId, source, delegateId);
    if (terminal) {
      return terminal;
    }
    const response = responseForTurn(turnId, source, delegateId);
    if (!response) {
      return undefined;
    }
    if (source === 'main' && response.type === 'message.stream.completed' && !response.content) {
      return undefined;
    }
    return {
      ...response,
      type: 'run.state',
      state: source === 'main' ? 'idle' : 'waiting_for_delegate_input',
    };
  };

  const endStatus = (state: string) =>
    state === 'idle' || state === 'waiting_for_delegate_input' ? 'completed' : state;

  const addEndNode = (
    event: WorkflowTraceEvent,
    parentId: string,
    y: number,
    agent?: string,
    delegateId?: string,
  ) => {
    if (!event.turnId || !event.state) {
      return;
    }
    const status = endStatus(event.state);
    addNode({
      id: event.source === 'main'
        ? `end:main:${event.turnId}`
        : `end:delegate:${delegateId || event.delegateId || 'unbound'}:${event.turnId}`,
      kind: 'end',
      label: status === 'completed' ? 'Task complete' : status === 'error' ? 'Task failed' : 'Interrupted',
      detail: event.reason || event.state,
      status,
      source: event.source,
      turnId: event.turnId,
      parentId,
      agent: event.srcagent || agent,
      timestamp: event.timestamp,
      finalMessage: finalMessageForTurn(event.turnId, event.source, delegateId || event.delegateId),
      y,
    });
  };

  acceptedMainTurns.forEach((event, index) => {
    const turnId = event.turnId as string;
    const message = messageForTurn(turnId);
    const y = mainLaneByTurn.get(turnId) || rootY;
    const terminal = endEventForTurn(turnId, 'main');
    const turnNode = addNode(
      {
        id: `turn:${turnId}`,
        kind: 'input',
        label: truncateLabel(message?.text || '', `Turn ${index + 1}`),
        detail: message?.text || '',
        status: terminal ? endStatus(terminal.state || '') : 'running',
        source: 'main',
        turnId,
        parentId: rootId,
        timestamp: event.timestamp,
        y,
      },
      'MAIN',
    );
    if (!turnNode) {
      return;
    }

    const fallbackDelegates = orderedTrace.filter(
      (candidate) =>
        candidate.type === 'delegate.entered' &&
        (candidate.parentTurnId === turnId || candidate.turnId === turnId) &&
        candidate.delegateId &&
        ![...toolAggregates.values()].some(
          (tool) => tool.toolName === 'aegis_a2a_delegate' && tool.delegateId === candidate.delegateId,
        ),
    );
    const actions: Array<
      | { kind: 'tool'; timestamp: number; tool: ToolAggregate }
      | { kind: 'delegate'; timestamp: number; event: WorkflowTraceEvent }
    > = [
      ...aggregatesForTurn(turnId, 'main').map((tool) => ({
        kind: 'tool' as const,
        timestamp: tool.firstEvent.timestamp,
        tool,
      })),
      ...fallbackDelegates.map((delegateEvent) => ({
        kind: 'delegate' as const,
        timestamp: delegateEvent.timestamp,
        event: delegateEvent,
      })),
    ].sort((left, right) => left.timestamp - right.timestamp);

    let tail = turnNode;
    actions.forEach((action) => {
      if (action.kind === 'delegate' || action.tool.toolName === 'aegis_a2a_delegate') {
        const delegateEvent = action.kind === 'delegate' ? action.event : action.tool.firstEvent;
        const delegateId =
          delegateEvent.delegateId ||
          `delegate:${turnId}:${action.kind === 'tool' ? action.tool.toolCallId : delegateEvent.id}`;
        const agent =
          delegateEvent.srcagent ||
          (action.kind === 'tool' ? action.tool.srcagent : undefined) ||
          delegateAgentFromArgs(action.kind === 'tool' ? action.tool.argsPreview : delegateEvent.argsPreview) ||
          'Delegate Agent';
        const exited = orderedTrace.some(
          (candidate) => candidate.type === 'delegate.exited' && candidate.delegateId === delegateId,
        );
        const next = addNode(
          {
            id: delegateId,
            kind: 'delegate',
            label: truncateLabel(agent, 'Delegate Agent'),
            detail: action.kind === 'tool' ? action.tool.argsPreview || agent : delegateEvent.argsPreview || agent,
            status: exited || (action.kind === 'tool' && action.tool.completed) ? 'completed' : 'running',
            source: 'delegate',
            turnId,
            parentId: tail.id,
            agent,
            timestamp: action.timestamp,
            argsPreview: action.kind === 'tool' ? action.tool.argsPreview : delegateEvent.argsPreview,
            resultPreview: action.kind === 'tool' ? action.tool.resultPreview : undefined,
            y,
          },
          `DELEGATE · ${agent}`,
        );
        if (next) {
          tail = next;
        }
        return;
      }
      const tool = action.tool;
      const next = addNode({
        id: `tool:${turnId}:${tool.toolCallId}`,
        kind: 'tool',
        label: truncateLabel(tool.toolName, 'Tool'),
        detail: tool.resultPreview || tool.argsPreview || tool.toolName,
        status: tool.completed ? 'completed' : 'running',
        source: 'main',
        turnId,
        parentId: tail.id,
        timestamp: tool.firstEvent.timestamp,
        argsPreview: tool.argsPreview,
        resultPreview: tool.resultPreview,
        y,
      });
      if (next) {
        tail = next;
      }
    });
    if (terminal) {
      addEndNode(terminal, tail.id, y);
    }
  });

  acceptedMainTurns.forEach((event) => {
    const turnId = event.turnId as string;
    const delegateNodes = nodes.filter(
      (node) => node.kind === 'delegate' && node.turnId === turnId,
    );
    const directDelegateTools = aggregatesForTurn(turnId, 'delegate');
    delegateNodes.forEach((delegateNode) => {
      const tools = directDelegateTools.filter(
        (tool) =>
          tool.delegateId === delegateNode.id ||
          (!tool.delegateId && delegateNodes.length === 1),
      );
      const userBranchCount = delegateTurns.filter(
        (delegateTurn) => delegateTurn.delegateId === delegateNode.id,
      ).length;
      const y = delegateNode.y + Math.max(72, ((userBranchCount + 1) / 2) * 72);
      let tail = delegateNode;
      tools.forEach((tool) => {
        const next = addNode({
          id: `tool:${turnId}:${tool.toolCallId}`,
          kind: 'tool',
          label: truncateLabel(tool.toolName, 'Tool'),
          detail: tool.resultPreview || tool.argsPreview || tool.toolName,
          status: tool.completed ? 'completed' : 'running',
          source: 'delegate',
          turnId,
          parentId: tail.id,
          agent: tool.srcagent || delegateNode.agent,
          timestamp: tool.firstEvent.timestamp,
          argsPreview: tool.argsPreview,
          resultPreview: tool.resultPreview,
          y,
        });
        if (next) {
          tail = next;
        }
      });
      const terminal = endEventForTurn(turnId, 'delegate', delegateNode.id);
      if (terminal) {
        addEndNode(terminal, tail.id, y, delegateNode.agent, delegateNode.id);
      }
    });
  });

  const delegateTurnIndexByDelegate = new Map<string, number>();
  delegateTurns.forEach((event, index) => {
    const turnId = event.turnId as string;
    const delegateId = event.delegateId || messageForTurn(turnId)?.workflowParentId;
    if (!delegateId) {
      return;
    }
    let delegateNode = nodeById.get(delegateId);
    const parentTurnId = event.parentTurnId;
    if (!delegateNode && parentTurnId) {
      const mainNode = nodeById.get(`turn:${parentTurnId}`);
      if (mainNode) {
        delegateNode = addNode(
          {
            id: delegateId,
            kind: 'delegate',
            label: truncateLabel(event.srcagent || '', 'Delegate Agent'),
            detail: event.srcagent || 'Delegate Agent',
            status: 'running',
            source: 'delegate',
            turnId: parentTurnId,
            parentId: mainNode.id,
            agent: event.srcagent,
            timestamp: event.timestamp,
            y: mainNode.y,
          },
          `DELEGATE · ${event.srcagent || 'agent'}`,
        );
      }
    }
    if (!delegateNode) {
      return;
    }
    const laneIndex = delegateTurnIndexByDelegate.get(delegateId) || 0;
    delegateTurnIndexByDelegate.set(delegateId, laneIndex + 1);
    const siblingCount = delegateTurns.filter(
      (candidate) => candidate.delegateId === delegateId,
    ).length;
    const y = delegateNode.y + (laneIndex - (siblingCount - 1) / 2) * 72;
    const message = messageForTurn(turnId);
    const terminal = endEventForTurn(turnId, 'delegate', delegateId);
    const turnNode = addNode(
      {
        id: `turn:${turnId}`,
        kind: 'input',
        label: truncateLabel(message?.text || '', `Delegate turn ${index + 1}`),
        detail: message?.text || '',
        status: terminal ? endStatus(terminal.state || '') : 'running',
        source: 'delegate',
        turnId,
        parentId: delegateId,
        agent: event.srcagent || delegateNode.agent,
        timestamp: event.timestamp,
        y,
      },
      `DELEGATE · ${event.srcagent || delegateNode.agent || 'agent'}`,
    );
    if (!turnNode) {
      return;
    }
    let tail = turnNode;
    aggregatesForTurn(turnId, 'delegate').forEach((tool) => {
      const next = addNode({
        id: `tool:${turnId}:${tool.toolCallId}`,
        kind: 'tool',
        label: truncateLabel(tool.toolName, 'Tool'),
        detail: tool.resultPreview || tool.argsPreview || tool.toolName,
        status: tool.completed ? 'completed' : 'running',
        source: 'delegate',
        turnId,
        parentId: tail.id,
        agent: event.srcagent || delegateNode?.agent,
        timestamp: tool.firstEvent.timestamp,
        argsPreview: tool.argsPreview,
        resultPreview: tool.resultPreview,
        y,
      });
      if (next) {
        tail = next;
      }
    });
    if (terminal) {
      addEndNode(terminal, tail.id, y, event.srcagent || delegateNode.agent, delegateId);
    }
  });

  const maxX = nodes.reduce((maximum, node) => Math.max(maximum, node.x), 0);
  const maxY = nodes.reduce((maximum, node) => Math.max(maximum, node.y), 0);
  const completedMainTurns = new Set(
    acceptedMainTurns
      .filter((event) => event.turnId && endEventForTurn(event.turnId, 'main'))
      .map((event) => event.turnId as string),
  );
  const graphStatus = partial
    ? 'partial'
    : acceptedMainTurns.length === 0
      ? 'empty'
      : acceptedMainTurns.every((event) => completedMainTurns.has(event.turnId as string))
        ? 'complete'
        : 'live';

  return {
    rootId,
    nodes,
    edges,
    status: graphStatus,
    width: Math.max(520, maxX + 220),
    height: Math.max(height, maxY + 80),
  };
}
