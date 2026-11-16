/**
 * drawer 文件预览：GET /api/chat/drawer-html?path=... 按 type 渲染。
 * html → sandboxed iframe（srcDoc）；markdown → react-markdown；
 * 图片（content 为 data URI）→ <img>；其它 → <pre><code>。
 *
 * Agent2UI 回流：只接受来自当前 iframe contentWindow、且通过协议校验
 * （channel/version/type/text 限长）的 postMessage，插入 composer 草稿，
 * 绝不自动发送。
 */
import { useEffect, useRef, useState } from 'react';
import { LoaderCircle } from 'lucide-react';

import { fetchJSON } from '../../../lib/api';
import { parseAgent2UIComposerInsertIntent } from '../../lib/agent2ui';
import { dispatchComposerInsert } from '../../lib/composerBus';
import { ChatMarkdown } from '../bubbles/AssistantBubble';

const IMAGE_TYPES = new Set(['avif', 'bmp', 'gif', 'ico', 'jpeg', 'jpg', 'png', 'svg', 'webp']);

export interface DrawerFilePayload {
  title: string;
  type: string;
  content: string;
}

interface DrawerFilePreviewProps {
  path: string;
  /** 自增可强制重新拉取（同路径文件被 agent 重写后刷新） */
  refreshNonce?: number;
}

export function DrawerFilePreview({ path, refreshNonce = 0 }: DrawerFilePreviewProps) {
  const [payload, setPayload] = useState<DrawerFilePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  useEffect(() => {
    function handleAgent2UIMessage(event: MessageEvent) {
      const frame = iframeRef.current;
      if (!frame || event.source !== frame.contentWindow) {
        return;
      }
      const intent = parseAgent2UIComposerInsertIntent(event.data);
      if (intent) {
        dispatchComposerInsert(intent.text);
      }
    }
    window.addEventListener('message', handleAgent2UIMessage);
    return () => window.removeEventListener('message', handleAgent2UIMessage);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchJSON<DrawerFilePayload>(`/api/chat/drawer-html?path=${encodeURIComponent(path)}`)
      .then((response) => {
        if (!cancelled) setPayload(response);
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : 'Failed to load file preview.');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path, refreshNonce]);

  if (loading && !payload) {
    return (
      <div className="flex items-center gap-2 p-4 font-mono text-[10px] uppercase tracking-wider text-[var(--aisoc-muted)]" role="status">
        <LoaderCircle className="h-3 w-3 animate-spin" aria-hidden="true" />
        Loading file preview…
      </div>
    );
  }
  if (error && !payload) {
    return (
      <div className="m-4 rounded-[var(--aisoc-radius-sm)] border border-[color-mix(in_srgb,var(--aisoc-danger)_35%,var(--aisoc-border))] bg-[color-mix(in_srgb,var(--aisoc-danger)_8%,transparent)] p-3 text-xs text-[var(--aisoc-danger)]" role="alert">
        {error}
      </div>
    );
  }
  if (!payload || !payload.content) {
    return (
      <div className="p-4 font-mono text-[10px] uppercase tracking-wider text-[var(--aisoc-muted)]" role="status">
        Binary file — cannot preview
      </div>
    );
  }
  if (payload.type === 'markdown') {
    return (
      <div className="h-full overflow-y-auto p-4 text-[13px] text-[var(--aisoc-text)]">
        <ChatMarkdown content={payload.content} />
      </div>
    );
  }
  if (IMAGE_TYPES.has(payload.type)) {
    return (
      <div className="flex h-full items-start justify-center overflow-auto p-4">
        <img src={payload.content} alt={payload.title} className="max-w-full rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)]" />
      </div>
    );
  }
  if (payload.type !== 'html') {
    return (
      <pre className="h-full overflow-auto p-4 font-mono text-[11px] leading-relaxed text-[var(--aisoc-text)]">
        <code>{payload.content}</code>
      </pre>
    );
  }
  return (
    <iframe
      ref={iframeRef}
      title={payload.title || 'AISOC file preview'}
      data-testid="drawer-html-frame"
      className="h-full w-full border-0 bg-white"
      sandbox="allow-scripts allow-forms"
      referrerPolicy="no-referrer"
      srcDoc={payload.content}
    />
  );
}
