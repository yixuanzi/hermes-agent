import { afterEach, describe, expect, it } from 'vitest';
import {
  DEFAULT_AEGIS_THEME,
  THEME_STORAGE_KEY,
  applyTheme,
  getStoredTheme,
} from './theme';

afterEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.aegisTheme;
});

describe('Aegis themes', () => {
  it('falls back to Neutral Ops when no valid local preference exists', () => {
    expect(DEFAULT_AEGIS_THEME).toBe('neutral-ops');
    expect(getStoredTheme()).toBe('neutral-ops');

    window.localStorage.setItem(THEME_STORAGE_KEY, 'unknown-theme');
    expect(getStoredTheme()).toBe('neutral-ops');
  });

  it('applies and persists a selected theme', () => {
    applyTheme('daylight-signal');

    expect(document.documentElement).toHaveAttribute('data-aegis-theme', 'daylight-signal');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('daylight-signal');
    expect(getStoredTheme()).toBe('daylight-signal');
  });
});
