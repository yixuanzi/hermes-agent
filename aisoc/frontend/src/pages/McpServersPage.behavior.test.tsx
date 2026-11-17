/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../lib/api";
import { setStoredAuth } from "../lib/auth";
import { CurrentUserProvider } from "../lib/authContext";
import type { AuthenticatedUser } from "../types";
import { McpServersPage } from "./McpServersPage";

vi.mock("../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

const fetchJSONMock = vi.mocked(fetchJSON);

const SERVER = {
  name: "dify",
  transport: "http",
  url: "https://api.dify.ai/***",
  command: null,
  args: [],
  env: {},
  auth: null,
  enabled: true,
  tools: null,
};

function user(isAdmin: boolean): AuthenticatedUser {
  return {
    uid: isAdmin ? "0000000000000001" : "0000000000000002",
    username: isAdmin ? "admin" : "analyst",
    display_name: isAdmin ? "admin" : "analyst",
    email: "user@aisoc.local",
    status: "enabled",
    create_time: "2026-01-01T00:00:00Z",
    last_login: null,
    is_admin: isAdmin,
  };
}

let root: Root | null = null;
let container: HTMLElement | null = null;

async function renderAs(isAdmin: boolean): Promise<HTMLElement> {
  setStoredAuth("test-token", user(isAdmin));
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(
      <CurrentUserProvider>
        <McpServersPage />
      </CurrentUserProvider>,
    );
  });
  await act(async () => {
    await Promise.resolve();
  });
  return container;
}

function buttonLabels(scope: HTMLElement): string[] {
  return Array.from(scope.querySelectorAll("button")).map((button) => button.textContent?.trim() || "");
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  localStorage.clear();
  fetchJSONMock.mockImplementation((path: string) => {
    if (path === "/api/mcp-servers") return Promise.resolve([SERVER]) as Promise<unknown>;
    throw new Error(`Unexpected request: ${path}`);
  });
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  container?.remove();
  root = null;
  container = null;
});

describe("MCP servers page", () => {
  it("is read-only for non-admin users", async () => {
    const page = await renderAs(false);

    expect(page.textContent).toContain("dify");
    const labels = buttonLabels(page);
    for (const action of ["新增服务器", "重载", "编辑", "删除", "停用"]) {
      expect(labels).not.toContain(action);
    }
    expect(labels).toContain("查看");
  });

  it("offers management actions to admins", async () => {
    const labels = buttonLabels(await renderAs(true));

    for (const action of ["新增服务器", "重载", "编辑", "删除", "停用"]) {
      expect(labels).toContain(action);
    }
  });
});
