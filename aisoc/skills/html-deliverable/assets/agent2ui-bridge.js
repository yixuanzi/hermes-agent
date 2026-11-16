(() => {
  "use strict";

  const channel = "aisoc-agent2ui";
  const version = 1;
  const objectSelector = "[data-agent2ui-content]";
  let selectedText = "";
  let overlay = null;

  const style = document.createElement("style");
  style.textContent = `
    .agent2ui-object { cursor:pointer; }
    .agent2ui-object:focus-visible, .agent2ui-menu button:focus-visible, .agent2ui-prefix input:focus-visible { outline:3px solid var(--theme, #087ea4); outline-offset:3px; }
    .agent2ui-menu, .agent2ui-prefix { position:fixed; z-index:2147483647; width:min(248px, calc(100vw - 24px)); padding:6px; border:1px solid var(--line, #c6d2e0); border-radius:10px; background:var(--surface, #fff); color:var(--page-foreground, var(--surface-foreground, #142235)); box-shadow:0 12px 34px rgb(20 34 53 / .22); }
    .agent2ui-menu button { display:block; width:100%; border:0; border-radius:7px; padding:9px 10px; background:transparent; color:inherit; font:inherit; text-align:left; cursor:pointer; }
    .agent2ui-menu button:hover, .agent2ui-menu button:focus-visible { background:var(--page-background, #f4f7fb); }
    .agent2ui-prefix { display:grid; gap:8px; padding:10px; }
    .agent2ui-prefix label { font:600 12px/1.3 system-ui, sans-serif; }
    .agent2ui-prefix input { width:100%; border:1px solid var(--line, #c6d2e0); border-radius:6px; padding:8px; background:var(--page-background, #f4f7fb); color:inherit; font:inherit; }
    .agent2ui-prefix__actions { display:flex; justify-content:flex-end; gap:8px; }
    .agent2ui-prefix button { border:1px solid var(--line, #c6d2e0); border-radius:6px; padding:6px 9px; background:var(--surface, #fff); color:inherit; font:inherit; cursor:pointer; }
    .agent2ui-prefix button[type="submit"] { border-color:var(--theme, #087ea4); background:var(--theme, #087ea4); color:var(--on-theme, #fff); }
  `;
  document.head.append(style);

  function emit(text) {
    if (!text || !text.trim()) return;
    window.parent.postMessage(
      { channel, version, type: "composer.insert", text },
      "*",
    );
  }

  function formValue(field) {
    if (field instanceof HTMLInputElement) {
      if (field.type === "checkbox") return field.checked ? "true" : "false";
      if (field.type === "radio" && !field.checked) return null;
    }
    if (field instanceof HTMLSelectElement && field.multiple) {
      return Array.from(field.selectedOptions, (option) => option.value).join(", ");
    }
    return field.value;
  }

  function serializeFormFields(form) {
    return Array.from(form.querySelectorAll("input, textarea, select"))
      .filter((field) => {
        if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) return false;
        if (!field.id || field.disabled) return false;
        return !["button", "submit", "reset", "image", "file", "hidden"].includes(field.type);
      })
      .map((field) => {
        const value = formValue(field);
        return value === null ? null : `${field.id} : ${String(value).replace(/[\r\n]+/g, " ").trim()}`;
      })
      .filter((line) => line !== null)
      .join("\n");
  }

  function closeOverlay() {
    overlay?.remove();
    overlay = null;
  }

  function place(node, x, y) {
    document.body.append(node);
    const margin = 12;
    const rect = node.getBoundingClientRect();
    node.style.left = `${Math.max(margin, Math.min(x, window.innerWidth - rect.width - margin))}px`;
    node.style.top = `${Math.max(margin, Math.min(y, window.innerHeight - rect.height - margin))}px`;
    overlay = node;
  }

  function openPrefixInput(x, y) {
    closeOverlay();
    const form = document.createElement("form");
    form.className = "agent2ui-prefix";
    form.setAttribute("role", "dialog");
    form.setAttribute("aria-label", "自定义前缀");
    form.innerHTML = `<label for="agent2ui-prefix-input">自定义前缀</label><input id="agent2ui-prefix-input" type="text" autocomplete="off"><div class="agent2ui-prefix__actions"><button type="button">取消</button><button type="submit">插入</button></div>`;
    const input = form.querySelector("input");
    const [cancel] = form.querySelectorAll("button");
    function insertPrefix() {
      const prefix = input.value.trim();
      if (prefix) emit(`${prefix}\n${selectedText}`);
      closeOverlay();
    }
    cancel.addEventListener("click", closeOverlay);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      insertPrefix();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeOverlay();
      }
    });
    input.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (overlay === form && !form.contains(document.activeElement)) closeOverlay();
      }, 0);
    });
    place(form, x, y);
    input.focus();
  }

  function openMenu(x, y) {
    closeOverlay();
    const menu = document.createElement("div");
    menu.className = "agent2ui-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "选中文本操作");
    const actions = [
      ["进一步分析…", () => emit(`进一步分析：\n${selectedText}`)],
      ["进一步调查…", () => emit(`进一步调查：\n${selectedText}`)],
      ["自定义前缀…", () => openPrefixInput(x, y)],
    ];
    actions.forEach(([label, action]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.setAttribute("role", "menuitem");
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (label !== "自定义前缀…") closeOverlay();
        action();
      });
      menu.append(button);
    });
    place(menu, x, y);
    menu.querySelector("button")?.focus();
  }

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest(objectSelector) : null;
    if (target) emit(target.getAttribute("data-agent2ui-content") || target.textContent || "");
    if (overlay && !overlay.contains(event.target)) closeOverlay();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeOverlay();
    const target = event.target instanceof Element ? event.target.closest(objectSelector) : null;
    if (
      target &&
      target.tagName !== "BUTTON" &&
      target.getAttribute("role") === "button" &&
      (event.key === "Enter" || event.key === " ")
    ) {
      event.preventDefault();
      emit(target.getAttribute("data-agent2ui-content") || target.textContent || "");
    }
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-agent2ui-form")) return;
    event.preventDefault();
    emit(serializeFormFields(form));
  });

  document.addEventListener("contextmenu", (event) => {
    if (overlay?.contains(event.target)) return;
    const text = window.getSelection()?.toString().trim() || "";
    if (!text) return;
    event.preventDefault();
    selectedText = text;
    openMenu(event.clientX, event.clientY);
  });

  document.addEventListener("pointerdown", (event) => {
    if (overlay && !overlay.contains(event.target)) closeOverlay();
  });
  window.addEventListener("scroll", closeOverlay, true);
  window.addEventListener("resize", closeOverlay);
})();
