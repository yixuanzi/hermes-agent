export const THEME_STORAGE_KEY = 'aegis_theme';

export const AEGIS_THEMES = [
  {
    id: 'daylight-signal',
    name: 'Daylight Signal',
    description: 'Bright clarity for daytime investigation and report review.',
    mode: 'light',
    preview: {
      background: '#F4F7FB',
      surface: '#FFFFFF',
      accent: '#087EA4',
      text: '#142235',
      muted: '#52667B',
      border: '#C6D2E0',
    },
  },
  {
    id: 'aegis-night',
    name: 'Aegis Night',
    description: 'Deep-space contrast for focused night-shift operations.',
    mode: 'dark',
    preview: {
      background: '#020408',
      surface: '#05080F',
      accent: '#22D3EE',
      text: '#F8FAFC',
      muted: '#94A3B8',
      border: '#1E293B',
    },
  },
  {
    id: 'neutral-ops',
    name: 'Neutral Ops',
    description: 'Low-saturation steel for prolonged monitoring sessions.',
    mode: 'dark',
    preview: {
      background: '#16181D',
      surface: '#202329',
      accent: '#8AA4BF',
      text: '#E6E8EC',
      muted: '#A6ADB7',
      border: '#3D434D',
    },
  },
] as const;

export type AegisTheme = (typeof AEGIS_THEMES)[number]['id'];

export const DEFAULT_AEGIS_THEME: AegisTheme = 'neutral-ops';

function isAegisTheme(value: string | null): value is AegisTheme {
  return AEGIS_THEMES.some((theme) => theme.id === value);
}

export function getStoredTheme(storage: Storage = window.localStorage): AegisTheme {
  const storedTheme = storage.getItem(THEME_STORAGE_KEY);
  return isAegisTheme(storedTheme) ? storedTheme : DEFAULT_AEGIS_THEME;
}

export function applyTheme(theme: AegisTheme, storage: Storage = window.localStorage): void {
  document.documentElement.dataset.aegisTheme = theme;
  storage.setItem(THEME_STORAGE_KEY, theme);
}

export function initializeTheme(): AegisTheme {
  const theme = getStoredTheme();
  applyTheme(theme);
  return theme;
}
