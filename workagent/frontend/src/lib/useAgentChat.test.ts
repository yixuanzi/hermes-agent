/**
 * @vitest-environment jsdom
 */

import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WIDGET_SESSION_KEY } from "./useAgentChat";

// Mock agent-rpc before importing the hook module
const mockCall = vi.fn();
const mockOn = vi.fn(() => () => {});
const mockDisconnect = vi.fn();

vi.mock("./agent-rpc", () => ({
  AgentRpc: vi.fn().mockImplementation(() => ({
    connect: vi.fn().mockResolvedValue(undefined),
    call: mockCall,
    on: mockOn,
    disconnect: mockDisconnect,
  })),
}));

vi.mock("./auth", () => ({
  getStoredToken: vi.fn(() => "test-token"),
}));

import { useAgentChat } from "./useAgentChat";

const TABS_KEY = "workagent.widget.tabs";
const ACTIVE_TAB_KEY = "workagent.widget.activeTabDbId";
const LEGACY_DB_KEY = "workagent.widget.dbSessionId";
const LEGACY_MSG_KEY = "workagent.widget.messages";

function renderChatHook() {
  return renderHook(() => useAgentChat());
}

describe("useAgentChat", () => {
  beforeEach(() => {
    localStorage.clear();
    mockCall.mockReset();
    mockOn.mockReset();
    mockOn.mockReturnValue(() => {});
    mockDisconnect.mockReset();
  });

  it("exports the session storage key constant", () => {
    expect(WIDGET_SESSION_KEY).toBe("workagent.widget.sessionId");
  });

  it("starts disconnected when no tabs exist", () => {
    const { result } = renderChatHook();
    expect(result.current.state.phase).toBe("disconnected");
    expect(result.current.tabs).toEqual([]);
  });

  describe("migration", () => {
    it("migrates legacy single-session keys to multi-tab format", () => {
      localStorage.setItem(LEGACY_DB_KEY, "db-legacy-001");
      localStorage.setItem(WIDGET_SESSION_KEY, "tui-legacy");
      localStorage.setItem(LEGACY_MSG_KEY, JSON.stringify([
        { role: "user", id: "m-1", text: "Hello world from legacy" },
      ]));

      const { result } = renderChatHook();

      // Legacy keys should be cleaned up
      expect(localStorage.getItem(WIDGET_SESSION_KEY)).toBeNull();
      expect(localStorage.getItem(LEGACY_DB_KEY)).toBeNull();
      expect(localStorage.getItem(LEGACY_MSG_KEY)).toBeNull();

      // Tab should be created
      const tabs = JSON.parse(localStorage.getItem(TABS_KEY) || "[]");
      expect(tabs).toHaveLength(1);
      expect(tabs[0].dbId).toBe("db-legacy-001");
      expect(tabs[0].title).toBe("Hello world from legacy");
      expect(localStorage.getItem(ACTIVE_TAB_KEY)).toBe("db-legacy-001");

      // State should be connecting (has initial tab)
      expect(result.current.state.phase).toBe("connecting");
    });

    it("skips migration when tabs key already exists", () => {
      const existingTabs = [{ dbId: "db-existing", tuiId: "tui-1", title: "Chat", messages: [] }];
      localStorage.setItem(TABS_KEY, JSON.stringify(existingTabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-existing");
      // Legacy keys should NOT be touched
      localStorage.setItem(LEGACY_DB_KEY, "db-old");

      renderChatHook();

      expect(localStorage.getItem(LEGACY_DB_KEY)).toBe("db-old");
    });
  });

  describe("connect()", () => {
    it("creates new session when no cached DB session ID", async () => {
      mockCall
        .mockResolvedValueOnce({ session_id: "tui-new", info: {} })
        .mockResolvedValueOnce({ title: "New Chat", session_key: "20260530_120000_abc123" });

      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => {
        expect(mockCall).toHaveBeenCalledWith("session.create", expect.any(Object));
        expect(result.current.state.sessionId).toBe("tui-new");
      });
    });

    it("resumes using active tab DB ID", async () => {
      const tabs = [{ dbId: "db-session-001", tuiId: "tui-old", title: "Chat", messages: [] }];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-session-001");

      mockCall
        .mockResolvedValueOnce({
          session_id: "tui-789",
          resumed: "db-session-001",
          messages: [
            { role: "user", text: "hello" },
            { role: "assistant", text: "hi" },
          ],
          info: {},
        })
        .mockResolvedValueOnce({ status: "ok" });

      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => {
        expect(result.current.state.phase).toBe("idle");
        expect(result.current.state.sessionId).toBe("tui-789");
      });

      expect(mockCall).toHaveBeenCalledWith("session.resume", expect.objectContaining({
        session_id: "db-session-001",
      }));
    });

    it("falls back to create on resume error", async () => {
      const tabs = [{ dbId: "expired-999", tuiId: null, title: "Chat", messages: [] }];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "expired-999");

      mockCall
        .mockRejectedValueOnce({ code: 4007, message: "session not found" })
        .mockResolvedValueOnce({ session_id: "fresh-111", info: {} })
        .mockResolvedValueOnce({ title: "", session_key: "new-db-id" });

      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => {
        expect(mockCall).toHaveBeenCalledWith("session.create", expect.any(Object));
      });
    });

    it("startNewSession skips resume and creates fresh", async () => {
      const tabs = [{ dbId: "db-old", tuiId: "tui-old", title: "Old Chat", messages: [] }];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-old");

      mockCall
        .mockResolvedValueOnce({ session_id: "tui-old-resumed", resumed: "db-old", messages: [], info: {} });

      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => {
        expect(result.current.state.phase).toBe("idle");
      });

      mockCall.mockReset();
      mockCall
        .mockResolvedValueOnce({ session_id: "tui-new", info: {} })
        .mockResolvedValueOnce({ title: "", session_key: "db-new" });

      act(() => result.current.startNewSession());

      await vi.waitFor(() => {
        expect(result.current.state.phase).toBe("idle");
        expect(result.current.state.sessionId).toBe("tui-new");
        expect(mockCall).toHaveBeenCalledWith("session.create", expect.any(Object));
      });
    });

    it("retries once on connection failure", async () => {
      const { AgentRpc } = await import("./agent-rpc");
      let callCount = 0;
      (AgentRpc as any).mockImplementation(() => ({
        connect: vi.fn().mockImplementation(() => {
          callCount++;
          if (callCount === 1) return Promise.reject(new Error("Connection failed"));
          return Promise.resolve();
        }),
        call: mockCall
          .mockResolvedValueOnce({ session_id: "retry-ok", info: {} })
          .mockResolvedValueOnce({ title: "", session_key: "db-retry" }),
        on: mockOn,
        disconnect: mockDisconnect,
      }));

      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => {
        expect(callCount).toBeGreaterThanOrEqual(2);
      }, { timeout: 4000 });
    });
  });

  describe("tab management", () => {
    it("startNewSession adds a new tab to the list", async () => {
      const tabs = [{ dbId: "db-1", tuiId: "tui-1", title: "First", messages: [] }];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-1");

      mockCall.mockResolvedValueOnce({ session_id: "tui-1", resumed: "db-1", messages: [], info: {} });
      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => expect(result.current.state.phase).toBe("idle"));

      mockCall.mockReset();
      mockCall
        .mockResolvedValueOnce({ session_id: "tui-2", info: {} })
        .mockResolvedValueOnce({ title: "", session_key: "db-2" });

      act(() => result.current.startNewSession());

      await vi.waitFor(() => {
        expect(result.current.tabs.length).toBe(2);
        expect(result.current.tabs[1].tuiId).toBe("tui-2");
      });
    });

    it("switchToTab loads cached messages lazily (no reconnect)", async () => {
      const tabs = [
        { dbId: "db-1", tuiId: "tui-1", title: "Chat 1", messages: [{ role: "user", id: "m-1", text: "hi" }] },
        { dbId: "db-2", tuiId: "tui-2", title: "Chat 2", messages: [{ role: "user", id: "m-2", text: "hello" }] },
      ];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-1");

      mockCall.mockResolvedValueOnce({ session_id: "tui-1", resumed: "db-1", messages: [], info: {} });
      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => expect(result.current.state.phase).toBe("idle"));

      mockCall.mockReset();

      // Switch tab — should NOT trigger any RPC call
      act(() => result.current.switchToTab("db-2"));

      expect(result.current.activeTabDbId).toBe("db-2");
      // Cached messages loaded immediately, no reconnect
      expect(result.current.state.messages.some(m => m.role === "user" && m.text === "hello")).toBe(true);
      expect(result.current.state.phase).toBe("idle");
      expect(mockCall).not.toHaveBeenCalled();
    });

    it("send triggers reconnect after tab switch", async () => {
      const tabs = [
        { dbId: "db-1", tuiId: "tui-1", title: "Chat 1", messages: [] },
        { dbId: "db-2", tuiId: "tui-2", title: "Chat 2", messages: [] },
      ];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-1");

      mockCall
        .mockResolvedValueOnce({ session_id: "tui-1", resumed: "db-1", messages: [], info: {} })
        .mockResolvedValueOnce({ title: "", session_key: "db-1" });
      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => expect(result.current.state.phase).toBe("idle"));

      // Clear all mock state from initial connect + persistDbId
      mockCall.mockReset();

      // Switch tab (lazy — no reconnect)
      act(() => result.current.switchToTab("db-2"));
      expect(mockCall).not.toHaveBeenCalled();

      // Now send — should trigger reconnect + prompt.submit
      mockCall
        .mockResolvedValueOnce({ session_id: "tui-2r", resumed: "db-2", messages: [], info: {} })
        .mockResolvedValueOnce({ status: "streaming" });

      act(() => result.current.send("test message"));

      await vi.waitFor(() => {
        expect(mockCall).toHaveBeenCalledWith("session.resume", expect.objectContaining({
          session_id: "db-2",
        }));
      }, { timeout: 3000 });
    });

    it("switchToTab on already active tab is a no-op", async () => {
      const tabs = [
        { dbId: "db-1", tuiId: "tui-1", title: "Chat 1", messages: [] },
        { dbId: "db-2", tuiId: "tui-2", title: "Chat 2", messages: [] },
      ];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-1");

      mockCall.mockResolvedValueOnce({ session_id: "tui-1", resumed: "db-1", messages: [], info: {} });
      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => expect(result.current.state.phase).toBe("idle"));

      mockCall.mockReset();
      act(() => result.current.switchToTab("db-1"));

      // No disconnect or reconnect should happen
      expect(mockDisconnect).not.toHaveBeenCalled();
      expect(mockCall).not.toHaveBeenCalled();
    });

    it("closeTab removes tab from list", async () => {
      const tabs = [
        { dbId: "db-1", tuiId: "tui-1", title: "Chat 1", messages: [] },
        { dbId: "db-2", tuiId: "tui-2", title: "Chat 2", messages: [] },
      ];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-1");

      mockCall.mockResolvedValueOnce({ session_id: "tui-1", resumed: "db-1", messages: [], info: {} });
      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => expect(result.current.state.phase).toBe("idle"));

      // Close inactive tab db-2
      act(() => result.current.closeTab("db-2"));

      expect(result.current.tabs).toHaveLength(1);
      expect(result.current.tabs[0].dbId).toBe("db-1");
    });

    it("closeTab on active tab switches to remaining tab", async () => {
      const tabs = [
        { dbId: "db-1", tuiId: "tui-1", title: "Chat 1", messages: [] },
        { dbId: "db-2", tuiId: "tui-2", title: "Chat 2", messages: [] },
      ];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-1");

      mockCall.mockResolvedValueOnce({ session_id: "tui-1", resumed: "db-1", messages: [], info: {} });
      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => expect(result.current.state.phase).toBe("idle"));

      mockCall.mockReset();
      mockCall.mockResolvedValueOnce({ session_id: "tui-2r", resumed: "db-2", messages: [], info: {} });

      act(() => result.current.closeTab("db-1"));

      expect(result.current.tabs).toHaveLength(1);
      expect(result.current.tabs[0].dbId).toBe("db-2");
    });
  });

  describe("persistence", () => {
    it("persists tabs to localStorage on change", async () => {
      mockCall
        .mockResolvedValueOnce({ session_id: "tui-1", info: {} })
        .mockResolvedValueOnce({ title: "", session_key: "db-persist" });

      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => {
        const stored = JSON.parse(localStorage.getItem(TABS_KEY) || "[]");
        expect(stored.length).toBeGreaterThanOrEqual(1);
        expect(stored[0].dbId).toBe("db-persist");
      });
    });

    it("persists activeTabDbId to localStorage", async () => {
      mockCall
        .mockResolvedValueOnce({ session_id: "tui-1", info: {} })
        .mockResolvedValueOnce({ title: "", session_key: "db-act" });

      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => {
        expect(localStorage.getItem(ACTIVE_TAB_KEY)).toBe("db-act");
      });
    });

    it("reloads tabs from localStorage on mount", () => {
      const tabs = [
        { dbId: "db-r1", tuiId: "tui-r1", title: "Restored 1", messages: [] },
        { dbId: "db-r2", tuiId: "tui-r2", title: "Restored 2", messages: [] },
      ];
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
      localStorage.setItem(ACTIVE_TAB_KEY, "db-r1");

      const { result } = renderChatHook();
      expect(result.current.tabs).toHaveLength(2);
      expect(result.current.activeTabDbId).toBe("db-r1");
    });
  });

  describe("send()", () => {
    it("calls prompt.submit with text", async () => {
      mockCall
        .mockResolvedValueOnce({ session_id: "sess-1", info: {} })
        .mockResolvedValueOnce({ title: "", session_key: "db-1" })
        .mockResolvedValueOnce({ status: "streaming" });

      const { result } = renderChatHook();
      act(() => result.current.connect());

      await vi.waitFor(() => {
        expect(result.current.state.phase).toBe("idle");
      });

      act(() => result.current.send("hello agent"));
      expect(mockCall).toHaveBeenCalledWith("prompt.submit", expect.objectContaining({
        text: "hello agent",
      }));
    });
  });
});
