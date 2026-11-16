import { beforeEach, describe, expect, it } from 'vitest';

import {
  FRONTEND_SETTINGS_STORAGE_KEY,
  getFrontendSettings,
  setChatAutoOpenHtmlOnTaskComplete,
} from './frontendSettings';

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

describe('frontend settings', () => {
  beforeEach(() => {
    storage = createMemoryStorage();
  });

  it('defaults to automatically opening HTML files and only disables on an explicit false value', () => {
    expect(getFrontendSettings(storage).chatAutoOpenHtmlOnTaskComplete).toBe(true);

    storage.setItem(FRONTEND_SETTINGS_STORAGE_KEY, '{invalid json');
    expect(getFrontendSettings(storage).chatAutoOpenHtmlOnTaskComplete).toBe(true);

    storage.setItem(FRONTEND_SETTINGS_STORAGE_KEY, JSON.stringify({
      chatAutoOpenHtmlOnTaskComplete: 'false',
    }));
    expect(getFrontendSettings(storage).chatAutoOpenHtmlOnTaskComplete).toBe(true);

    storage.setItem(FRONTEND_SETTINGS_STORAGE_KEY, JSON.stringify({
      chatAutoOpenHtmlOnTaskComplete: false,
    }));
    expect(getFrontendSettings(storage).chatAutoOpenHtmlOnTaskComplete).toBe(false);
  });

  it('persists a browser-local setting', () => {
    expect(setChatAutoOpenHtmlOnTaskComplete(false, storage)).toEqual({
      chatAutoOpenHtmlOnTaskComplete: false,
    });
    expect(getFrontendSettings(storage)).toEqual({ chatAutoOpenHtmlOnTaskComplete: false });
  });
});
