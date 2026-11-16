export const AGENT2UI_CHANNEL = "aegis-agent2ui";
export const AGENT2UI_VERSION = 1;
export const AGENT2UI_MAX_TEXT_LENGTH = 8_000;

export interface Agent2UIComposerInsertIntent {
  channel: typeof AGENT2UI_CHANNEL;
  version: typeof AGENT2UI_VERSION;
  type: "composer.insert";
  text: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function parseAgent2UIComposerInsertIntent(
  value: unknown,
): Agent2UIComposerInsertIntent | null {
  if (
    !isRecord(value) ||
    value.channel !== AGENT2UI_CHANNEL ||
    value.version !== AGENT2UI_VERSION ||
    value.type !== "composer.insert" ||
    typeof value.text !== "string"
  ) {
    return null;
  }

  const text = value.text;
  if (!text.trim() || text.length > AGENT2UI_MAX_TEXT_LENGTH) {
    return null;
  }

  return {
    channel: AGENT2UI_CHANNEL,
    version: AGENT2UI_VERSION,
    type: "composer.insert",
    text,
  };
}

export function insertAgent2UIComposerText(
  draft: string,
  insertion: string,
  caret: number | null,
): { text: string; caret: number } {
  const position = Math.min(
    draft.length,
    Math.max(0, caret === null ? draft.length : caret),
  );
  return {
    text: `${draft.slice(0, position)}${insertion}${draft.slice(position)}`,
    caret: position + insertion.length,
  };
}
