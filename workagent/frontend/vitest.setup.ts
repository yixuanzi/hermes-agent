// Node 25+ defines its own `localStorage`/`sessionStorage` accessors on the
// global object. Without --localstorage-file they resolve to `undefined` (or a
// Storage without working methods), and in the jsdom environment `globalThis`
// IS the window, so they shadow jsdom's Storage and every test touching storage
// throws before it runs. Install an in-memory Storage wherever the global one
// is missing or unusable, before any test module reads it.
function memoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    key: (index: number) => [...store.keys()][index] ?? null,
    getItem: (key: string) => store.get(String(key)) ?? null,
    setItem: (key: string, value: string) => void store.set(String(key), String(value)),
    removeItem: (key: string) => void store.delete(String(key)),
    clear: () => store.clear(),
  };
}

function isUsable(name: "localStorage" | "sessionStorage"): boolean {
  try {
    const storage = globalThis[name];
    return typeof storage?.getItem === "function" && typeof storage.clear === "function";
  } catch {
    // Some Node builds throw from the accessor instead of returning undefined.
    return false;
  }
}

for (const name of ["localStorage", "sessionStorage"] as const) {
  if (isUsable(name)) continue;
  const storage = memoryStorage();
  const window = (globalThis as { window?: object }).window;
  for (const target of new Set([globalThis, window].filter(Boolean) as object[])) {
    Object.defineProperty(target, name, { value: storage, configurable: true, writable: true });
  }
}
