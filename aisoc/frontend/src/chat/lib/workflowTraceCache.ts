import type { WorkflowTraceEvent, WorkflowTraceEventType } from '../types';

export type CachedWorkflowTraceByConversation = Record<string, WorkflowTraceEvent[]>;

const STORAGE_PREFIX = 'aisoc_chat_workflow_trace';
const STORAGE_VERSION = 1;
/** Trace events are small objects (id/type/timestamp/turnId/...), so this can
 * be far more generous than the drawer-tab or old-widget message caps. */
const MAX_EVENTS_PER_CONVERSATION = 500;

const WORKFLOW_EVENT_TYPES = new Set<WorkflowTraceEventType>([
  'message.accepted',
  'message.completed',
  'message.stream.completed',
  'tool.started',
  'tool.completed',
  'run.state',
  'delegate.entered',
  'delegate.exited',
]);

interface WorkflowTraceStoragePayload {
  version: number;
  conversations: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function normalizeEvent(value: unknown): WorkflowTraceEvent | null {
  if (!isRecord(value)) return null;
  const id = value.id;
  const type = value.type;
  const timestamp = value.timestamp;
  const source = value.source;
  if (typeof id !== 'string' || !id) return null;
  if (typeof type !== 'string' || !WORKFLOW_EVENT_TYPES.has(type as WorkflowTraceEventType)) return null;
  if (typeof timestamp !== 'number' || !Number.isFinite(timestamp)) return null;
  if (source !== 'main' && source !== 'delegate') return null;
  // Pass the rest of the (already-validated-shape) object through as-is —
  // the remaining fields are all optional and were produced by our own
  // sessionWorkflow.ts, so no per-field validation beyond the required ones.
  return { ...value, id, type, timestamp, source } as WorkflowTraceEvent;
}

export function getWorkflowTraceStorageKey(userId: string): string | null {
  const normalizedUserId = userId.trim();
  return normalizedUserId
    ? `${STORAGE_PREFIX}:v${STORAGE_VERSION}:${encodeURIComponent(normalizedUserId)}`
    : null;
}

export function loadCachedWorkflowTrace(
  userId: string,
  conversationIds: Iterable<string>,
  storage: Storage = window.localStorage,
): CachedWorkflowTraceByConversation {
  const key = getWorkflowTraceStorageKey(userId);
  if (!key) return {};

  const allowedConversationIds = new Set(conversationIds);
  try {
    const raw = storage.getItem(key);
    if (!raw) return {};
    const payload = JSON.parse(raw) as unknown;
    if (
      !isRecord(payload) ||
      payload.version !== STORAGE_VERSION ||
      !isRecord(payload.conversations)
    ) {
      return {};
    }

    const restored: CachedWorkflowTraceByConversation = {};
    for (const [conversationId, value] of Object.entries(payload.conversations)) {
      if (!allowedConversationIds.has(conversationId) || !Array.isArray(value)) {
        continue;
      }
      const events = value.map(normalizeEvent).filter((event): event is WorkflowTraceEvent => event !== null);
      if (events.length === 0) continue;
      restored[conversationId] = events;
    }
    return restored;
  } catch {
    return {};
  }
}

export function saveCachedWorkflowTrace(
  userId: string,
  tracesByConversation: CachedWorkflowTraceByConversation,
  conversationIds: Iterable<string>,
  storage: Storage = window.localStorage,
): void {
  const key = getWorkflowTraceStorageKey(userId);
  if (!key) return;

  const allowedConversationIds = new Set(conversationIds);
  const conversations: Record<string, WorkflowTraceEvent[]> = {};
  for (const [conversationId, events] of Object.entries(tracesByConversation)) {
    if (!allowedConversationIds.has(conversationId) || !events || events.length === 0) {
      continue;
    }
    conversations[conversationId] =
      events.length > MAX_EVENTS_PER_CONVERSATION
        ? events.slice(events.length - MAX_EVENTS_PER_CONVERSATION)
        : events;
  }

  try {
    if (Object.keys(conversations).length === 0) {
      storage.removeItem(key);
      return;
    }
    const payload: WorkflowTraceStoragePayload = {
      version: STORAGE_VERSION,
      conversations,
    };
    storage.setItem(key, JSON.stringify(payload));
  } catch {
    // Local UI state remains usable when storage is unavailable or full.
  }
}

export function clearCachedWorkflowTrace(
  userId: string,
  storage: Storage = window.localStorage,
): void {
  const key = getWorkflowTraceStorageKey(userId);
  if (!key) return;
  try {
    storage.removeItem(key);
  } catch {
    // Local UI state remains usable when storage access is unavailable.
  }
}
