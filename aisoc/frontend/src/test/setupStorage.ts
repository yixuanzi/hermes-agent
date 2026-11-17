// Node 25+ ships its own global Web Storage. Without --localstorage-file its
// `localStorage` is undefined, and under vitest that global shadows jsdom's,
// so `localStorage.clear()` throws. Give every test file a working in-memory
// Storage wherever the global one is missing or unusable.

function createMemoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key);
    },
    setItem: (key: string, value: string) => {
      data.set(key, String(value));
    },
  };
}

function isUsable(read: () => Storage | undefined): boolean {
  try {
    const storage = read();
    if (!storage) return false;
    storage.setItem("__storage_probe__", "1");
    storage.removeItem("__storage_probe__");
    return true;
  } catch {
    return false;
  }
}

const scopes: object[] = [globalThis];
if (typeof window !== "undefined" && (window as object) !== globalThis) scopes.push(window);

for (const scope of scopes) {
  for (const name of ["localStorage", "sessionStorage"] as const) {
    if (!isUsable(() => (scope as Record<string, Storage | undefined>)[name])) {
      Object.defineProperty(scope, name, { value: createMemoryStorage(), configurable: true, writable: true });
    }
  }
}
