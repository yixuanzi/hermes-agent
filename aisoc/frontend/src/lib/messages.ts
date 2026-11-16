/**
 * Shared session message types and formatting utilities.
 * Used by SessionsPage and CronPage for rendering session messages.
 */

export type SessionMessage = {
  id?: string | number;
  role?: string;
  content?: unknown;
  timestamp?: number;
  [key: string]: unknown;
};

export type SessionMessagesResponse = {
  session_id: string;
  messages: SessionMessage[];
};

export function normalizeMessageText(input: string): string {
  return input
    .replace(/\r\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
}

export function safeJsonStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function normalizeToolCallArguments(argumentsValue: unknown): string {
  if (typeof argumentsValue !== "string") return safeJsonStringify(argumentsValue);
  const trimmed = argumentsValue.trim();
  if (!trimmed) return "";
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return trimmed;
  }
}

export function indentBlock(text: string, prefix = "  "): string {
  return text
    .split("\n")
    .map((line) => `${prefix}${line}`)
    .join("\n");
}

export function formatToolCallsPayload(toolCalls: unknown): string {
  if (!toolCalls) return "";
  if (!Array.isArray(toolCalls)) return safeJsonStringify(toolCalls);
  const blocks = toolCalls.map((item, index) => {
    const entry = item as Record<string, unknown>;
    const fn = (entry.function as Record<string, unknown> | undefined) || undefined;
    const header = `#${index + 1} ${String(fn?.name || "unknown_function")}`;
    const lines = [header];
    if (entry.id) lines.push(`id: ${String(entry.id)}`);
    if (entry.type) lines.push(`type: ${String(entry.type)}`);
    if (fn && Object.prototype.hasOwnProperty.call(fn, "arguments")) {
      const args = normalizeToolCallArguments(fn.arguments);
      lines.push("arguments:");
      lines.push(indentBlock(args || "(empty)"));
    }
    return lines.join("\n");
  });
  return blocks.join("\n\n");
}

export function formatMessagePayload(message: SessionMessage): string {
  const sections: string[] = [];
  const rawContent = message.content;

  if (typeof rawContent === "string" && rawContent.trim().length > 0) {
    sections.push(normalizeMessageText(rawContent));
  } else if (Array.isArray(rawContent)) {
    const parts: string[] = [];
    for (const block of rawContent) {
      if (block && typeof block === "object") {
        const b = block as Record<string, unknown>;
        if (b.type === "text" && typeof b.text === "string" && b.text.trim()) {
          parts.push(normalizeMessageText(b.text));
        } else if (b.type === "tool_use" || b.type === "tool_result") {
          const label = b.type === "tool_use" ? (b.name || "tool") : "result";
          let detail = "";
          if (b.input && typeof b.input === "object") detail = safeJsonStringify(b.input);
          else if (typeof b.content === "string" && b.content.trim()) detail = normalizeMessageText(b.content);
          else if (b.content && typeof b.content === "object") detail = safeJsonStringify(b.content);
          parts.push(`[${label}]${detail ? " " + detail : ""}`);
        } else if (b.type === "thinking" && typeof b.thinking === "string" && b.thinking.trim()) {
          parts.push(normalizeMessageText(b.thinking));
        }
      }
    }
    if (parts.length > 0) sections.push(parts.join("\n"));
    else if (rawContent.length > 0) sections.push(normalizeMessageText(safeJsonStringify(rawContent)));
  } else if (rawContent !== undefined && rawContent !== null && rawContent !== "") {
    sections.push(normalizeMessageText(safeJsonStringify(rawContent)));
  }

  const toolCallsRendered = formatToolCallsPayload(message.tool_calls);
  if (toolCallsRendered) {
    sections.push(`tool_calls\n${toolCallsRendered}`);
  }

  const functionCall = message.function_call;
  if (functionCall !== undefined) {
    sections.push(`function_call\n${safeJsonStringify(functionCall)}`);
  }

  if (sections.length > 0) return sections.join("\n\n");
  return "(no textual content)";
}
