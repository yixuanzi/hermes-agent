import { useLocation } from "react-router-dom";

import "../ontology/ontology.css";
import { StandardGraphPage } from "../ontology/pages/StandardGraphPage";
import { DifferentiationOverviewPage } from "../ontology/pages/DifferentiationOverviewPage";

type OntologyView = "standard-graph" | "diff-overview";

export function resolveOntologyView(pathname: string): OntologyView {
  if (pathname.startsWith("/ontology/diff-overview")) return "diff-overview";
  return "standard-graph";
}

/** Thin container: sub-view selection is driven by the outer AppShell nav
 * (which exposes the ontology children directly under Workbench).
 * Everything inside just renders the active child page. React Query cache
 * comes from the app-level QueryClientProvider (src/main.tsx), so it survives
 * route switches away from /ontology as well. */
export function OntologyPage() {
  const location = useLocation();
  const view = resolveOntologyView(location.pathname);

  return (
    <section className="ontology-scope" style={{ padding: "1.5rem", minHeight: "100%", overflowY: "auto" }}>
      {view === "standard-graph" ? <StandardGraphPage /> : null}
      {view === "diff-overview" ? <DifferentiationOverviewPage /> : null}
    </section>
  );
}
