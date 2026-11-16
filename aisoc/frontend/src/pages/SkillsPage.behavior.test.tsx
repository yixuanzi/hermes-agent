/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../lib/api";
import { SkillsPage } from "./SkillsPage";

vi.mock("../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

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

let rootRef: Root | null = null;
let containerRef: HTMLElement | null = null;
const fetchJSONMock = vi.mocked(fetchJSON);

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
});

afterEach(async () => {
  if (rootRef && containerRef) {
    await act(async () => {
      rootRef?.unmount();
    });
    containerRef.remove();
  }
  rootRef = null;
  containerRef = null;
});

async function mountSkillsPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/skills"]}>
        <SkillsPage />
      </MemoryRouter>,
    );
  });

  rootRef = root;
  containerRef = container;
}

describe("SkillsPage behavior", () => {
  it("loads detail and appendix content from the new skills APIs", async () => {
    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/skills") {
        return Promise.resolve([
          { name: "s1", description: "skill one", enabled: true, category: "", path: "/tmp/s1" },
          { name: "s2", description: "skill two", enabled: false, category: "devops", path: "/tmp/s2" },
        ]) as Promise<unknown>;
      }
      if (url === "/api/skills/s1") {
        return Promise.resolve({
          name: "s1",
          path: "/tmp/s1",
          content: "# S1",
          appendix: [{ name: "a.md", path: "references/a.md" }],
        }) as Promise<unknown>;
      }
      if (url === "/api/skills/s1/appendix?path=references%2Fa.md") {
        return Promise.resolve({
          name: "a.md",
          path: "references/a.md",
          content: "appendix-body",
        }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountSkillsPage();

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("misc");
      expect(text).toContain("devops");
      expect(text).toContain("# S1");
      expect(text).toContain("references/a.md");
    });

    const appendixButton = Array.from(
      (containerRef as HTMLElement).querySelectorAll<HTMLButtonElement>("button"),
    ).find((button) => button.textContent?.includes("references/a.md"));
    expect(appendixButton).not.toBeNull();

    await act(async () => {
      appendixButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("appendix-body");
      expect(text).toContain("Appendix: references/a.md");
    });
  });

  it("does not show success when toggle succeeds but refresh fails", async () => {
    let skillsLoadCount = 0;

    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/skills") {
        skillsLoadCount += 1;
        if (skillsLoadCount === 1) {
          return Promise.resolve([
            { name: "threat-hunt", description: "Threat Hunt", enabled: false, category: "", path: "/tmp" },
          ]) as Promise<unknown>;
        }
        return Promise.reject(new Error("refresh failed")) as Promise<unknown>;
      }
      if (url === "/api/skills/threat-hunt") {
        return Promise.resolve({
          name: "threat-hunt",
          path: "/tmp",
          content: "# threat-hunt",
          appendix: [],
        }) as Promise<unknown>;
      }
      if (url === "/api/skills/toggle") {
        return Promise.resolve({ ok: true }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountSkillsPage();

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("threat-hunt");
    });

    const miscToggle = (containerRef as HTMLElement).querySelector(
      'button[aria-label="Toggle misc category"]',
    ) as HTMLButtonElement | null;
    expect(miscToggle).not.toBeNull();

    await act(async () => {
      miscToggle?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const toggleButton = (containerRef as HTMLElement).querySelector(
      ".skills-mini-toggle",
    ) as HTMLButtonElement | null;
    expect(toggleButton).not.toBeNull();

    await act(async () => {
      toggleButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Operation Failed");
      expect(text).toContain("Updated threat-hunt, but failed to refresh skills from /api/skills.");
      expect(text).not.toContain("Operation Completed");
      expect(text).not.toContain("threat-hunt enabled successfully.");
    });
  });
});
