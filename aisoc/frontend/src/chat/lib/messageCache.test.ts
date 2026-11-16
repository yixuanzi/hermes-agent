import { beforeEach, describe, expect, it } from 'vitest';

import type { Message } from '../types';
import {
  clearCachedMessages,
  getMessageCacheStorageKey,
  loadCachedMessages,
  saveCachedMessages,
} from './messageCache';

// The repo's jsdom localStorage is unreliable in this suite (known conflict,
// see chatDrawerTabs.test.ts), so exercise the explicit `storage` parameter
// with an in-memory Storage.
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

function message(overrides: Partial<Message> & { id: string }): Message {
  return {
    sender: 'user',
    text: 'hi',
    timestamp: '10:00',
    ...overrides,
  };
}

let storage: Storage;

beforeEach(() => {
  storage = createMemoryStorage();
});

describe('message cache', () => {
  it('persists and restores messages only for allowed conversations', () => {
    saveCachedMessages(
      'user-a',
      {
        'conv-a': [message({ id: 'm1' }), message({ id: 'm2', sender: 'assistant', text: 'hello' })],
        'deleted-conv': [message({ id: 'm3' })],
      },
      ['conv-a'],
      storage,
    );

    const key = getMessageCacheStorageKey('user-a');
    expect(key).not.toBeNull();
    expect(JSON.parse(storage.getItem(key!) || '{}')).toEqual({
      version: 1,
      conversations: {
        'conv-a': [message({ id: 'm1' }), message({ id: 'm2', sender: 'assistant', text: 'hello' })],
      },
    });

    expect(loadCachedMessages('user-a', ['conv-a'], storage)).toEqual({
      'conv-a': [message({ id: 'm1' }), message({ id: 'm2', sender: 'assistant', text: 'hello' })],
    });
    expect(loadCachedMessages('user-a', ['deleted-conv'], storage)).toEqual({});
  });

  it('keeps rich tool-call fields (kind/chainSteps/delegateTools) intact through a round trip', () => {
    const toolMessage = message({
      id: 'm-tools',
      sender: 'assistant',
      text: '',
      kind: 'main-tools',
      turnId: 't1',
      source: 'main',
      chainSteps: [
        { agentName: 'run_shell', type: 'vip_tool', status: 'Completed', message: 'ok', timestamp: '10:01' },
      ],
    });
    saveCachedMessages('user-a', { 'conv-a': [toolMessage] }, ['conv-a'], storage);
    expect(loadCachedMessages('user-a', ['conv-a'], storage)).toEqual({ 'conv-a': [toolMessage] });
  });

  it('caps each conversation to the most recent messages on save', () => {
    const messages = Array.from({ length: 210 }, (_, index) => message({ id: `m${index}` }));
    saveCachedMessages('user-a', { 'conv-a': messages }, ['conv-a'], storage);

    const restored = loadCachedMessages('user-a', ['conv-a'], storage);
    expect(restored['conv-a']).toHaveLength(200);
    expect(restored['conv-a']?.[0]?.id).toBe('m10');
    expect(restored['conv-a']?.at(-1)?.id).toBe('m209');
  });

  it('isolates users and safely ignores corrupt, outdated, or malformed entries', () => {
    saveCachedMessages('user-a', { 'conv-a': [message({ id: 'm1' })] }, ['conv-a'], storage);

    expect(loadCachedMessages('user-b', ['conv-a'], storage)).toEqual({});

    const userBKey = getMessageCacheStorageKey('user-b');
    storage.setItem(userBKey!, '{invalid json');
    expect(loadCachedMessages('user-b', ['conv-a'], storage)).toEqual({});

    storage.setItem(userBKey!, JSON.stringify({ version: 0, conversations: {} }));
    expect(loadCachedMessages('user-b', ['conv-a'], storage)).toEqual({});

    storage.setItem(
      userBKey!,
      JSON.stringify({
        version: 1,
        conversations: {
          'conv-a': [
            { id: 'ok', sender: 'user', text: 'hi', timestamp: '10:00' },
            { id: 'no-sender', text: 'hi', timestamp: '10:00' },
            { id: 'no-text', sender: 'user', timestamp: '10:00' },
            { sender: 'user', text: 'hi', timestamp: '10:00' },
            'not-an-object',
          ],
        },
      }),
    );
    expect(loadCachedMessages('user-b', ['conv-a'], storage)).toEqual({
      'conv-a': [{ id: 'ok', sender: 'user', text: 'hi', timestamp: '10:00' }],
    });
  });

  it('removes the current user cache when requested', () => {
    saveCachedMessages('user-a', { 'conv-a': [message({ id: 'm1' })] }, ['conv-a'], storage);

    clearCachedMessages('user-a', storage);
    expect(storage.getItem(getMessageCacheStorageKey('user-a')!)).toBeNull();
  });

  it('removes the storage entry entirely once no conversation has any messages left', () => {
    saveCachedMessages('user-a', { 'conv-a': [message({ id: 'm1' })] }, ['conv-a'], storage);
    saveCachedMessages('user-a', { 'conv-a': [] }, ['conv-a'], storage);

    expect(storage.getItem(getMessageCacheStorageKey('user-a')!)).toBeNull();
  });
});
