/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, fetchJSON } from "../lib/api";
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
  vi.restoreAllMocks();
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
  it("shows disabled counts for the inventory, categories, and search results", async () => {
    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/skills") {
        return Promise.resolve([
          { name: "enabled-skill", description: "enabled", enabled: true, category: "ops", path: "/tmp/enabled" },
          { name: "disabled-one", description: "disabled one", enabled: false, category: "ops", path: "/tmp/one" },
          { name: "disabled-two", description: "disabled two", enabled: false, category: "docs", path: "/tmp/two" },
          { name: "always-enabled", description: "always enabled", enabled: true, category: "qa", path: "/tmp/qa" },
        ]) as Promise<unknown>;
      }
      if (url === "/api/skills/enabled-skill") {
        return Promise.resolve({
          name: "enabled-skill",
          path: "/tmp/enabled",
          content: "# enabled-skill",
          appendix: [],
        }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountSkillsPage();

    await waitForAssert(() => {
      const countLabels = Array.from(
        (containerRef as HTMLElement).querySelectorAll<HTMLElement>(".skills-disabled-count"),
      ).map((node) => node.textContent);
      expect(countLabels).toContain("2 disabled");
      expect(countLabels).toContain("0 disabled");
      expect(countLabels).toContain("1 disabled");
    });

    const searchInput = (containerRef as HTMLElement).querySelector(
      'input[aria-label="Search skills"]',
    ) as HTMLInputElement | null;
    expect(searchInput).not.toBeNull();

    await act(async () => {
      if (!searchInput) return;
      const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      valueSetter?.call(searchInput, "enabled-skill");
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
      searchInput.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).querySelector(".skills-counts")?.textContent).toContain("0 disabled");
    });
  });

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

  it("bulk-toggles a whole category via /api/skills/toggle-category", async () => {
    let toggleCategoryCalls = 0;
    let skillsLoadCount = 0;

    fetchJSONMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/skills") {
        skillsLoadCount += 1;
        const enabled = skillsLoadCount === 1;
        return Promise.resolve([
          { name: "alpha", description: "alpha", enabled, category: "aegis", path: "/tmp/alpha" },
          { name: "beta", description: "beta", enabled, category: "aegis", path: "/tmp/beta" },
        ]) as Promise<unknown>;
      }
      if (url === "/api/skills/toggle-category") {
        toggleCategoryCalls += 1;
        expect(init?.method).toBe("PUT");
        expect(JSON.parse(String(init?.body))).toEqual({ category: "aegis", enabled: false });
        return Promise.resolve({
          ok: true,
          category: "aegis",
          enabled: false,
          names: ["alpha", "beta"],
        }) as Promise<unknown>;
      }
      if (url === "/api/skills/alpha") {
        return Promise.resolve({
          name: "alpha",
          path: "/tmp/alpha",
          content: "# alpha",
          appendix: [],
        }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountSkillsPage();

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).textContent).toContain("aegis");
    });

    const bulkButton = (containerRef as HTMLElement).querySelector(
      ".skills-category-bulk-toggle",
    ) as HTMLButtonElement | null;
    expect(bulkButton).not.toBeNull();
    expect(bulkButton?.textContent).toBe("Disable all");

    await act(async () => {
      bulkButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect(toggleCategoryCalls).toBe(1);
      expect((containerRef as HTMLElement).textContent).toContain("aegis disabled successfully.");
      const refreshedBulkButton = (containerRef as HTMLElement).querySelector(
        ".skills-category-bulk-toggle",
      ) as HTMLButtonElement | null;
      expect(refreshedBulkButton?.textContent).toBe("Enable all");
    });
  });

  it("confirms, deletes the selected skill, and selects the next skill", async () => {
    let deleted = false;
    fetchJSONMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/skills/s1" && init?.method === "DELETE") {
        deleted = true;
        return Promise.resolve({ ok: true, name: "s1" }) as Promise<unknown>;
      }
      if (url === "/api/skills") {
        return Promise.resolve(
          deleted
            ? [{ name: "s2", description: "skill two", enabled: true, category: "devops", path: "/tmp/s2" }]
            : [
                { name: "s1", description: "skill one", enabled: true, category: "", path: "/tmp/s1" },
                { name: "s2", description: "skill two", enabled: true, category: "devops", path: "/tmp/s2" },
              ],
        ) as Promise<unknown>;
      }
      if (url === "/api/skills/s1") {
        return Promise.resolve({ name: "s1", path: "/tmp/s1", content: "# S1", appendix: [] }) as Promise<unknown>;
      }
      if (url === "/api/skills/s2") {
        return Promise.resolve({ name: "s2", path: "/tmp/s2", content: "# S2", appendix: [] }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    await mountSkillsPage();

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).querySelector('button[aria-label="Delete skill s1"]')).not.toBeNull();
    });

    const deleteButton = (containerRef as HTMLElement).querySelector(
      'button[aria-label="Delete skill s1"]',
    ) as HTMLButtonElement | null;
    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect(confirmSpy).toHaveBeenCalledWith(
        'Delete skill "s1" and everything in its folder? This cannot be undone.',
      );
      expect(fetchJSONMock).toHaveBeenCalledWith("/api/skills/s1", { method: "DELETE" });
      expect((containerRef as HTMLElement).textContent).toContain("# S2");
      expect((containerRef as HTMLElement).querySelector('button[aria-label="Delete skill s1"]')).toBeNull();
    });
  });

  it("does not delete when confirmation is cancelled and surfaces protected-path errors", async () => {
    fetchJSONMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/skills") {
        return Promise.resolve([
          { name: "protected-skill", description: "protected", enabled: true, category: "", path: "/tmp/protected" },
        ]) as Promise<unknown>;
      }
      if (url === "/api/skills/protected-skill") {
        if (init?.method === "DELETE") {
          return Promise.reject(
            new Error(JSON.stringify({ detail: "Only skills inside the current profile's local skills directory can be deleted." })),
          ) as Promise<unknown>;
        }
        return Promise.resolve({
          name: "protected-skill",
          path: "/tmp/protected",
          content: "# protected-skill",
          appendix: [],
        }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    await mountSkillsPage();

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).querySelector('button[aria-label="Delete skill protected-skill"]')).not.toBeNull();
    });

    const deleteButton = (containerRef as HTMLElement).querySelector(
      'button[aria-label="Delete skill protected-skill"]',
    ) as HTMLButtonElement | null;
    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(fetchJSONMock).not.toHaveBeenCalledWith("/api/skills/protected-skill", { method: "DELETE" });

    confirmSpy.mockReturnValue(true);
    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitForAssert(() => {
      expect((containerRef as HTMLElement).textContent).toContain(
        "Only skills inside the current profile's local skills directory can be deleted.",
      );
    });
  });
});
