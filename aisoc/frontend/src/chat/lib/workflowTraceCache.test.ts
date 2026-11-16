import { beforeEach, describe, expect, it } from 'vitest';

import type { WorkflowTraceEvent } from '../types';
import {
  clearCachedWorkflowTrace,
  getWorkflowTraceStorageKey,
  loadCachedWorkflowTrace,
  saveCachedWorkflowTrace,
} from './workflowTraceCache';

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

function event(overrides: Partial<WorkflowTraceEvent> & { id: string; timestamp: number }): WorkflowTraceEvent {
  return {
    type: 'tool.completed',
    source: 'main',
    ...overrides,
  };
}

let storage: Storage;

beforeEach(() => {
  storage = createMemoryStorage();
});

describe('workflow trace cache', () => {
  it('persists and restores trace events only for allowed conversations', () => {
    saveCachedWorkflowTrace(
      'user-a',
      {
        'conv-a': [event({ id: 'e1', timestamp: 1 }), event({ id: 'e2', timestamp: 2 })],
        'deleted-conv': [event({ id: 'e3', timestamp: 3 })],
      },
      ['conv-a'],
      storage,
    );

    const key = getWorkflowTraceStorageKey('user-a');
    expect(key).not.toBeNull();
    expect(JSON.parse(storage.getItem(key!) || '{}')).toEqual({
      version: 1,
      conversations: {
        'conv-a': [event({ id: 'e1', timestamp: 1 }), event({ id: 'e2', timestamp: 2 })],
      },
    });

    expect(loadCachedWorkflowTrace('user-a', ['conv-a'], storage)).toEqual({
      'conv-a': [event({ id: 'e1', timestamp: 1 }), event({ id: 'e2', timestamp: 2 })],
    });
    // Not in the allowed set at load time either, even though it was saved.
    expect(loadCachedWorkflowTrace('user-a', ['deleted-conv'], storage)).toEqual({});
  });

  it('caps each conversation to the most recent events on save', () => {
    const events = Array.from({ length: 520 }, (_, index) =>
      event({ id: `e${index}`, timestamp: index }),
    );
    saveCachedWorkflowTrace('user-a', { 'conv-a': events }, ['conv-a'], storage);

    const restored = loadCachedWorkflowTrace('user-a', ['conv-a'], storage);
    expect(restored['conv-a']).toHaveLength(500);
    expect(restored['conv-a']?.[0]?.id).toBe('e20');
    expect(restored['conv-a']?.at(-1)?.id).toBe('e519');
  });

  it('isolates users and safely ignores corrupt, outdated, or malformed entries', () => {
    saveCachedWorkflowTrace(
      'user-a',
      { 'conv-a': [event({ id: 'e1', timestamp: 1 })] },
      ['conv-a'],
      storage,
    );

    expect(loadCachedWorkflowTrace('user-b', ['conv-a'], storage)).toEqual({});

    const userBKey = getWorkflowTraceStorageKey('user-b');
    storage.setItem(userBKey!, '{invalid json');
    expect(loadCachedWorkflowTrace('user-b', ['conv-a'], storage)).toEqual({});

    storage.setItem(userBKey!, JSON.stringify({ version: 0, conversations: {} }));
    expect(loadCachedWorkflowTrace('user-b', ['conv-a'], storage)).toEqual({});

    // Malformed individual events (missing/invalid required fields) are
    // dropped rather than invalidating the whole conversation's cache.
    storage.setItem(
      userBKey!,
      JSON.stringify({
        version: 1,
        conversations: {
          'conv-a': [
            { id: 'ok', type: 'tool.completed', timestamp: 1, source: 'main' },
            { id: 'no-type', timestamp: 2, source: 'main' },
            { id: 'bad-type', type: 'not-a-real-type', timestamp: 3, source: 'main' },
            { id: 'bad-source', type: 'tool.completed', timestamp: 4, source: 'other' },
            { type: 'tool.completed', timestamp: 5, source: 'main' },
            'not-an-object',
          ],
        },
      }),
    );
    expect(loadCachedWorkflowTrace('user-b', ['conv-a'], storage)).toEqual({
      'conv-a': [{ id: 'ok', type: 'tool.completed', timestamp: 1, source: 'main' }],
    });
  });

  it('removes the current user cache when requested', () => {
    saveCachedWorkflowTrace(
      'user-a',
      { 'conv-a': [event({ id: 'e1', timestamp: 1 })] },
      ['conv-a'],
      storage,
    );

    clearCachedWorkflowTrace('user-a', storage);
    expect(storage.getItem(getWorkflowTraceStorageKey('user-a')!)).toBeNull();
  });

  it('removes the storage entry entirely once no conversation has any events left', () => {
    saveCachedWorkflowTrace(
      'user-a',
      { 'conv-a': [event({ id: 'e1', timestamp: 1 })] },
      ['conv-a'],
      storage,
    );
    saveCachedWorkflowTrace('user-a', { 'conv-a': [] }, ['conv-a'], storage);

    expect(storage.getItem(getWorkflowTraceStorageKey('user-a')!)).toBeNull();
  });
});
