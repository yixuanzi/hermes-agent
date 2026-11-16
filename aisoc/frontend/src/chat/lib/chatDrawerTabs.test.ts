import { beforeEach, describe, expect, it } from 'vitest';

import {
  clearCachedDrawerTabs,
  getDrawerTabsStorageKey,
  loadCachedDrawerTabs,
  saveCachedDrawerTabs,
} from './chatDrawerTabs';

// The repo's jsdom localStorage is unreliable in this suite (known conflict),
// so exercise the explicit `storage` parameter with an in-memory Storage.
function createMemoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => (data.has(key) ? data.get(key)! : null),
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key);
    },
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  };
}

let storage: Storage;

beforeEach(() => {
  storage = createMemoryStorage();
});

describe('chat drawer tab cache', () => {
  it('persists only valid tab metadata for existing conversations', () => {
    saveCachedDrawerTabs('user-a', {
      'conversation-a': {
        activeTab: 'file:reports%2Fsummary.html',
        tabs: [
          {
            id: 'file:reports%2Fsummary.html',
            path: 'reports/summary.html',
            title: 'summary.html',
          },
        ],
      },
      'deleted-conversation': {
        activeTab: 'workflow',
        tabs: [
          { id: 'file:old.txt', path: 'old.txt', title: 'old.txt' },
        ],
      },
    }, ['conversation-a'], storage);

    const key = getDrawerTabsStorageKey('user-a');
    expect(key).not.toBeNull();
    expect(JSON.parse(storage.getItem(key!) || '{}')).toEqual({
      version: 1,
      conversations: {
        'conversation-a': {
          activeTab: 'file:reports%2Fsummary.html',
          tabs: [{
            id: 'file:reports%2Fsummary.html',
            path: 'reports/summary.html',
            title: 'summary.html',
          }],
        },
      },
    });
    expect(loadCachedDrawerTabs('user-a', ['conversation-a'], storage)).toEqual({
      'conversation-a': {
        activeTab: 'file:reports%2Fsummary.html',
        tabs: [{
          id: 'file:reports%2Fsummary.html',
          path: 'reports/summary.html',
          title: 'summary.html',
        }],
      },
    });
  });

  it('isolates users and safely ignores corrupt or outdated entries', () => {
    saveCachedDrawerTabs('user-a', {
      'conversation-a': {
        activeTab: 'workflow',
        tabs: [{ id: 'file:reports%2Fa.html', path: 'reports/a.html', title: 'a.html' }],
      },
    }, ['conversation-a'], storage);

    expect(loadCachedDrawerTabs('user-b', ['conversation-a'], storage)).toEqual({});
    const userBKey = getDrawerTabsStorageKey('user-b');
    storage.setItem(userBKey!, '{invalid json');
    expect(loadCachedDrawerTabs('user-b', ['conversation-a'], storage)).toEqual({});
    storage.setItem(userBKey!, JSON.stringify({ version: 0, conversations: {} }));
    expect(loadCachedDrawerTabs('user-b', ['conversation-a'], storage)).toEqual({});
  });

  it('removes the current user cache when requested', () => {
    saveCachedDrawerTabs('user-a', {
      'conversation-a': {
        activeTab: 'workflow',
        tabs: [{ id: 'file:reports%2Fa.html', path: 'reports/a.html', title: 'a.html' }],
      },
    }, ['conversation-a'], storage);

    clearCachedDrawerTabs('user-a', storage);
    expect(storage.getItem(getDrawerTabsStorageKey('user-a')!)).toBeNull();
  });
});
