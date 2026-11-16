/**
 * contentEditable 输入区：
 * - Enter 发送 / Shift+Enter 换行；
 * - 输入 `@` 唤起 QuickCommandMenu（findShortcutQuery 基于纯文本 + 光标位置），
 *   选中命令后 `@[type_name]` token 渲染成不可编辑 pill（内部维护纯文本值）；
 * - 粘贴图片 / 选择文件 → POST /api/chat/attachments（原生 fetch，fetchJSON 会
 *   强制 JSON Content-Type，不适用于 multipart）；
 * - running 时展示「打断」按钮 → interruptActiveConversation。
 */
import { useEffect, useRef, useState } from 'react';
import { LayoutTemplate, OctagonPause, Paperclip, Send } from 'lucide-react';

import { getStoredToken } from '../../../lib/auth';
import { insertAgent2UIComposerText } from '../../lib/agent2ui';
import {
  findShortcutQuery,
  quickCommandToken,
  ChatQuickCommand,
  ShortcutQuery,
} from '../../lib/chatQuickCommands';
import { subscribeComposerInsert } from '../../lib/composerBus';
import { getThemeMessageArgument } from '../../lib/theme';
import { useChatRuntime } from '../../runtime/chatRuntime';
import { ChatAttachment, Conversation } from '../../types';
import { AttachmentChips, PendingAttachment } from './AttachmentChips';
import {
  getComposerCaretOffset,
  renderComposerContent,
  setComposerCaretOffset,
} from './composerDom';
import { filterQuickCommands, QuickCommandMenu, useQuickCommands } from './QuickCommandMenu';

function isConversationBusy(conversation?: Conversation): boolean {
  if (!conversation) return false;
  if (conversation.lastKnownRunState === 'waiting_for_approval') return true;
  if (conversation.lastKnownRunState === 'waiting_for_clarify') {
    return !conversation.pendingClarify?.awaitingText;
  }
  return false;
}

async function uploadAttachmentFile(file: File): Promise<ChatAttachment> {
  const form = new FormData();
  form.append('file', file, file.name);
  const headers: Record<string, string> = {};
  const token = getStoredToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch('/api/chat/attachments', { method: 'POST', body: form, headers });
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText);
  }
  const payload = (await response.json()) as { attachment: ChatAttachment };
  return payload.attachment;
}

const A2UI_TOGGLE_STORAGE_KEY = 'aisoc_chat_a2ui_enabled';

