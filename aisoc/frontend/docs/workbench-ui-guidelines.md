# Workbench UI Guidelines

This guide keeps AISOC workbench pages visually consistent while preserving current behavior contracts.

## Shared Tokens

Use the shared `:root` tokens in `src/styles.css` as the single source of truth.

- Color/surface tokens:
  - `--aisoc-bg`, `--aisoc-bg-alt`
  - `--aisoc-panel`, `--aisoc-panel-strong`, `--aisoc-surface`
  - `--aisoc-accent`, `--aisoc-accent-strong`, `--aisoc-accent-soft`
  - `--aisoc-text`, `--aisoc-muted`, `--aisoc-danger`
  - `--aisoc-border`, `--aisoc-border-strong`
- Shape and motion tokens:
  - `--aisoc-radius-sm`, `--aisoc-radius-md`, `--aisoc-radius-lg`
  - `--aisoc-shadow`
  - `--aisoc-transition`
- Layer tokens:
  - Canonical: `--aisoc-z-content`, `--aisoc-z-sticky`, `--aisoc-z-drawer`, `--aisoc-z-modal`, `--aisoc-z-modal-critical`
  - Compatibility aliases: `--z-content`, `--z-sticky`, `--z-drawer`, `--z-modal`, `--z-modal-critical`

Rules:

1. Prefer `--aisoc-*` tokens directly for new code.
2. Avoid introducing page-specific color/radius constants unless a token gap is real.
3. Keep typography/body contrast aligned with `--aisoc-text` and `--aisoc-muted`.

## Panel Grammar

Compose pages from shared structural primitives rather than one-off wrappers.

Baseline structure:

1. Page wrapper: `<section className="<page>-workbench-page">`
2. Mission header: `<PageMissionHeader ... />`
3. Main workspace split: `<div className="<page>-workbench">`
4. Content regions:
   - Primary panes: `article.detail-panel`
   - Context rail: `aside.detail-panel`
   - Reusable blocks: `.panel`, `.cyber-panel`, `.card`

Shared behavior primitives:

- Lists use `.list-grid`; interactive rows use `.clickable-card` plus `.active` when selected.
- Repeated multi-column detail patterns use `.detail-layout`.
- Action clusters use `.button-row`.
- Inline payload views use `.detail-content` and `<pre>` styling.

Rule of thumb: if a page can be expressed with `PageMissionHeader + detail-panel + list-grid + StateBlock`, do not invent new base visual primitives.

## Z-Index Scale

Use this layering scale consistently:

- `--aisoc-z-content` (`10`): default cards, panes, and page content
- `--aisoc-z-sticky` (`20`): sticky local controls/bars that should sit above content
- `--aisoc-z-drawer` (`30`): drawers or side overlays
- `--aisoc-z-modal` (`50`): standard modal dialogs
- `--aisoc-z-modal-critical` (`60`): confirmation/critical dialogs that must top regular modals

Rules:

1. Do not hardcode numeric `z-index` values in components.
2. If a new layer is needed, add a named token first, then consume it.
3. Nested modal flows should move up the scale rather than competing at the same layer.

## Async Feedback Rules

Async states should be explicit at both page and panel scopes.

Loading:

- For panel-level data loads, render `StateBlock kind="loading"` in the target pane.
- For lightweight secondary loads, short `.subtle-copy` loading text is acceptable.

Empty:

- Use `StateBlock kind="empty"` when the empty state is a key workflow branch.
- Use concise `.subtle-copy` empty text for minor informational empties.

Error:

- Use `StateBlock kind="error"` for blocking fetch failures (page or major panel).
- Use local `.error-text` near action zones for operation failures (trigger/pause/resume/toggle, etc.).

Pending actions and race-safety:

- Disable controls while an action is in flight and reflect verb progress in labels (`Triggering...`, `Pausing...`, `Resuming...`).
- Guard detail fetch races with request-id checks (`isLatest*Request`) before committing state.
- Keep analyst context visible during transitions (header/status/context rails remain mounted).

These rules keep cron, skills, sessions, memory, and overview behavior aligned without changing API contracts.
