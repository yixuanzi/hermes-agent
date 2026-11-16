import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import {
  ChatPage,
  extractSessionIdFromChatEvent,
  resolveInitialResumeSession,
} from "./ChatPage";

describe("ChatPage structure", () => {
  it("renders analyst workbench zones and key markers", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/chat?resume=session-42"]}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(html).toContain("chat-workbench");
    expect(html).toContain("chat-terminal-pane");
    expect(html).toContain("Terminal");
    expect(html).toContain("New");
  });

  it("resolves initial resume session with request-first precedence", () => {
    expect(resolveInitialResumeSession("sess-requested", "sess-cached")).toBe("sess-requested");
    expect(resolveInitialResumeSession("   ", "sess-cached")).toBe("sess-cached");
    expect(resolveInitialResumeSession("   ", "   ")).toBeNull();
  });

  it("extracts session_id from direct or nested chat event payloads", () => {
    expect(
      extractSessionIdFromChatEvent(
        '{"jsonrpc":"2.0","method":"event","params":{"type":"session.info","session_id":"sid-ui","payload":{"session_id":"sid-real"}}}',
      ),
    ).toBe("sid-real");
    expect(extractSessionIdFromChatEvent('{"session_id":"sid-direct"}')).toBe("sid-direct");
    expect(extractSessionIdFromChatEvent('{"payload":{"session_id":"sid-nested"}}')).toBe(
      "sid-nested",
    );
    expect(extractSessionIdFromChatEvent("{bad-json")).toBe("");
  });
});
