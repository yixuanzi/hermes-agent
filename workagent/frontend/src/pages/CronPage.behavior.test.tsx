/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON } from "../lib/api";
import { CronPage } from "./CronPage";

vi.mock("../lib/api", () => ({
  fetchJSON: vi.fn(),
}));

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
};

type CronJobsPagePayload = {
  items: Array<Record<string, unknown>>;
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
};

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function jobsPage(
  items: Array<Record<string, unknown>>,
  page: number,
  total: number,
  totalPages: number,
): CronJobsPagePayload {
  return {
    items,
    page,
    page_size: 12,
    total,
    total_pages: totalPages,
    has_prev: page > 1,
    has_next: page < totalPages,
  };
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

function findJobRow(container: HTMLElement, name: string): HTMLLIElement {
  const row = Array.from(container.querySelectorAll<HTMLLIElement>('li[role="button"]')).find((candidate) =>
    candidate.textContent?.includes(name),
  );
  if (!row) throw new Error(`Cron job row not found for: ${name}`);
  return row;
}

function findActionButton(scope: ParentNode, label: string): HTMLButtonElement {
  const button = Array.from(scope.querySelectorAll<HTMLButtonElement>("button")).find(
    (candidate) => candidate.getAttribute("aria-label") === label || candidate.textContent?.includes(label),
  );
  if (!button) throw new Error(`Action button not found: ${label}`);
  return button;
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

async function mountCronPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/cron"]}>
        <CronPage />
      </MemoryRouter>,
    );
  });

  rootRef = root;
  containerRef = container;

  return { container, root };
}

