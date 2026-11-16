export type CachedDrawerTabId = `file:${string}`;
export type CachedWorkflowDrawerTab = 'workflow' | CachedDrawerTabId;

export interface CachedDynamicDrawerTab {
  id: CachedDrawerTabId;
  path: string;
  title: string;
}

export interface CachedSessionDrawerTabs {
  activeTab: CachedWorkflowDrawerTab;
  tabs: CachedDynamicDrawerTab[];
}

export type CachedDrawerTabsByConversation = Record<string, CachedSessionDrawerTabs>;

const STORAGE_PREFIX = 'aisoc_chat_drawer_tabs';
const STORAGE_VERSION = 1;

interface DrawerTabsStoragePayload {
  version: number;
  conversations: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function normalizePath(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const path = value.trim();
  return path ? path : null;
}

function drawerTabId(path: string): CachedDrawerTabId {
  return `file:${encodeURIComponent(path)}`;
}

function drawerTabTitle(path: string, title: unknown): string {
  if (typeof title === 'string' && title.trim()) return title.trim();
  return path.split('/').filter(Boolean).pop() || path;
}

export function getDrawerTabsStorageKey(userId: string): string | null {
  const normalizedUserId = userId.trim();
  return normalizedUserId
    ? `${STORAGE_PREFIX}:v${STORAGE_VERSION}:${encodeURIComponent(normalizedUserId)}`
    : null;
}

export function loadCachedDrawerTabs(
  userId: string,
  conversationIds: Iterable<string>,
  storage: Storage = window.localStorage,
): CachedDrawerTabsByConversation {
  const key = getDrawerTabsStorageKey(userId);
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

    const restored: CachedDrawerTabsByConversation = {};
    for (const [conversationId, value] of Object.entries(payload.conversations)) {
      if (!allowedConversationIds.has(conversationId) || !isRecord(value) || !Array.isArray(value.tabs)) {
        continue;
      }
      const seenPaths = new Set<string>();
      const tabs = value.tabs.flatMap((tab): CachedDynamicDrawerTab[] => {
        if (!isRecord(tab)) return [];
        const path = normalizePath(tab.path);
        if (!path || seenPaths.has(path)) return [];
        seenPaths.add(path);
        return [{ id: drawerTabId(path), path, title: drawerTabTitle(path, tab.title) }];
      });
      if (tabs.length === 0) continue;

      const activeTab =
        typeof value.activeTab === 'string' && tabs.some((tab) => tab.id === value.activeTab)
          ? value.activeTab as CachedDrawerTabId
          : 'workflow';
      restored[conversationId] = { activeTab, tabs };
    }
    return restored;
  } catch {
    return {};
  }
}

export function saveCachedDrawerTabs(
  userId: string,
  drawerTabsByConversation: CachedDrawerTabsByConversation,
  conversationIds: Iterable<string>,
  storage: Storage = window.localStorage,
): void {
  const key = getDrawerTabsStorageKey(userId);
  if (!key) return;

  const allowedConversationIds = new Set(conversationIds);
  const conversations: Record<string, CachedSessionDrawerTabs> = {};
  for (const [conversationId, sessionTabs] of Object.entries(drawerTabsByConversation)) {
    if (!allowedConversationIds.has(conversationId) || sessionTabs.tabs.length === 0) {
      continue;
    }
    const seenPaths = new Set<string>();
    const tabs = sessionTabs.tabs.flatMap((tab): CachedDynamicDrawerTab[] => {
      const path = normalizePath(tab.path);
      if (!path || seenPaths.has(path)) return [];
      seenPaths.add(path);
      return [{ id: drawerTabId(path), path, title: drawerTabTitle(path, tab.title) }];
    });
    if (tabs.length === 0) continue;
    conversations[conversationId] = {
      activeTab: tabs.some((tab) => tab.id === sessionTabs.activeTab)
        ? sessionTabs.activeTab
        : 'workflow',
      tabs,
    };
  }

  try {
    if (Object.keys(conversations).length === 0) {
      storage.removeItem(key);
      return;
    }
    const payload: DrawerTabsStoragePayload = {
      version: STORAGE_VERSION,
      conversations,
    };
    storage.setItem(key, JSON.stringify(payload));
  } catch {
    // Local UI state remains usable when storage is unavailable or full.
  }
}

export function clearCachedDrawerTabs(
  userId: string,
  storage: Storage = window.localStorage,
): void {
  const key = getDrawerTabsStorageKey(userId);
  if (!key) return;
  try {
    storage.removeItem(key);
  } catch {
    // Local UI state remains usable when storage access is unavailable.
  }
}
