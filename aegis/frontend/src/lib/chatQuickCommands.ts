export type ChatQuickCommandType = 'agent' | 'prompt' | 'instruct';

export interface ChatQuickCommand {
  type: ChatQuickCommandType;
  name: string;
  desc: string;
  content: string;
}

export interface ChatQuickCommandListResponse {
  commands: ChatQuickCommand[];
}

export interface ShortcutQuery {
  query: string;
  start: number;
  end: number;
}

export function quickCommandToken(command: ChatQuickCommand): string {
  return `@[${command.type}_${command.name}]`;
}

export function findShortcutQuery(text: string, caret: number): ShortcutQuery | null {
  const beforeCaret = text.slice(0, caret);
  const match = /(?:^|\s)@([^\s\[\]]*)$/.exec(beforeCaret);
  if (!match) return null;
  const start = beforeCaret.length - match[0].length + match[0].lastIndexOf('@');
  return { query: match[1], start, end: caret };
}
