/**
 * Composer contentEditable 的 DOM 工具：
 * - 纯文本 → 带不可编辑 token pill 的 DOM 片段；
 * - 光标偏移（纯文本坐标系）的读取与恢复。
 */

export const SHORTCUT_TOKEN_PATTERN = /@\[(?:agent|prompt|instruct)_[^\]]+\]/g;

const PILL_CLASS_NAME =
  'inline-block rounded border border-[color-mix(in_srgb,var(--aisoc-accent)_40%,var(--aisoc-border))] bg-[var(--aisoc-accent-soft)] px-1 font-mono text-[11px] font-bold text-[var(--aisoc-accent)]';

/** 把纯文本渲染进 composer：已知 token 渲染成 contentEditable=false 的 pill。 */
export function renderComposerContent(
  composer: HTMLElement,
  text: string,
  knownTokens: ReadonlySet<string>,
): void {
  const fragment = document.createDocumentFragment();
  SHORTCUT_TOKEN_PATTERN.lastIndex = 0;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = SHORTCUT_TOKEN_PATTERN.exec(text)) !== null) {
    if (match.index > cursor) {
      fragment.append(document.createTextNode(text.slice(cursor, match.index)));
    }
    const token = match[0];
    if (knownTokens.has(token)) {
      const pill = document.createElement('span');
      pill.contentEditable = 'false';
      pill.dataset.shortcutToken = token;
      pill.className = PILL_CLASS_NAME;
      pill.textContent = token;
      fragment.append(pill);
    } else {
      fragment.append(document.createTextNode(token));
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) {
    fragment.append(document.createTextNode(text.slice(cursor)));
  }
  composer.replaceChildren(fragment);
}

/** 当前光标在 composer 纯文本中的偏移；不在 composer 内时返回 null。 */
export function getComposerCaretOffset(composer: HTMLElement | null): number | null {
  const selection = window.getSelection();
  if (!composer || !selection?.rangeCount || !composer.contains(selection.anchorNode)) {
    return null;
  }
  const range = document.createRange();
  range.selectNodeContents(composer);
  range.setEnd(selection.anchorNode as Node, selection.anchorOffset);
  return range.toString().length;
}

/** 把光标恢复到纯文本偏移处（跨 text node / pill 边界）。 */
export function setComposerCaretOffset(composer: HTMLElement | null, offset: number): void {
  if (!composer) return;
  const selection = window.getSelection();
  const range = document.createRange();
  const walker = document.createTreeWalker(composer, NodeFilter.SHOW_TEXT);
  let remaining = offset;
  let node = walker.nextNode();
  while (node) {
    const length = node.textContent?.length || 0;
    if (remaining <= length) {
      range.setStart(node, remaining);
      range.collapse(true);
      selection?.removeAllRanges();
      selection?.addRange(range);
      return;
    }
    remaining -= length;
    node = walker.nextNode();
  }
  range.selectNodeContents(composer);
  range.collapse(false);
  selection?.removeAllRanges();
  selection?.addRange(range);
}