describe("CronPage behavior", () => {
  it("creates cron job from New modal and refreshes list", async () => {
    const createdPayload = {
      name: "daily-test-msg",
      prompt: "向当前对话发送一条测试消息，内容为：这是一条每日测试消息。",
      schedule: "* * * * *",
      deliver: "slack",
      skills: [],
      skill: null,
      enabled_toolsets: null,
      model: "deepseek-v4-flash",
      provider: null,
      base_url: null,
      script: null,
      workdir: null,
      no_agent: false,
    };

    let jobsLoadCount = 0;
    fetchJSONMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/cron/jobs?page=1&page_size=12" && (!init || !init.method || init.method === "GET")) {
        jobsLoadCount += 1;
        if (jobsLoadCount === 1) {
          return Promise.resolve(jobsPage([], 1, 0, 1)) as Promise<unknown>;
        }
        return Promise.resolve(
          jobsPage([{ id: "job-new", name: "daily-test-msg", profile: "default", paused: false }], 1, 1, 1),
        ) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs" && init?.method === "POST") {
        expect(init.body).toBe(JSON.stringify(createdPayload));
        return Promise.resolve({ id: "job-new", ...createdPayload }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountCronPage();

    const newButton = findActionButton(containerRef as HTMLElement, "New");

    await act(async () => {
      newButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Create Job");
      expect(text).not.toContain("Edit JSON payload for");
    });

    const createButton = findActionButton(containerRef as HTMLElement, "Create Job");

    await act(async () => {
      createButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("daily-test-msg");
    });
  });

  it("shows at most 12 job cards per page and supports pagination buttons", async () => {
    const page1Items = Array.from({ length: 12 }, (_, index) => ({
      id: `job-${index + 1}`,
      name: `Job ${index + 1}`,
      paused: false,
      profile: "default",
    }));
    const page2Items = [{ id: "job-13", name: "Job 13", paused: false, profile: "default" }];

    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/cron/jobs?page=1&page_size=12") {
        return Promise.resolve(jobsPage(page1Items, 1, 13, 2)) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs?page=2&page_size=12") {
        return Promise.resolve(jobsPage(page2Items, 2, 13, 2)) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountCronPage();

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).querySelectorAll('li[role="button"]').length).toBe(12);
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Page 1 / 2");
      expect(text).toContain("Job 12");
      expect(text).not.toContain("Job 13");
    });

    const nextButton = findActionButton(containerRef as HTMLElement, "Next");

    await act(async () => {
      nextButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).querySelectorAll('li[role="button"]').length).toBe(1);
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Page 2 / 2");
      expect(text).toContain("Job 13");
    });
  });

  it("deletes job card after confirmation", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    let deleted = false;

    fetchJSONMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/cron/jobs?page=1&page_size=12") {
        if (!deleted) {
          return Promise.resolve(
            jobsPage([{ id: "job-a", name: "Job A", paused: false, profile: "default" }], 1, 1, 1),
          ) as Promise<unknown>;
        }
        return Promise.resolve(jobsPage([], 1, 0, 1)) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a" && init?.method === "DELETE") {
        deleted = true;
        return Promise.resolve({ ok: true }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountCronPage();

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).textContent || "").toContain("Job A");
    });

    const row = findJobRow(containerRef as HTMLElement, "Job A");
    const deleteButton = findActionButton(row, "Delete job");

    await act(async () => {
      deleteButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(confirmSpy).toHaveBeenCalled();
      expect(text).not.toContain("Job A");
      expect(text).toContain("No Cron Jobs Found");
    });

    confirmSpy.mockRestore();
  });

  it("shows history list directly and opens session messages for selected run", async () => {
    const aDeferred = createDeferred<Record<string, unknown>[]>();
    const bDeferred = createDeferred<Record<string, unknown>[]>();

    fetchJSONMock.mockImplementation((url: string) => {
      if (url === "/api/cron/jobs?page=1&page_size=12") {
        return Promise.resolve(
          jobsPage(
            [
              { id: "a", name: "Job A", profile: "default", paused: false },
              { id: "b", name: "Job B", profile: "default", paused: false },
            ],
            1,
            2,
            1,
          ),
        ) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/a/history") {
        return aDeferred.promise as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/b/history") {
        return bDeferred.promise as Promise<unknown>;
      }
      if (url === "/api/sessions/cron_b_session/messages") {
        return Promise.resolve({
          session_id: "cron_b_session",
          messages: [{ role: "assistant", content: "hello from cron_b" }],
        }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountCronPage();

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).querySelectorAll('li[role="button"]').length).toBe(2);
    });

    const rowA = findJobRow(containerRef as HTMLElement, "Job A");
    const rowB = findJobRow(containerRef as HTMLElement, "Job B");

    await act(async () => {
      rowA.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      rowB.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      bDeferred.resolve([
        {
          session_id: "cron_b_session",
          started_at: 1717000000,
          messages: 5,
          tokens: 1024,
          status: "completed",
        },
      ]);
      await Promise.resolve();
    });

    await act(async () => {
      aDeferred.resolve([
        {
          session_id: "stale_a_session",
          started_at: 1717000001,
          messages: 2,
          tokens: 200,
          status: "completed",
        },
      ]);
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("cron_b_session");
      expect(text).not.toContain("stale_a_session");
      expect(text).not.toContain("Selected Job:");
    });

    const sessionButton = findActionButton(containerRef as HTMLElement, "cron_b_session");

    await act(async () => {
      sessionButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Session Messages");
      expect(text).toContain("hello from cron_b");
    });
  });

  it("edits raw detail json, confirms save, and refreshes detail", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    let rawSaveCount = 0;

    fetchJSONMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/cron/jobs?page=1&page_size=12") {
        return Promise.resolve(
          jobsPage([{ id: "job-a", name: "Job A", paused: false, profile: "default" }], 1, 1, 1),
        ) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a/history") {
        return Promise.resolve([]) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a" && (!init || !init.method || init.method === "GET")) {
        if (rawSaveCount === 0) {
          return Promise.resolve({
            id: "job-a",
            name: "Job A",
            prompt: "before prompt",
            profile: "default",
            profile_name: "default",
            hermes_home: "/tmp/default",
            is_default_profile: true,
          }) as Promise<unknown>;
        }
        return Promise.resolve({
          id: "job-a",
          name: "Job A updated",
          prompt: "after prompt",
          profile: "default",
          profile_name: "default",
          hermes_home: "/tmp/default",
          is_default_profile: true,
        }) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a/raw" && init?.method === "PUT") {
        rawSaveCount += 1;
        expect(confirmSpy).toHaveBeenCalled();
        expect(init.body).toBe(
          JSON.stringify({
            job: {
              id: "job-a",
              name: "Job A updated",
              prompt: "after prompt",
              profile: "default",
              profile_name: "default",
              hermes_home: "/tmp/default",
              is_default_profile: true,
            },
          }),
        );
        return Promise.resolve({
          id: "job-a",
          name: "Job A updated",
          prompt: "after prompt",
          profile: "default",
        }) as Promise<unknown>;
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountCronPage();

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).textContent || "").toContain("Job A");
    });

    const row = findJobRow(containerRef as HTMLElement, "Job A");
    const detailButton = findActionButton(row, "Open detail");

    await act(async () => {
      detailButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).textContent || "").toContain("Editable Updates JSON");
    });

    const rawButton = findActionButton(containerRef as HTMLElement, "Raw JSON");
    await act(async () => {
      rawButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const rawEditor = (containerRef as HTMLElement).querySelector<HTMLTextAreaElement>("#cron-raw-editor");
    expect(rawEditor).not.toBeNull();

    await act(async () => {
      rawEditor!.value = JSON.stringify({
        id: "job-a",
        name: "Job A updated",
        prompt: "after prompt",
        profile: "default",
        profile_name: "default",
        hermes_home: "/tmp/default",
        is_default_profile: true,
      }, null, 2);
      rawEditor!.dispatchEvent(new Event("input", { bubbles: true }));
      rawEditor!.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    const saveButton = findActionButton(containerRef as HTMLElement, "Save Raw JSON");
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      const text = (containerRef as HTMLElement).textContent || "";
      expect(text).toContain("Job A updated");
      expect(text).toContain("Cron job raw detail updated.");
    });

    confirmSpy.mockRestore();
  });

  it("does not save raw detail json when confirmation is cancelled", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    fetchJSONMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/cron/jobs?page=1&page_size=12") {
        return Promise.resolve(
          jobsPage([{ id: "job-a", name: "Job A", paused: false, profile: "default" }], 1, 1, 1),
        ) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a/history") {
        return Promise.resolve([]) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a" && (!init || !init.method || init.method === "GET")) {
        return Promise.resolve({
          id: "job-a",
          name: "Job A",
          prompt: "before prompt",
          profile: "default",
          profile_name: "default",
          hermes_home: "/tmp/default",
          is_default_profile: true,
        }) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a/raw" && init?.method === "PUT") {
        throw new Error("raw save should not be called when confirmation is cancelled");
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountCronPage();

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).textContent || "").toContain("Job A");
    });

    const row = findJobRow(containerRef as HTMLElement, "Job A");
    const detailButton = findActionButton(row, "Open detail");

    await act(async () => {
      detailButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const rawButton = findActionButton(containerRef as HTMLElement, "Raw JSON");
    await act(async () => {
      rawButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const rawEditor = (containerRef as HTMLElement).querySelector<HTMLTextAreaElement>("#cron-raw-editor");
    expect(rawEditor).not.toBeNull();

    await act(async () => {
      rawEditor!.value = JSON.stringify({ id: "job-a", name: "Job A updated", profile: "default" }, null, 2);
      rawEditor!.dispatchEvent(new Event("input", { bubbles: true }));
      rawEditor!.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    const saveButton = findActionButton(containerRef as HTMLElement, "Save Raw JSON");
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(confirmSpy).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("shows client-side error for invalid raw detail json", async () => {
    fetchJSONMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/cron/jobs?page=1&page_size=12") {
        return Promise.resolve(
          jobsPage([{ id: "job-a", name: "Job A", paused: false, profile: "default" }], 1, 1, 1),
        ) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a/history") {
        return Promise.resolve([]) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a" && (!init || !init.method || init.method === "GET")) {
        return Promise.resolve({
          id: "job-a",
          name: "Job A",
          prompt: "before prompt",
          profile: "default",
          profile_name: "default",
          hermes_home: "/tmp/default",
          is_default_profile: true,
        }) as Promise<unknown>;
      }
      if (url === "/api/cron/jobs/job-a/raw" && init?.method === "PUT") {
        throw new Error("raw save should not be called when json is invalid");
      }
      throw new Error(`Unexpected URL in test: ${url}`);
    });

    await mountCronPage();

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).textContent || "").toContain("Job A");
    });

    const row = findJobRow(containerRef as HTMLElement, "Job A");
    const detailButton = findActionButton(row, "Open detail");

    await act(async () => {
      detailButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const rawButton = findActionButton(containerRef as HTMLElement, "Raw JSON");
    await act(async () => {
      rawButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const rawEditor = (containerRef as HTMLElement).querySelector<HTMLTextAreaElement>("#cron-raw-editor");
    expect(rawEditor).not.toBeNull();

    await act(async () => {
      rawEditor!.value = "{";
      rawEditor!.dispatchEvent(new Event("input", { bubbles: true }));
      rawEditor!.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    const saveButton = findActionButton(containerRef as HTMLElement, "Save Raw JSON");
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitForAssert(() => {
      expect((containerRef as HTMLElement).textContent || "").toContain("Invalid JSON");
    });
  });
});
