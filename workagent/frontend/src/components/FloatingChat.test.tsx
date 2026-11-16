// workagent/frontend/src/components/FloatingChat.test.tsx
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// Mock the hook to avoid WebSocket side effects during SSR tests
const mockSwitchToTab = vi.fn();
const mockCloseTab = vi.fn();

vi.mock("../lib/useAgentChat", () => ({
  useAgentChat: () => ({
    state: {
      phase: "disconnected",
      sessionId: null,
      messages: [],
      activeApproval: null,
      activeClarify: null,
      error: null,
    },
    tabs: [
      { dbId: "db-1", tuiId: "tui-1", title: "Chat 1", messages: [] },
      { dbId: "db-2", tuiId: "tui-2", title: "Chat 2", messages: [] },
    ],
    activeTabDbId: "db-1",
    send: vi.fn(),
    respondApproval: vi.fn(),
    respondClarify: vi.fn(),
    startNewSession: vi.fn(),
    switchToTab: mockSwitchToTab,
    closeTab: mockCloseTab,
    connect: vi.fn(),
    disconnect: vi.fn(),
    interrupt: vi.fn(),
  }),
  formatToolDuration: (s: number) => `${s.toFixed(1)}s`,
}));

import { FloatingChat } from "./FloatingChat";

describe("FloatingChat structure", () => {
  it("renders the floating icon with orbit elements", () => {
    const html = renderToStaticMarkup(<FloatingChat />);
    expect(html).toContain("widget-icon");
    expect(html).toContain("widget-orbit-inner");
    expect(html).toContain("widget-orbit-outer");
  });

  it("renders drawer (hidden via CSS translateX)", () => {
    const html = renderToStaticMarkup(<FloatingChat />);
    expect(html).toContain("widget-drawer");
    expect(html).toContain("widget-textarea");
  });

  it("renders send button", () => {
    const html = renderToStaticMarkup(<FloatingChat />);
    expect(html).toContain("widget-btn-send");
  });

  it("renders tab bar with tab buttons", () => {
    const html = renderToStaticMarkup(<FloatingChat />);
    expect(html).toContain("widget-tab-bar");
    expect(html).toContain("widget-tab-title");
  });

  it("renders active tab with active class", () => {
    const html = renderToStaticMarkup(<FloatingChat />);
    expect(html).toContain("widget-tab-active");
  });

  it("renders tab close buttons when multiple tabs exist", () => {
    const html = renderToStaticMarkup(<FloatingChat />);
    expect(html).toContain("widget-tab-close");
  });
});
