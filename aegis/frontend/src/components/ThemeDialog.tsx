import { useEffect, useRef, type RefObject } from 'react';
import { Check, X } from 'lucide-react';
import { AEGIS_THEMES, AegisTheme } from '../lib/theme';

interface ThemeDialogProps {
  onClose: () => void;
  onThemeChange: (theme: AegisTheme) => void;
  selectedTheme: AegisTheme;
  triggerRef: RefObject<HTMLButtonElement | null>;
}

export default function ThemeDialog({
  onClose,
  onThemeChange,
  selectedTheme,
  triggerRef,
}: ThemeDialogProps) {
  const radioRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const hasSetInitialFocus = useRef(false);

  useEffect(() => {
    if (!hasSetInitialFocus.current) {
      const selectedIndex = AEGIS_THEMES.findIndex((theme) => theme.id === selectedTheme);
      radioRefs.current[selectedIndex]?.focus();
      hasSetInitialFocus.current = true;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose, selectedTheme]);

  useEffect(() => () => triggerRef.current?.focus(), [triggerRef]);

  function selectRelativeTheme(currentIndex: number, direction: 1 | -1) {
    const nextIndex = (currentIndex + direction + AEGIS_THEMES.length) % AEGIS_THEMES.length;
    onThemeChange(AEGIS_THEMES[nextIndex].id);
    requestAnimationFrame(() => radioRefs.current[nextIndex]?.focus());
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" onMouseDown={onClose}>
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="theme-dialog-title"
        className="aegis-theme-dialog w-full max-w-3xl rounded-2xl border p-6 shadow-[0_32px_100px_rgba(0,0,0,0.45)]"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-6">
          <div>
            <p className="font-mono text-[10px] font-bold tracking-[0.18em] text-[var(--aegis-accent)]">PERSONAL APPEARANCE</p>
            <h2 id="theme-dialog-title" className="mt-1 text-xl font-bold text-[var(--aegis-text)]">Choose your operating environment</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-[var(--aegis-text-muted)]">Your choice applies immediately and remains on this browser. Security status colors remain unchanged in every theme.</p>
          </div>
          <button
            type="button"
            aria-label="Close theme selector"
            onClick={onClose}
            className="rounded-lg border border-[var(--aegis-border)] bg-[var(--aegis-elevated)] p-2 text-[var(--aegis-text-muted)] transition hover:border-[var(--aegis-accent)] hover:text-[var(--aegis-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--aegis-focus)]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div role="radiogroup" aria-label="Aegis color theme" className="mt-6 grid gap-3 md:grid-cols-3">
          {AEGIS_THEMES.map((theme, index) => {
            const selected = theme.id === selectedTheme;
            return (
              <button
                key={theme.id}
                ref={(element) => { radioRefs.current[index] = element; }}
                type="button"
                role="radio"
                aria-checked={selected}
                tabIndex={selected ? 0 : -1}
                onClick={() => onThemeChange(theme.id)}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                    event.preventDefault();
                    selectRelativeTheme(index, 1);
                  }
                  if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                    event.preventDefault();
                    selectRelativeTheme(index, -1);
                  }
                }}
                className={`group relative overflow-hidden rounded-xl border p-3 text-left transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--aegis-focus)] ${
                  selected
                    ? 'border-[var(--aegis-accent)] ring-1 ring-[var(--aegis-accent)]'
                    : 'border-[var(--aegis-border)] hover:border-[var(--aegis-accent)]'
                }`}
                style={{ backgroundColor: theme.preview.background, color: theme.preview.text }}
              >
                <div className="rounded-lg border p-3" style={{ backgroundColor: theme.preview.surface, borderColor: theme.preview.border }}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: theme.preview.accent }} />
                    {selected ? <Check className="h-4 w-4" style={{ color: theme.preview.accent }} aria-label="Selected theme" /> : null}
                  </div>
                  <div className="mt-7 h-2.5 w-3/4 rounded" style={{ backgroundColor: theme.preview.text }} />
                  <div className="mt-2 h-2 w-full rounded" style={{ backgroundColor: theme.preview.muted, opacity: 0.72 }} />
                  <div className="mt-4 h-7 w-24 rounded" style={{ backgroundColor: theme.preview.accent }} />
                </div>
                <div className="mt-3 font-semibold">{theme.name}</div>
                <div className="mt-1 min-h-10 text-xs leading-5 opacity-75">{theme.description}</div>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
