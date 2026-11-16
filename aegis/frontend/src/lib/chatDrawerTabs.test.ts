import { afterEach, describe, expect, it } from 'vitest';

import {
  clearCachedDrawerTabs,
  getDrawerTabsStorageKey,
  loadCachedDrawerTabs,
  saveCachedDrawerTabs,
} from './chatDrawerTabs';

afterEach(() => {
  window.localStorage.clear();
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
    }, ['conversation-a']);

    const key = getDrawerTabsStorageKey('user-a');
    expect(key).not.toBeNull();
    expect(JSON.parse(window.localStorage.getItem(key!) || '{}')).toEqual({
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
    expect(loadCachedDrawerTabs('user-a', ['conversation-a'])).toEqual({
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
    }, ['conversation-a']);

    expect(loadCachedDrawerTabs('user-b', ['conversation-a'])).toEqual({});
    const userBKey = getDrawerTabsStorageKey('user-b');
    window.localStorage.setItem(userBKey!, '{invalid json');
    expect(loadCachedDrawerTabs('user-b', ['conversation-a'])).toEqual({});
    window.localStorage.setItem(userBKey!, JSON.stringify({ version: 0, conversations: {} }));
    expect(loadCachedDrawerTabs('user-b', ['conversation-a'])).toEqual({});
  });

  it('removes the current user cache when requested', () => {
    saveCachedDrawerTabs('user-a', {
      'conversation-a': {
        activeTab: 'workflow',
        tabs: [{ id: 'file:reports%2Fa.html', path: 'reports/a.html', title: 'a.html' }],
      },
    }, ['conversation-a']);

    clearCachedDrawerTabs('user-a');
    expect(window.localStorage.getItem(getDrawerTabsStorageKey('user-a')!)).toBeNull();
  });
});
