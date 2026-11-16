/**
 * 聊天气泡的身份展示辅助：用户侧的"生成头像"（无需上传/存储图片，同一用户名
 * 每次结果稳定），以及 AI 侧固定的产品身份文案。取色沿用 AISOC 青→蓝→紫签名
 * 色系（见 theme.ts），保证跟主题视觉一致。
 */

/** AI 侧固定人设名称——全局统一，不按 agent profile 切换。 */
export const ASSISTANT_NAME = 'AISOC';

const AVATAR_PALETTE = ['#22d3ee', '#38bdf8', '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7'];

export interface GeneratedAvatar {
  initials: string;
  color: string;
}

/** 对种子字符串(通常是 username)做稳定 hash，派生一致的"色块 + 首字母"头像。 */
export function initialsAvatar(seed: string): GeneratedAvatar {
  const normalized = seed.trim();
  let hash = 0;
  for (let i = 0; i < normalized.length; i++) {
    hash = (hash * 31 + normalized.charCodeAt(i)) | 0;
  }
  const color = AVATAR_PALETTE[Math.abs(hash) % AVATAR_PALETTE.length];
  const initials = normalized.slice(0, 1).toUpperCase() || '?';
  return { initials, color };
}
