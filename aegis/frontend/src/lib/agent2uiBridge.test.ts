import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { fireEvent } from "@testing-library/react";

const bridge = readFileSync(
  resolve(process.cwd(), "../skills/html-deliverable/assets/agent2ui-bridge.js"),
  "utf8",
);

function selectText(element: HTMLElement) {
  const textNode = element.firstChild as Text;
  const range = document.createRange();
  range.selectNodeContents(textNode);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

async function flushMessage() {
  await new Promise((resolve) => window.setTimeout(resolve, 0));
}

function installBridge() {
  document.body.innerHTML = `<button type="button" data-agent2ui-content="Use this conclusion">Conclusion</button><p>Selected evidence</p><form data-agent2ui-form><input id="case-id" type="text"><textarea id="case-note"></textarea><button type="submit">Insert form</button></form>`;
  const script = document.createElement("script");
  script.textContent = bridge;
  document.body.append(script);
}

describe("html-deliverable Agent2UI bridge", () => {
  afterEach(() => {
    window.getSelection()?.removeAllRanges();
    document.head.querySelectorAll("style").forEach((style) => style.remove());
    document.body.replaceChildren();
  });

  it("sends an important object's declared content to the parent protocol", async () => {
    const messages: unknown[] = [];
    const listener = (event: MessageEvent) => messages.push(event.data);
    window.addEventListener("message", listener);
    installBridge();

    fireEvent.click(document.querySelector("[data-agent2ui-content]")!);
    await flushMessage();

    expect(messages).toContainEqual({
      channel: "aegis-agent2ui",
      version: 1,
      type: "composer.insert",
      text: "Use this conclusion",
    });
    window.removeEventListener("message", listener);
  });

  it("uses the page foreground for menus on a dark surface", () => {
    installBridge();

    const style = document.head.querySelector("style")!;
    expect(style.textContent).toContain(
      "color:var(--page-foreground, var(--surface-foreground, #142235))",
    );
  });

  it("serializes every marked form field as id and value on native submit", async () => {
    const messages: unknown[] = [];
    const listener = (event: MessageEvent) => messages.push(event.data);
    window.addEventListener("message", listener);
    installBridge();

    const form = document.querySelector("form[data-agent2ui-form]")!;
    (document.querySelector("#case-id") as HTMLInputElement).value = "INC-42";
    (document.querySelector("#case-note") as HTMLTextAreaElement).value = "复核完成";
    fireEvent.submit(form);
    await flushMessage();

    expect(messages).toContainEqual({
      channel: "aegis-agent2ui",
      version: 1,
      type: "composer.insert",
      text: "case-id : INC-42\ncase-note : 复核完成",
    });
    window.removeEventListener("message", listener);
  });

  it("offers analysis, investigation, and custom-prefix actions only for a selection", async () => {
    const messages: unknown[] = [];
    const listener = (event: MessageEvent) => messages.push(event.data);
    window.addEventListener("message", listener);
    installBridge();
    const evidence = document.querySelector("p")!;

    fireEvent.contextMenu(evidence, { clientX: 24, clientY: 24 });
    expect(document.querySelector(".agent2ui-menu")).toBeNull();

    selectText(evidence);
    fireEvent.contextMenu(evidence, { clientX: 24, clientY: 24 });
    const menu = document.querySelector(".agent2ui-menu")!;
    expect(menu.textContent).toContain("进一步分析…");
    expect(menu.textContent).toContain("进一步调查…");
    expect(menu.textContent).toContain("自定义前缀…");
    fireEvent.click([...menu.querySelectorAll("button")].find((button) => button.textContent === "进一步分析…")!);
    await flushMessage();
    expect(messages).toContainEqual(expect.objectContaining({ text: "进一步分析：\nSelected evidence" }));

    selectText(evidence);
    fireEvent.contextMenu(evidence, { clientX: 24, clientY: 24 });
    fireEvent.click([...document.querySelectorAll(".agent2ui-menu button")].find((button) => button.textContent === "自定义前缀…")!);
    const form = document.querySelector(".agent2ui-prefix") as HTMLFormElement;
    const input = form.querySelector("input") as HTMLInputElement;
    input.value = "请聚焦风险";
    const insertButton = [...form.querySelectorAll("button")].find(
      (button) => button.textContent === "插入",
    )!;
    expect(insertButton).toHaveAttribute("type", "submit");
    fireEvent.click(insertButton);
    await flushMessage();
    expect(messages).toContainEqual(expect.objectContaining({ text: "请聚焦风险\nSelected evidence" }));
    window.removeEventListener("message", listener);
  });
});
