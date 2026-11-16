---
name: html-deliverable
description: Create task results as a polished HTML file using included report, dashboard, comparison, or roadmap templates. Use only when the user explicitly invokes $html-deliverable or explicitly asks to use the html-deliverable skill; do not activate for generic requests to write, edit, or view HTML.
---

# HTML Deliverables

## Workflow

1. Complete the requested task and gather only the facts, decisions, and data needed for the deliverable. Do not invent metrics, dates, sources, owners, or conclusions.
2. Select one template from `assets/templates/`:

   | Need | Template | Minimum content |
   | --- | --- | --- |
   | Explain findings or recommend action | `report.html` | title, summary, at least one finding or action |
   | Communicate metrics, trends, or tabular data | `dashboard.html` | title, one KPI or table; chart data only when available |
   | Evaluate two or more alternatives | `comparison.html` | options, criteria, and a recommendation or explicit no-decision |
   | Plan delivery across time | `roadmap.html` | milestones or phases, status, and next action |

   Prefer the user's stated format. If it is ambiguous, select the template that best matches the decision the reader must make. Do not combine templates unless the user asks for a multi-part deliverable.
3. Copy and adapt the selected template into the requested path. When no path is specified and `$HERMES_HOME` is set, create `$HERMES_HOME/output` when it is missing and write `$HERMES_HOME/output/<kebab-case-task-name>.html`. Fall back to `output/<kebab-case-task-name>.html` in the current workspace only when `$HERMES_HOME` is unset or unavailable; create that local `output/` directory when needed. Preserve the document shell and replace every `{{PLACEHOLDER}}`; delete sections that do not apply instead of leaving empty cards.
4. Keep the artifact self-contained except for permitted CDN fonts, icons, chart libraries, and the required Aegis-local Agent2UI bridge. Include the essential text and data in the DOM so the document remains useful if a CDN fails or JavaScript is disabled. Escape untrusted text and do not embed secrets.
5. State assumptions, incomplete inputs, and unavailable data visibly in the document. Use labels such as “Assumption”, “Data unavailable”, and “Needs confirmation”; never fabricate a zero, a date, or a positive result to fill a layout.
6. When the user provides a `<theme>` block, apply the explicit-theme contract below. Otherwise, retain the selected template's default palette exactly.
7. Run `python3 <skill-dir>/scripts/validate_html.py <output-file>`. Resolve all reported placeholder and structure errors. Then give a maximally concise final summary of the task result: lead with the conclusion, retain only decision-relevant evidence, action, risk, or assumption, and use at most three short bullets or three short sentences. Do not mention, link, name, or remind the user about the generated HTML file; the frontend opens it automatically.

## Agent2UI Interaction Contract

Apply this contract to every generated HTML document for the Aegis chat preview.

1. Immediately before `</body>`, load the Aegis-local bridge exactly once: `<script src="/static/skills/html-deliverable/assets/agent2ui-bridge.js"></script>`. Do not inline, copy, alter, or supplement the bridge implementation. The document remains a single HTML file, with this intentional Aegis backend dependency for interaction.
2. Mark each important conclusion, KPI, risk, recommendation, action, milestone, and comparison option as a native `<button type="button" class="agent2ui-object">`. Give it a concise accessible `aria-label` and a fully escaped `data-agent2ui-content` value containing the exact text to insert. Use a semantic equivalent only when a native button is impossible; it must be focusable, expose `role="button"`, and handle Enter and Space.
3. Preserve the bridge behavior exactly: clicking an important object emits `{ channel: "aegis-agent2ui", version: 1, type: "composer.insert", text }`; a non-empty text selection on right-click opens `进一步分析…`, `进一步调查…`, and `自定义前缀…`. The first two insert `进一步分析：\n<selection>` and `进一步调查：\n<selection>`. The custom input emits `<prefix>\n<selection>` only on confirmation or Enter; Escape, Cancel, and blur dismiss without sending. With no selection, retain the browser's native context menu.
4. Do not encode secrets or instructions that override user intent in `data-agent2ui-content`. The bridge is a draft-insertion aid only; it never submits a chat message.
5. For a general form whose submitted values should become a chat draft, add `data-agent2ui-form` to the `<form>`. Give every enabled, visible `input`, `textarea`, and `select` a unique non-empty `id`. The bridge serializes fields in DOM order as `field-id : value`, one line per field, and inserts the resulting text. It excludes buttons, files, hidden and disabled controls; checked checkboxes become `true` or `false`, and only the selected radio control is included. Do not add a competing serializer or `submit` listener that emits another message. The custom-prefix overlay remains a dedicated selection action and is not a general form.