function loadA2uiToggle(): boolean {
  try {
    return window.localStorage.getItem(A2UI_TOGGLE_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function saveA2uiToggle(enabled: boolean): void {
  try {
    window.localStorage.setItem(A2UI_TOGGLE_STORAGE_KEY, enabled ? '1' : '0');
  } catch {
    // Preference persistence is best effort only.
  }
}

export function Composer() {
  const {
    activeConversation,
    submitInput,
    interruptActiveConversation,
    rejectedInput,
    clearRejectedInput,
  } = useChatRuntime();
  const [inputVal, setInputVal] = useState('');
  const [a2uiEnabled, setA2uiEnabled] = useState<boolean>(() => loadA2uiToggle());
  const [shortcutQuery, setShortcutQuery] = useState<ShortcutQuery | null>(null);
  const [activeCommandIndex, setActiveCommandIndex] = useState(0);
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const composerRef = useRef<HTMLDivElement>(null);
  const domValueRef = useRef('');
  const pendingCaretRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachmentsRef = useRef<PendingAttachment[]>([]);

  const { commands, loading: commandsLoading, error: commandsError } = useQuickCommands(
    shortcutQuery !== null,
  );
  const matchingCommands = shortcutQuery ? filterQuickCommands(commands, shortcutQuery.query) : [];
  const busy = isConversationBusy(activeConversation);
  const running = activeConversation?.lastKnownRunState === 'running';
  const clarifyAwaitingText = Boolean(activeConversation?.pendingClarify?.awaitingText);

  // ---- contentEditable：纯文本值 + token pill 同步 ----
  function syncRichComposer(text: string) {
    const composer = composerRef.current;
    if (!composer) return;
    renderComposerContent(composer, text, new Set(commands.map(quickCommandToken)));
    domValueRef.current = text;
  }

  // state 与 DOM 不一致时（选择指令 / 恢复被拒消息 / 清空）重建 DOM 并复位光标。
  useEffect(() => {
    if (domValueRef.current !== inputVal) {
      syncRichComposer(inputVal);
    }
    const caret = pendingCaretRef.current;
    if (caret === null) return;
    pendingCaretRef.current = null;
    composerRef.current?.focus();
    setComposerCaretOffset(composerRef.current, caret);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputVal, commands]);

  // 被拒绝的输入回填到 composer。
  useEffect(() => {
    if (!rejectedInput) return;
    pendingCaretRef.current = rejectedInput.text.length;
    setInputVal((current) => current || rejectedInput.text);
    setShortcutQuery(null);
    clearRejectedInput();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rejectedInput]);

  // Agent2UI 回流：drawer 预览里验证过的 postMessage 意图插入草稿（不发送）。
  useEffect(
    () =>
      subscribeComposerInsert(({ text }) => {
        const caret = getComposerCaretOffset(composerRef.current);
        const inserted = insertAgent2UIComposerText(domValueRef.current, text, caret);
        pendingCaretRef.current = inserted.caret;
        setInputVal(inserted.text);
        setShortcutQuery(null);
      }),
    [],
  );

  useEffect(() => {
    attachmentsRef.current = pendingAttachments;
  }, [pendingAttachments]);
  useEffect(
    () => () => {
      attachmentsRef.current.forEach((item) => {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      });
    },
    [],
  );

  function updateShortcutQuery(value: string, caret: number | null) {
    const next = findShortcutQuery(value, caret ?? value.length);
    setShortcutQuery((previous) => {
      const changed =
        previous?.query !== next?.query || previous?.start !== next?.start || previous?.end !== next?.end;
      if (changed) setActiveCommandIndex(0);
      return next;
    });
  }

  function readComposerValue() {
    const composer = composerRef.current;
    if (!composer) return;
    const value = (composer.textContent || '').replace(/\u00a0/g, ' ');
    domValueRef.current = value;
    setInputVal(value);
    updateShortcutQuery(value, getComposerCaretOffset(composerRef.current));
  }

  function selectQuickCommand(command: ChatQuickCommand) {
    const query = shortcutQuery;
    if (!query) return;
    const token = quickCommandToken(command);
    const nextValue = `${inputVal.slice(0, query.start)}${token} ${inputVal.slice(query.end)}`;
    pendingCaretRef.current = query.start + token.length + 1;
    setInputVal(nextValue);
    setShortcutQuery(null);
  }

  /** Backspace 时整体删除光标前的 token pill。 */
  function removeTokenBeforeCaret(): boolean {
    const selection = window.getSelection();
    const caret = getComposerCaretOffset(composerRef.current);
    if (caret === null || !selection?.isCollapsed) return false;
    const trailingSpace = inputVal[caret - 1] === ' ' ? 1 : 0;
    const tokenEnd = caret - trailingSpace;
    const matched = commands
      .map(quickCommandToken)
      .sort((left, right) => right.length - left.length)
      .find((token) => inputVal.slice(0, tokenEnd).endsWith(token));
    if (!matched) return false;
    const tokenStart = tokenEnd - matched.length;
    pendingCaretRef.current = tokenStart;
    setInputVal(`${inputVal.slice(0, tokenStart)}${inputVal.slice(caret)}`);
    setShortcutQuery(null);
    return true;
  }

  // ---- 附件 ----

  async function uploadAttachment(localId: string, file: File) {
    try {
      const attachment = await uploadAttachmentFile(file);
      setPendingAttachments((current) =>
        current.map((item) =>
          item.localId === localId ? { ...item, status: 'ready', attachment, error: undefined } : item,
        ),
      );
    } catch (error) {
      setPendingAttachments((current) =>
        current.map((item) =>
          item.localId === localId
            ? { ...item, status: 'failed', error: error instanceof Error ? error.message : 'Upload failed' }
            : item,
        ),
      );
    }
  }

  function queueAttachments(files: FileList | File[]) {
    const uploads = Array.from(files).map((file) => ({
      localId: `upload-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      file,
      previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined,
      status: 'uploading' as const,
    }));
    if (uploads.length === 0) return;
    setPendingAttachments((current) => [...current, ...uploads]);
    uploads.forEach((upload) => void uploadAttachment(upload.localId, upload.file));
  }

  function removeAttachment(localId: string) {
    setPendingAttachments((current) =>
      current.filter((item) => {
        if (item.localId === localId && item.previewUrl) URL.revokeObjectURL(item.previewUrl);
        return item.localId !== localId;
      }),
    );
  }

  function retryAttachment(localId: string) {
    const target = pendingAttachments.find((item) => item.localId === localId);
    if (!target) return;
    setPendingAttachments((current) =>
      current.map((item) =>
        item.localId === localId ? { ...item, status: 'uploading', error: undefined } : item,
      ),
    );
    void uploadAttachment(localId, target.file);
  }

  // ---- 事件 ----

  function handleSubmit() {
    const attachments = pendingAttachments
      .filter((item) => item.status === 'ready' && item.attachment)
      .map((item) => item.attachment as ChatAttachment);
    if (!inputVal.trim() && attachments.length === 0) return;
    const text =
      a2uiEnabled && !inputVal.includes('@[instruct_a2ui]') ? `@[instruct_a2ui]\n${inputVal}` : inputVal;
    submitInput(text, attachments, {
      theme_color: getThemeMessageArgument(),
      date: new Date().toISOString(),
    });
    setInputVal('');
    syncRichComposer('');
    setShortcutQuery(null);
    setPendingAttachments((current) => {
      current.forEach((item) => {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      });
      return [];
    });
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (shortcutQuery && matchingCommands.length > 0) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setActiveCommandIndex((current) => (current + 1) % matchingCommands.length);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setActiveCommandIndex((current) => (current - 1 + matchingCommands.length) % matchingCommands.length);
        return;
      }
      if ((event.key === 'Enter' || event.key === 'Tab') && matchingCommands[activeCommandIndex]) {
        event.preventDefault();
        selectQuickCommand(matchingCommands[activeCommandIndex]);
        return;
      }
    }
    if (shortcutQuery && event.key === 'Escape') {
      event.preventDefault();
      setShortcutQuery(null);
      return;
    }
    if (event.key === 'Backspace' && removeTokenBeforeCaret()) {
      event.preventDefault();
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      handleSubmit();
    }
  }

  function handlePaste(event: React.ClipboardEvent<HTMLDivElement>) {
    const images = Array.from(event.clipboardData.files).filter((file) =>
      file.type.startsWith('image/'),
    );
    if (images.length > 0) {
      event.preventDefault();
      queueAttachments(images);
      return;
    }
    const pasted = event.clipboardData.getData('text/plain');
    if (!pasted) return;
    event.preventDefault();
    const selection = window.getSelection();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (!range) return;
    range.deleteContents();
    const textNode = document.createTextNode(pasted);
    range.insertNode(textNode);
    range.setStartAfter(textNode);
    range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
    readComposerValue();
  }

  const placeholder = clarifyAwaitingText
    ? 'Type your clarification, Enter to send'
    : 'Type a message, @ for Quick Commands, Enter to send / Shift+Enter for newline';
  const sendDisabled = busy || (!inputVal.trim() && !pendingAttachments.some((item) => item.status === 'ready'));

  return (
    <div className="shrink-0 border-t border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)] p-3">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="sr-only"
        aria-label="Upload chat attachment"
        onChange={(event) => queueAttachments(event.target.files || [])}
      />
      <div className="space-y-2">
        <AttachmentChips attachments={pendingAttachments} onRemove={removeAttachment} onRetry={retryAttachment} />
        <div className="flex items-end gap-2">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={busy}
            title="Add attachment"
            aria-label="Add attachment"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] text-[var(--aisoc-muted)] transition-colors hover:border-[var(--aisoc-border-strong)] hover:text-[var(--aisoc-text)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Paperclip className="h-4 w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() =>
              setA2uiEnabled((current) => {
                const next = !current;
                saveA2uiToggle(next);
                return next;
              })
            }
            disabled={busy}
            aria-pressed={a2uiEnabled}
            title="A2UI: deliver task results as an interactive HTML page"
            aria-label="Toggle A2UI mode"
            className={`flex h-9 shrink-0 items-center gap-1.5 rounded-[var(--aisoc-radius-sm)] border px-2.5 font-mono text-[11px] font-bold transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              a2uiEnabled
                ? 'border-[color-mix(in_srgb,var(--aisoc-accent)_45%,var(--aisoc-border))] bg-[var(--aisoc-accent-soft)] text-[var(--aisoc-accent)]'
                : 'border-[var(--aisoc-border)] text-[var(--aisoc-muted)] hover:border-[var(--aisoc-border-strong)] hover:text-[var(--aisoc-text)]'
            }`}
          >
            <LayoutTemplate className="h-3.5 w-3.5" aria-hidden="true" />
            A2UI
          </button>

          <div className="relative min-w-0 flex-1">
            {shortcutQuery ? (
              <QuickCommandMenu
                commands={matchingCommands}
                loading={commandsLoading}
                error={commandsError}
                activeIndex={activeCommandIndex}
                onSelect={selectQuickCommand}
              />
            ) : null}
            <div
              ref={composerRef}
              role="combobox"
              aria-multiline="true"
              aria-label="Chat input"
              aria-expanded={Boolean(shortcutQuery)}
              aria-controls={shortcutQuery ? 'aisoc-quick-command-listbox' : undefined}
              aria-activedescendant={
                shortcutQuery && matchingCommands.length > 0
                  ? `aisoc-quick-command-option-${activeCommandIndex}`
                  : undefined
              }
              aria-autocomplete="list"
              aria-disabled={busy}
              contentEditable={!busy}
              suppressContentEditableWarning
              data-placeholder={placeholder}
              onInput={readComposerValue}
              onKeyDown={handleKeyDown}
              onKeyUp={() => updateShortcutQuery(domValueRef.current, getComposerCaretOffset(composerRef.current))}
              onMouseUp={() => updateShortcutQuery(domValueRef.current, getComposerCaretOffset(composerRef.current))}
              onPaste={handlePaste}
              className="max-h-40 min-h-9 w-full overflow-y-auto whitespace-pre-wrap break-words rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg)] px-2.5 py-2 text-[13px] leading-relaxed text-[var(--aisoc-text)] outline-none transition-colors focus:border-[color-mix(in_srgb,var(--aisoc-accent)_45%,var(--aisoc-border))] aria-disabled:cursor-not-allowed aria-disabled:opacity-60 empty:before:pointer-events-none empty:before:text-[var(--aisoc-muted)] empty:before:content-[attr(data-placeholder)]"
            />
          </div>

          {running ? (
            <button
              type="button"
              onClick={interruptActiveConversation}
              className="flex h-9 shrink-0 items-center gap-1.5 rounded-[var(--aisoc-radius-sm)] border border-[color-mix(in_srgb,var(--aisoc-danger)_40%,var(--aisoc-border))] px-3 font-mono text-[11px] font-bold text-[var(--aisoc-danger)] transition-colors hover:bg-[color-mix(in_srgb,var(--aisoc-danger)_10%,transparent)]"
            >
              <OctagonPause className="h-3.5 w-3.5" aria-hidden="true" />
              Stop
            </button>
          ) : null}
          <button
            type="button"
            onClick={handleSubmit}
            disabled={sendDisabled}
            className="flex h-9 shrink-0 items-center gap-1.5 rounded-[var(--aisoc-radius-sm)] border border-[color-mix(in_srgb,var(--aisoc-accent)_45%,var(--aisoc-border))] bg-[var(--aisoc-accent-soft)] px-3.5 font-mono text-[11px] font-bold text-[var(--aisoc-accent)] transition-colors hover:bg-[color-mix(in_srgb,var(--aisoc-accent)_22%,transparent)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" aria-hidden="true" />
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
