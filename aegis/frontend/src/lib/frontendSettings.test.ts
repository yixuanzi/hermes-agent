import { beforeEach, describe, expect, it } from 'vitest';

import {
  FRONTEND_SETTINGS_STORAGE_KEY,
  getFrontendSettings,
  setChatAutoOpenHtmlOnTaskComplete,
} from './frontendSettings';

describe('frontend settings', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('defaults to automatically opening HTML files and only disables on an explicit false value', () => {
    expect(getFrontendSettings().chatAutoOpenHtmlOnTaskComplete).toBe(true);

    window.localStorage.setItem(FRONTEND_SETTINGS_STORAGE_KEY, '{invalid json');
    expect(getFrontendSettings().chatAutoOpenHtmlOnTaskComplete).toBe(true);

    window.localStorage.setItem(FRONTEND_SETTINGS_STORAGE_KEY, JSON.stringify({
      chatAutoOpenHtmlOnTaskComplete: 'false',
    }));
    expect(getFrontendSettings().chatAutoOpenHtmlOnTaskComplete).toBe(true);

    window.localStorage.setItem(FRONTEND_SETTINGS_STORAGE_KEY, JSON.stringify({
      chatAutoOpenHtmlOnTaskComplete: false,
    }));
    expect(getFrontendSettings().chatAutoOpenHtmlOnTaskComplete).toBe(false);
  });

  it('persists a browser-local setting', () => {
    expect(setChatAutoOpenHtmlOnTaskComplete(false)).toEqual({
      chatAutoOpenHtmlOnTaskComplete: false,
    });
    expect(getFrontendSettings()).toEqual({ chatAutoOpenHtmlOnTaskComplete: false });
  });
});
