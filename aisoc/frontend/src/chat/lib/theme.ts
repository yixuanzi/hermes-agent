/**
 * Chat theme palettes for AISOC "Command Deck".
 *
 * Values mirror the design tokens in src/design/tokens.css (the signature
 * cyan→blue→violet system documented in design-system/aisoc/MASTER.md). The
 * primary consumer is the A2UI `{theme_color}` message argument: agent-generated
 * HTML deliverables restyle themselves to match the host UI. `getActiveTheme()`
 * resolves the live dark/light mode from `readTheme()` (the <html data-theme>
 * truth source), so newly generated deliverables follow the current theme.
 *
 * The `getThemeMessageArgument` serialization SHAPE is a protocol contract and
 * must stay stable (keys unchanged); only the palette values vary by mode.
 */

import { readTheme } from "../../design/theme";

export interface AisocTheme {
  id: string;
  name: string;
  description: string;
  mode: 'dark' | 'light';
  preview: {
    background: string;
    surface: string;
    accent: string;
    text: string;
    muted: string;
    border: string;
  };
}

/** Command Deck 深色:深空海军蓝底 + 青色签名强调。 */
export const AISOC_COMMAND_DECK_DARK: AisocTheme = {
  id: 'aisoc-command-deck',
  name: 'AISOC Command Deck',
  description: 'Deep-space navy command deck with a cyan→blue→violet signature glow.',
  mode: 'dark',
  preview: {
    background: '#0a0f1a',
    surface: '#111a29',
    accent: '#22d3ee',
    text: '#eaf2ff',
    muted: '#93a4c4',
    border: '#1e2b45',
  },
};

/** Command Deck 浅色:纸感浅灰底 + 加深签名色(白底可读)。 */
export const AISOC_COMMAND_DECK_LIGHT: AisocTheme = {
  id: 'aisoc-command-deck-light',
  name: 'AISOC Command Deck · Light',
  description: 'Paper-light command deck with a deepened cyan→blue→violet signature accent.',
  mode: 'light',
  preview: {
    background: '#f4f6fb',
    surface: '#ffffff',
    accent: '#0891b2',
    text: '#0f1e33',
    muted: '#46587a',
    border: '#d3dcea',
  },
};

/** 兼容旧引用:默认(深色)主题别名。 */
export const AISOC_NIGHT_THEME = AISOC_COMMAND_DECK_DARK;

/** 解析当前生效主题(经 readTheme() 读取 <html data-theme>,SSR 下默认深色)。 */
export function getActiveTheme(): AisocTheme {
  return readTheme() === 'light' ? AISOC_COMMAND_DECK_LIGHT : AISOC_COMMAND_DECK_DARK;
}

/**
 * Serialize the active theme for the A2UI `{theme_color}` message argument
 * (consumed by the a2ui instruct template's `<theme>` contract).
 */
export function getThemeMessageArgument(theme: AisocTheme = getActiveTheme()): string {
  return JSON.stringify(
    {
      style: theme.description,
      background_surface: `${theme.preview.background} / ${theme.preview.surface}`,
      accent: theme.preview.accent,
      text_muted: `${theme.preview.text} / ${theme.preview.muted}`,
      border: theme.preview.border,
    },
    null,
    0,
  );
}
