import { FormEvent, useState } from "react";

import { fetchJSON } from "../lib/api";

type LogsResponse = {
  file: string;
  lines: string[];
};

export function LogsPage() {
  const [file, setFile] = useState("agent");
  const [level, setLevel] = useState("ALL");
  const [component, setComponent] = useState("all");
  const [search, setSearch] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState("");

  async function load(event?: FormEvent) {
    event?.preventDefault();
    setError("");
    try {
      const query = new URLSearchParams({
        file,
        level,
        component,
        search,
        lines: "200",
      });
      const payload = await fetchJSON<LogsResponse>(`/api/logs?${query.toString()}`);
      setLines(payload.lines || []);
    } catch {
      setError("Failed to load logs.");
    }
  }

  return (
    <section>
      <h2>Logs</h2>
      <form className="filter-row" onSubmit={load}>
        <input value={file} onChange={(e) => setFile(e.target.value)} placeholder="file" />
        <input value={level} onChange={(e) => setLevel(e.target.value)} placeholder="level" />
        <input
          value={component}
          onChange={(e) => setComponent(e.target.value)}
          placeholder="component"
        />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="search text"
        />
        <button type="submit">Load</button>
      </form>
      {error ? <p className="error-text">{error}</p> : null}
      <pre className="log-box">{lines.join("\n")}</pre>
    </section>
  );
}