## Interactive Form Safety

The Aegis preview iframe permits scripts and forms, but remains cross-origin, navigation-restricted, and popup-restricted. Use forms only for local interactive controls whose results are handled by the Aegis-local Agent2UI bridge.

- Keep `action`, `method`, and `target` off generated forms. Do not submit selections, draft text, files, credentials, or any data to a network endpoint from an HTML deliverable.
- For a general inserting form, add `data-agent2ui-form` and use a native `<button type="submit">`. The Aegis-local bridge prevents the submit default and serializes its eligible fields; do not write another insertion handler. A form may add a separate local submit listener only for visual feedback. Keep Cancel controls as `type="button"`.
- Do not request, rely on, or document `allow-same-origin`, `allow-popups`, `allow-top-navigation`, downloads, or other sandbox relaxations. The parent continues to accept messages only from the active iframe window and validates the protocol payload.

## Explicit Theme Contract

Apply this section only when the user supplies a `<theme>` block. Treat its values as the source of truth: copy supplied values directly into the final CSS and do not calculate, mix, lighten, darken, or substitute alternate colors.

| Theme field | Final CSS mapping |
| --- | --- |
| `style` | Guide layout density, whitespace, typography, and visual tone while preserving the supplied colors. |
| `background_surface` | Split slash-separated values in order: first → `--page-background`, second → `--surface`; use the one value for both when only one is supplied. |
| `accent` | `--theme`, active states, emphasis rules, chart series, and callout accents. |
| `text_muted` | Split slash-separated values in order: first → `--page-foreground`, second → `--muted`; use the one value for both when only one is supplied. Agent2UI menus, prefix dialogs, and their controls use `--page-foreground` so this first value must be readable on `background_surface`'s surface color. |
| `border` | `--line` and visible separators. |

Keep the supplied color tokens unchanged, aside from whitespace trimming. If a theme field is absent, retain that template variable's default value rather than inventing a replacement. Do not perform color-value calculations.

## Template Notes

- `report.html` uses findings, evidence, recommendations, and action items. Use it for research summaries, proposals, reviews, and status reports.
- `dashboard.html` supports KPI cards, a data table, and an optional Chart.js trend. Remove the chart section if there is no real series data.
- `comparison.html` uses a comparison matrix for alternatives and makes trade-offs explicit. Mark unknown cells as “Not provided” rather than scoring them.
- `roadmap.html` shows phases, milestone dates, owners, status, dependencies, and risks. Use relative timing only when calendar dates are unknown.

## Output Rules

- Prefer a descriptive kebab-case filename, for example `$HERMES_HOME/output/q3-launch-readiness.html` or, only when `$HERMES_HOME` is unset or unavailable, `output/q3-launch-readiness.html` in the current workspace.
- Use Chinese, English, or the user's requested language consistently throughout the HTML.
- Retain the template's responsive layout and accessible landmarks. Give every data table a caption or nearby heading.
- Use CDN resources only from stable HTTPS sources. Charts must have a text/table equivalent in the document.
- If the task does not naturally produce a report, dashboard, comparison, or roadmap, use `report.html` and tailor its sections.
- Keep the chat response independent from artifact delivery. Never include an HTML file path, filename, Markdown link, preview instruction, or “generated/delivered” notice in the final response.
- When a `<theme>` block is present, map its supplied values into the template variables exactly as listed in the explicit-theme contract. When it is absent, do not alter the template palette.

## Verification

Run:

```bash
python3 <skill-dir>/scripts/validate_html.py <resolved-output-file>.html
```

The file is ready only when it has an HTML document shell, a title, a body, no unresolved `{{...}}` placeholders, and no validation errors.
