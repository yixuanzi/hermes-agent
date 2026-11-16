/**
 * @vitest-environment jsdom
 *
 * Regression tests for messagesFromSessionDetail() reconstructing tool-call
 * ("main-tools") bubbles from a session's plain persisted transcript, so
 * they survive a refresh / re-opening an old conversation instead of
 * flattening to plain text (see aisoc/backend/services/session_service.py's
 * get_session_detail_with_messages, which now also returns tool_call_id).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../../lib/api";
import type { Message } from "../types";
import { AisocChatProvider, useChatRuntime } from "./chatRuntime";

vi.mock("../../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

const fetchJSONMock = vi.mocked(fetchJSON);

function createMemoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => (data.has(key) ? data.get(key)! : null),
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key);
    },
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  };
}

class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send() {}
  close() {}
}

let rootRef: Root | null = null;
let containerRef: HTMLElement | null = null;
let probeState: { messages: Message[] } = { messages: [] };

function Probe() {
  const runtime = useChatRuntime();
  probeState.messages = runtime.activeConversation?.messages || [];
  return null;
}

async function waitForAssert(assertion: () => void, timeoutMs = 2000): Promise<void> {
  const start = Date.now();
  while (true) {
    try {
      assertion();
      return;
    } catch (error) {
      if (Date.now() - start >= timeoutMs) throw error;
      await act(async () => {
        await Promise.resolve();
      });
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }
}

async function mount(): Promise<void> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <AisocChatProvider isChatVisible={true}>
        <Probe />
      </AisocChatProvider>,
    );
  });
  rootRef = root;
  containerRef = container;
}

function mockSessionsAndDetail(detailMessages: unknown[]) {
  fetchJSONMock.mockImplementation(async (path: string) => {
    if (path.startsWith("/api/sessions?")) {
      return {
        sessions: [{ id: "sess-1", title: "Existing session", started_at: 1, last_active: 1 }],
        total: 1,
      };
    }
    if (path.includes("/detail")) {
      return { session_id: "sess-1", messages: detailMessages };
    }
    throw new Error(`Unhandled fetchJSON path in test: ${path}`);
  });
}

describe("messagesFromSessionDetail tool-call reconstruction", () => {
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(window, "localStorage", { value: createMemoryStorage(), configurable: true });
    (globalThis as { WebSocket?: unknown }).WebSocket = FakeWebSocket;
    probeState = { messages: [] };
    fetchJSONMock.mockReset();
  });

  afterEach(() => {
    if (rootRef) {
      act(() => rootRef!.unmount());
      rootRef = null;
    }
    containerRef?.remove();
    containerRef = null;
  });

  it("leaves a plain text-only conversation unchanged", async () => {
    mockSessionsAndDetail([
      { role: "user", content: "hello", timestamp: 1 },
      { role: "assistant", content: "hi there", timestamp: 2 },
    ]);
    await mount();
    await waitForAssert(() => {
      expect(probeState.messages.map((m) => ({ kind: m.kind, sender: m.sender, text: m.text }))).toEqual([
        { kind: "chat", sender: "user", text: "hello" },
        { kind: "chat", sender: "assistant", text: "hi there" },
      ]);
    });
  });

  it("groups a single tool call into a main-tools bubble between the user turn and the reply", async () => {
    mockSessionsAndDetail([
      { role: "user", content: "run the scan", timestamp: 1 },
      {
        role: "tool",
        content: "scan complete, 0 findings",
        tool_name: "run_shell",
        tool_call_id: "call_abc",
        timestamp: 2,
      },
      { role: "assistant", content: "Scan finished with no findings.", timestamp: 3 },
    ]);
    await mount();
    await waitForAssert(() => {
      expect(probeState.messages.map((m) => m.kind)).toEqual(["chat", "main-tools", "chat"]);
      const toolMessage = probeState.messages[1];
      expect(toolMessage.chainSteps).toEqual([
        {
          id: "call_abc",
          agentName: "run_shell",
          type: "vip_tool",
          status: "Completed",
          message: "scan complete, 0 findings",
          timestamp: expect.any(String),
        },
      ]);
    });
  });

  it("groups multiple rounds of tool calls within one turn into a single bubble, in order", async () => {
    mockSessionsAndDetail([
      { role: "user", content: "investigate host X", timestamp: 1 },
      { role: "tool", content: "host X: 3 open ports", tool_name: "port_scan", tool_call_id: "call_1", timestamp: 2 },
      { role: "tool", content: "no known CVEs", tool_name: "cve_lookup", tool_call_id: "call_2", timestamp: 3 },
      { role: "assistant", content: "Host X looks clean.", timestamp: 4 },
    ]);
    await mount();
    await waitForAssert(() => {
      expect(probeState.messages.map((m) => m.kind)).toEqual(["chat", "main-tools", "chat"]);
      const toolMessage = probeState.messages[1];
      expect(toolMessage.chainSteps?.map((s) => s.id)).toEqual(["call_1", "call_2"]);
      expect(toolMessage.chainSteps?.map((s) => s.agentName)).toEqual(["port_scan", "cve_lookup"]);
    });
  });

  it("flushes a trailing tool-call group with no final reply yet", async () => {
    mockSessionsAndDetail([
      { role: "user", content: "keep digging", timestamp: 1 },
      { role: "tool", content: "still running...", tool_name: "long_task", tool_call_id: "call_x", timestamp: 2 },
    ]);
    await mount();
    await waitForAssert(() => {
      expect(probeState.messages.map((m) => m.kind)).toEqual(["chat", "main-tools"]);
      expect(probeState.messages[1].chainSteps?.[0].agentName).toBe("long_task");
    });
  });

  it("falls back to a synthetic step id and placeholder text when tool_call_id/content are missing", async () => {
    mockSessionsAndDetail([
      { role: "user", content: "do it", timestamp: 1 },
      { role: "tool", content: "", tool_name: "noop_tool", tool_call_id: null, timestamp: 2 },
      { role: "assistant", content: "Done.", timestamp: 3 },
    ]);
    await mount();
    await waitForAssert(() => {
      const toolMessage = probeState.messages[1];
      expect(toolMessage.chainSteps?.[0].id).toBe("history-tools-sess-1-1");
      expect(toolMessage.chainSteps?.[0].message).toBe("(no output)");
    });
  });

  it("reconstructs modifiedFiles on the reply from a successful write_file result", async () => {
    mockSessionsAndDetail([
      { role: "user", content: "make me a landing page", timestamp: 1 },
      {
        role: "tool",
        content: JSON.stringify({ success: true, files_modified: ["aisoc-introduction.html"] }),
        tool_name: "write_file",
        tool_call_id: "call_write",
        timestamp: 2,
      },
      { role: "assistant", content: "Here's your landing page.", timestamp: 3 },
    ]);
    await mount();
    await waitForAssert(() => {
      const reply = probeState.messages.find((m) => m.kind === "chat" && m.sender === "assistant");
      expect(reply?.modifiedFiles).toEqual(["aisoc-introduction.html"]);
    });
  });

  it("does not attach modifiedFiles when the tool result reports an error", async () => {
    mockSessionsAndDetail([
      { role: "user", content: "make me a landing page", timestamp: 1 },
      {
        role: "tool",
        content: JSON.stringify({ success: false, error: "disk full" }),
        tool_name: "write_file",
        tool_call_id: "call_write",
        timestamp: 2,
      },
      { role: "assistant", content: "That failed.", timestamp: 3 },
    ]);
    await mount();
    await waitForAssert(() => {
      const reply = probeState.messages.find((m) => m.kind === "chat" && m.sender === "assistant");
      expect(reply?.modifiedFiles).toBeUndefined();
    });
  });

  it("does not attach modifiedFiles for tools unrelated to file mutation", async () => {
    mockSessionsAndDetail([
      { role: "user", content: "what time is it", timestamp: 1 },
      {
        role: "tool",
        content: JSON.stringify({ files_modified: ["should-not-count.html"] }),
        tool_name: "read_file",
        tool_call_id: "call_read",
        timestamp: 2,
      },
      { role: "assistant", content: "It's 6pm.", timestamp: 3 },
    ]);
    await mount();
    await waitForAssert(() => {
      const reply = probeState.messages.find((m) => m.kind === "chat" && m.sender === "assistant");
      expect(reply?.modifiedFiles).toBeUndefined();
    });
  });
});
