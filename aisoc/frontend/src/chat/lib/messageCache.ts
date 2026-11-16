import type { Message } from '../types';

export type CachedMessagesByConversation = Record<string, Message[]>;

const STORAGE_PREFIX = 'aisoc_chat_messages';
const STORAGE_VERSION = 1;
/** Message text can be long, so this is more conservative than the trace
 * event cap — still much more generous than the old widget's 50/tab, since
 * this is one full session, not a bounded set of tabs. */
const MAX_MESSAGES_PER_CONVERSATION = 200;

interface MessageCacheStoragePayload {
  version: number;
  conversations: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function normalizeMessage(value: unknown): Message | null {
  if (!isRecord(value)) return null;
  const { id, sender, text, timestamp } = value;
  if (typeof id !== 'string' || !id) return null;
  if (typeof sender !== 'string' || !sender) return null;
  if (typeof text !== 'string') return null;
  if (typeof timestamp !== 'string') return null;
  // Everything else on Message is optional and was produced by our own
  // chatRuntime.tsx, so pass it through once the required fields check out.
  return { ...value, id, sender, text, timestamp } as Message;
}

export function getMessageCacheStorageKey(userId: string): string | null {
  const normalizedUserId = userId.trim();
  return normalizedUserId
    ? `${STORAGE_PREFIX}:v${STORAGE_VERSION}:${encodeURIComponent(normalizedUserId)}`
    : null;
}

export function loadCachedMessages(
  userId: string,
  conversationIds: Iterable<string>,
  storage: Storage = window.localStorage,
): CachedMessagesByConversation {
  const key = getMessageCacheStorageKey(userId);
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

    const restored: CachedMessagesByConversation = {};
    for (const [conversationId, value] of Object.entries(payload.conversations)) {
      if (!allowedConversationIds.has(conversationId) || !Array.isArray(value)) {
        continue;
      }
      const messages = value.map(normalizeMessage).filter((message): message is Message => message !== null);
      if (messages.length === 0) continue;
      restored[conversationId] = messages;
    }
    return restored;
  } catch {
    return {};
  }
}

export function saveCachedMessages(
  userId: string,
  messagesByConversation: CachedMessagesByConversation,
  conversationIds: Iterable<string>,
  storage: Storage = window.localStorage,
): void {
  const key = getMessageCacheStorageKey(userId);
  if (!key) return;

  const allowedConversationIds = new Set(conversationIds);
  const conversations: Record<string, Message[]> = {};
  for (const [conversationId, messages] of Object.entries(messagesByConversation)) {
    if (!allowedConversationIds.has(conversationId) || !messages || messages.length === 0) {
      continue;
    }
    conversations[conversationId] =
      messages.length > MAX_MESSAGES_PER_CONVERSATION
        ? messages.slice(messages.length - MAX_MESSAGES_PER_CONVERSATION)
        : messages;
  }

  try {
    if (Object.keys(conversations).length === 0) {
      storage.removeItem(key);
      return;
    }
    const payload: MessageCacheStoragePayload = {
      version: STORAGE_VERSION,
      conversations,
    };
    storage.setItem(key, JSON.stringify(payload));
  } catch {
    // Local UI state remains usable when storage is unavailable or full.
  }
}

export function clearCachedMessages(
  userId: string,
  storage: Storage = window.localStorage,
): void {
  const key = getMessageCacheStorageKey(userId);
  if (!key) return;
  try {
    storage.removeItem(key);
  } catch {
    // Local UI state remains usable when storage access is unavailable.
  }
}
