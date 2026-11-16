/**
 * `@` 快捷指令下拉菜单。命令列表懒加载（首次触发时 GET /api/chat/quick-commands，
 * 模块级缓存），按 query 过滤 name/desc；键盘导航由 Composer 掌管（activeIndex 下发）。
 */
import { useEffect, useState } from 'react';
import { Bot, Code2, ShieldAlert } from 'lucide-react';

import { fetchJSON } from '../../../lib/api';
import {
  ChatQuickCommand,
  ChatQuickCommandListResponse,
} from '../../lib/chatQuickCommands';

// 模块级缓存：跨组件实例共享，只拉取一次。
let cachedCommands: ChatQuickCommand[] | null = null;
let pendingLoad: Promise<ChatQuickCommand[]> | null = null;

function loadQuickCommands(): Promise<ChatQuickCommand[]> {
  if (cachedCommands) {
    return Promise.resolve(cachedCommands);
  }
  if (!pendingLoad) {
    pendingLoad = fetchJSON<ChatQuickCommandListResponse>('/api/chat/quick-commands')
      .then((response) => {
        cachedCommands = response.commands || [];
        return cachedCommands;
      })
      .catch((error: unknown) => {
        pendingLoad = null;
        throw error;
      });
  }
  return pendingLoad;
}

/** 懒加载快捷指令；enabled 首次为 true 时触发请求。 */
export function useQuickCommands(enabled: boolean): {
  commands: ChatQuickCommand[];
  loading: boolean;
  error: string;
} {
  const [commands, setCommands] = useState<ChatQuickCommand[]>(cachedCommands || []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!enabled || cachedCommands) {
      if (cachedCommands) setCommands(cachedCommands);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    loadQuickCommands()
      .then((loaded) => {
        if (!cancelled) setCommands(loaded);
      })
      .catch(() => {
        if (!cancelled) setError('Failed to load Quick Commands.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return { commands, loading, error };
}

export function filterQuickCommands(
  commands: ChatQuickCommand[],
  query: string,
): ChatQuickCommand[] {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return commands;
  return commands.filter((command) =>
    [command.type, command.name, command.desc].join(' ').toLocaleLowerCase().includes(normalized),
  );
}

function TypeIcon({ type }: { type: ChatQuickCommand['type'] }) {
  const className = 'h-3 w-3';
  if (type === 'agent') return <Bot className={className} aria-hidden="true" />;
  if (type === 'prompt') return <Code2 className={className} aria-hidden="true" />;
  return <ShieldAlert className={className} aria-hidden="true" />;
}

interface QuickCommandMenuProps {
  commands: ChatQuickCommand[];
  loading: boolean;
  error: string;
  activeIndex: number;
  onSelect: (command: ChatQuickCommand) => void;
}

export function QuickCommandMenu({
  commands,
  loading,
  error,
  activeIndex,
  onSelect,
}: QuickCommandMenuProps) {
  return (
    <div
      id="aisoc-quick-command-listbox"
      role="listbox"
      aria-label="Available Quick Commands"
      className="absolute bottom-full left-0 z-[var(--aisoc-z-drawer)] mb-1.5 max-h-56 w-full max-w-md overflow-y-auto rounded-[var(--aisoc-radius-md)] border border-[var(--aisoc-border)] bg-[var(--aisoc-panel-strong)] py-1 shadow-[var(--aisoc-shadow-soft)] backdrop-blur"
    >
      {loading ? (
        <div className="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-[var(--aisoc-muted)]" role="status">
          Loading Quick Commands…
        </div>
      ) : null}
      {error ? (
        <div className="px-3 py-2 text-[11px] text-[var(--aisoc-danger)]" role="alert">
          {error}
        </div>
      ) : null}
      {!loading && !error && commands.length === 0 ? (
        <div className="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-[var(--aisoc-muted)]">
          No matching commands
        </div>
      ) : null}
      {!loading && !error
        ? commands.map((command, index) => (
            <button
              key={`${command.type}-${command.name}`}
              id={`aisoc-quick-command-option-${index}`}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              onMouseDown={(event) => {
                // mousedown 防止 contentEditable 失焦
                event.preventDefault();
                onSelect(command);
              }}
              className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors ${
                index === activeIndex
                  ? 'bg-[var(--aisoc-accent-soft)]'
                  : 'hover:bg-[var(--aisoc-panel)]'
              }`}
            >
              <span className="shrink-0 text-[var(--aisoc-accent)]">
                <TypeIcon type={command.type} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[11px] font-bold text-[var(--aisoc-text)]">
                  {command.name}
                </span>
                <span className="block truncate text-[10px] text-[var(--aisoc-muted)]">
                  {command.desc || 'No description'}
                </span>
              </span>
              <span className="shrink-0 font-mono text-[9px] uppercase tracking-wider text-[var(--aisoc-muted)]">
                {command.type}
              </span>
            </button>
          ))
        : null}
    </div>
  );
}
