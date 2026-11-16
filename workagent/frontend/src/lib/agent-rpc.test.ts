// workagent/frontend/src/lib/agent-rpc.test.ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentRpc } from "./agent-rpc";

// Minimal mock WebSocket
function createMockWs() {
  const ws = {
    send: vi.fn(),
    close: vi.fn(),
    readyState: 0,
    onopen: null as (() => void) | null,
    onmessage: null as ((ev: { data: string }) => void) | null,
    onerror: null as (() => void) | null,
    onclose: null as ((ev: { code: number }) => void) | null,
  };
  return ws;
}

describe("AgentRpc", () => {
  let rpc: AgentRpc;
  let mockWs: ReturnType<typeof createMockWs>;

  beforeEach(() => {
    rpc = new AgentRpc();
    mockWs = createMockWs();
    // Inject mock ws factory
    (rpc as any)._createWs = () => {
      const w = createMockWs();
      mockWs = w;
      return w as any;
    };
  });

  /** Helper: connect and resolve via gateway.ready */
  async function connectAndReady() {
    const promise = rpc.connect("ws://localhost/api/chat/ws?token=x");
    mockWs.readyState = 1; // WebSocket.OPEN
    mockWs.onopen!();
    mockWs.onmessage!({
      data: JSON.stringify({
        jsonrpc: "2.0",
        method: "event",
        params: { type: "gateway.ready", payload: {} },
      }),
    });
    await promise;
  }

  it("connects and resolves after gateway.ready", async () => {
    await connectAndReady();
    // Resolved without error — test passes
  });

  it("call() sends JSON-RPC and resolves with result", async () => {
    await connectAndReady();

    const callPromise = rpc.call("session.create", { cols: 80 });
    // Verify sent
    expect(mockWs.send).toHaveBeenCalled();
    const sent = JSON.parse(mockWs.send.mock.calls[0][0]);
    expect(sent.method).toBe("session.create");
    expect(sent.id).toBeTruthy();

    // Simulate response
    mockWs.onmessage!({
      data: JSON.stringify({
        jsonrpc: "2.0",
        id: sent.id,
        result: { session_id: "abc123" },
      }),
    });
    const result = await callPromise;
    expect(result.session_id).toBe("abc123");
  });

  it("routes events to subscribers via on()", async () => {
    const handler = vi.fn();
    rpc.on("tool.start", handler);

    await connectAndReady();

    mockWs.onmessage!({
      data: JSON.stringify({
        jsonrpc: "2.0",
        method: "event",
        params: {
          type: "tool.start",
          session_id: "abc",
          payload: { name: "git_diff" },
        },
      }),
    });
    expect(handler).toHaveBeenCalledWith(
      expect.objectContaining({ payload: { name: "git_diff" } }),
    );
  });

  it("unsubscribe via returned function", async () => {
    const handler = vi.fn();
    const unsub = rpc.on("tool.start", handler);
    unsub();

    await connectAndReady();

    mockWs.onmessage!({
      data: JSON.stringify({
        jsonrpc: "2.0",
        method: "event",
        params: { type: "tool.start", payload: { name: "x" } },
      }),
    });
    expect(handler).not.toHaveBeenCalled();
  });

  it("disconnect closes WebSocket", async () => {
    await connectAndReady();
    rpc.disconnect();
    expect(mockWs.close).toHaveBeenCalled();
  });
});
