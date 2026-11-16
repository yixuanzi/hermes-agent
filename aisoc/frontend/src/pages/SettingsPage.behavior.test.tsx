/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../lib/api";
import { setStoredAuth } from "../lib/auth";
import { CurrentUserProvider } from "../lib/authContext";
import type { AuthenticatedUser } from "../types";
import { SettingsPage } from "./SettingsPage";

vi.mock("../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

const ADMIN_USER: AuthenticatedUser = {
  uid: "0000000000000001",
  username: "admin",
  display_name: "admin",
  email: "admin@aisoc.local",
  status: "enabled",
  create_time: "2026-01-01T00:00:00Z",
  last_login: null,
  is_admin: true,
};

function seedAdminAuth(): void {
  setStoredAuth("test-token", ADMIN_USER);
}

async function mountWithAdmin(ui: React.ReactNode): Promise<HTMLElement> {
  seedAdminAuth();
  return mount(<CurrentUserProvider>{ui}</CurrentUserProvider>);
}

const RUNTIME_STATUS = {
  status: "ok",
  model: "test-model",
  provider: "test-provider",
  profile: "default",
  uptime_seconds: 90,
  last_activity: null,
};

let rootRef: Root | null = null;
let containerRef: HTMLElement | null = null;
const fetchJSONMock = vi.mocked(fetchJSON);

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

async function mount(ui: React.ReactNode): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(ui);
  });
  rootRef = root;
  containerRef = container;
  return container;
}

function findButton(container: HTMLElement, name: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
    (candidate) => candidate.textContent?.trim() === name,
  );
  if (!button) throw new Error(`Button not found: ${name}`);
  return button;
}

async function click(element: HTMLElement): Promise<void> {
  await act(async () => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
  });
}

async function pressKey(element: HTMLElement, key: string, shiftKey = false): Promise<void> {
  await act(async () => {
    element.dispatchEvent(new KeyboardEvent("keydown", { key, shiftKey, bubbles: true }));
    await Promise.resolve();
  });
}

async function enterPhrase(container: HTMLElement, phrase: string): Promise<HTMLInputElement> {
  const input = container.querySelector<HTMLInputElement>('input[aria-label="Restart confirmation phrase"]');
  if (!input) throw new Error("Restart confirmation input not found");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter?.call(input, phrase);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return input;
}

async function openPhraseStage(container: HTMLElement): Promise<void> {
  await click(findButton(container, "Restart AISOC"));
  await click(findButton(container, "Continue"));
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  localStorage.clear();
  fetchJSONMock.mockImplementation((path: string) => {
    if (path === "/api/overview/status") return Promise.resolve(RUNTIME_STATUS) as Promise<unknown>;
    throw new Error(`Unexpected request: ${path}`);
  });
});

afterEach(async () => {
  vi.useRealTimers();
  if (rootRef && containerRef) {
    await act(async () => {
      rootRef?.unmount();
    });
    containerRef.remove();
  }
  rootRef = null;
  containerRef = null;
});

describe("Settings route", () => {
  it("renders the Settings page through the authenticated app route", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    seedAdminAuth();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ authenticated: true, user: ADMIN_USER, expires_in: 28800 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const { App } = await import("../App");

    const container = await mount(
      <MemoryRouter initialEntries={["/settings"]}>
        <App />
      </MemoryRouter>,
    );

    await waitForAssert(() => {
      expect(container.textContent).toContain("Runtime Status");
      expect(container.textContent).toContain("Dangerous Actions");
    });
  });
});

describe("Settings restart flow", () => {
  it("requires both confirmation stages and the exact phrase", async () => {
    const container = await mountWithAdmin(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    await click(findButton(container, "Restart AISOC"));
    expect(container.querySelector('[role="dialog"]')?.textContent).toContain("Service interruption");
    expect(fetchJSONMock).not.toHaveBeenCalledWith("/api/system/restart", expect.anything());

    await click(findButton(container, "Continue"));
    const confirm = findButton(container, "Confirm restart");
    expect(confirm.disabled).toBe(true);

    await enterPhrase(container, "RESTART AISOC ");
    expect(confirm.disabled).toBe(true);
    expect(fetchJSONMock).not.toHaveBeenCalledWith("/api/system/restart", expect.anything());

    await enterPhrase(container, "RESTART AISOC");
    expect(confirm.disabled).toBe(false);
  });

  it("reloads when an all-success health sequence reports a new process pid", async () => {
    vi.useFakeTimers();
    const reloadPage = vi.fn();
    let healthAttempts = 0;
    fetchJSONMock.mockImplementation((path: string) => {
      if (path === "/api/overview/status") return Promise.resolve(RUNTIME_STATUS) as Promise<unknown>;
      if (path === "/api/system/restart") {
        return Promise.resolve({
          accepted: true,
          already_requested: false,
          service: "aisoc",
          pid: 123,
        }) as Promise<unknown>;
      }
      if (path === "/health") {
        healthAttempts += 1;
        return Promise.resolve({ status: "ok", pid: healthAttempts === 1 ? 123 : 456 }) as Promise<unknown>;
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    const container = await mountWithAdmin(
      <MemoryRouter>
        <SettingsPage reloadPage={reloadPage} recoveryPollMs={100} />
      </MemoryRouter>,
    );
    await openPhraseStage(container);
    await enterPhrase(container, "RESTART AISOC");
    await click(findButton(container, "Confirm restart"));

    await waitForAssert(() => {
      expect(container.textContent).toContain("Restarting AISOC");
      expect(findButton(container, "Restarting…").disabled).toBe(true);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(fetchJSONMock).toHaveBeenCalledWith("/health", {}, false);
    expect(reloadPage).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  it("falls back to downtime then recovery when health has no pid", async () => {
    vi.useFakeTimers();
    const reloadPage = vi.fn();
    let healthAttempts = 0;
    fetchJSONMock.mockImplementation((path: string) => {
      if (path === "/api/overview/status") return Promise.resolve(RUNTIME_STATUS) as Promise<unknown>;
      if (path === "/api/system/restart") {
        return Promise.resolve({
          accepted: true,
          already_requested: false,
          service: "aisoc",
          pid: 123,
        }) as Promise<unknown>;
      }
      if (path === "/health") {
        healthAttempts += 1;
        if (healthAttempts === 1) return Promise.reject(new TypeError("service down")) as Promise<unknown>;
        return Promise.resolve({ status: "ok" }) as Promise<unknown>;
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    const container = await mountWithAdmin(
      <MemoryRouter>
        <SettingsPage reloadPage={reloadPage} recoveryPollMs={100} />
      </MemoryRouter>,
    );
    await openPhraseStage(container);
    await enterPhrase(container, "RESTART AISOC");
    await click(findButton(container, "Confirm restart"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(reloadPage).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  it("manages dialog focus, traps tab navigation, and restores trigger focus on Escape", async () => {
    const container = await mountWithAdmin(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );
    const trigger = findButton(container, "Restart AISOC");
    trigger.focus();

    await click(trigger);
    const firstCancel = findButton(container, "Cancel");
    expect(document.activeElement).toBe(firstCancel);

    await click(findButton(container, "Continue"));
    const input = container.querySelector<HTMLInputElement>('input[aria-label="Restart confirmation phrase"]');
    if (!input) throw new Error("Restart confirmation input not found");
    expect(document.activeElement).toBe(input);

    const cancel = findButton(container, "Cancel");
    cancel.focus();
    await pressKey(cancel, "Tab");
    expect(document.activeElement).toBe(input);

    await pressKey(input, "Tab", true);
    expect(document.activeElement).toBe(cancel);

    await pressKey(cancel, "Escape");
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
