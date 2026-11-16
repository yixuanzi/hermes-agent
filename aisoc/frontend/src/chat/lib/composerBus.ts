/**
 * Tiny window-scoped event bus for inserting draft text into the chat
 * composer from outside its component tree (e.g. Agent2UI postMessage
 * intents validated in the drawer preview iframe host).
 *
 * Draft-insertion only — nothing on this bus ever submits a message.
 */

const COMPOSER_INSERT_EVENT = 'aisoc:composer-insert';

export interface ComposerInsertDetail {
  text: string;
}

export function dispatchComposerInsert(text: string): void {
  const normalized = String(text || '');
  if (!normalized.trim()) {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<ComposerInsertDetail>(COMPOSER_INSERT_EVENT, { detail: { text: normalized } }),
  );
}

export function subscribeComposerInsert(
  handler: (detail: ComposerInsertDetail) => void,
): () => void {
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<ComposerInsertDetail>).detail;
    if (detail && typeof detail.text === 'string' && detail.text.trim()) {
      handler(detail);
    }
  };
  window.addEventListener(COMPOSER_INSERT_EVENT, listener);
  return () => window.removeEventListener(COMPOSER_INSERT_EVENT, listener);
}
