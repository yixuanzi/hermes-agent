// Node 25+ ships its own Web Storage globals; without `--localstorage-file` the getter yields
// undefined, and under vitest it shadows jsdom's implementation. Install an in-memory Storage
// only when the global one is missing or unusable, so a Node that behaves keeps jsdom's.

class MemoryStorage implements Storage {
  private readonly items = new Map<string, string>();

  get length(): number {
    return this.items.size;
  }

  clear(): void {
    this.items.clear();
  }

  getItem(key: string): string | null {
    return this.items.get(String(key)) ?? null;
  }

  key(index: number): string | null {
    return Array.from(this.items.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.items.delete(String(key));
  }

  setItem(key: string, value: string): void {
    this.items.set(String(key), String(value));
  }
}

function isUsable(name: 'localStorage' | 'sessionStorage'): boolean {
  try {
    const storage = globalThis[name];
    if (typeof storage?.getItem !== 'function') return false;
    storage.getItem('__probe__');
    return true;
  } catch {
    return false;
  }
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  if (isUsable(name)) continue;
  const storage = new MemoryStorage();
  for (const target of new Set<object>([globalThis, window])) {
    Object.defineProperty(target, name, { configurable: true, value: storage });
  }
}
