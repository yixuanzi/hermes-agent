import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import "../ontology/ontology.css";
import { StandardGraphPage } from "../ontology/pages/StandardGraphPage";
import { DifferentiationOverviewPage } from "../ontology/pages/DifferentiationOverviewPage";
import { OntologyChatPage } from "../ontology/pages/OntologyChatPage";

type OntologyView = "standard-graph" | "diff-overview" | "chat";

export function resolveOntologyView(pathname: string): OntologyView {
  if (pathname.startsWith("/ontology/diff-overview")) return "diff-overview";
  if (pathname.startsWith("/ontology/chat")) return "chat";
  return "standard-graph";
}

/** Thin container: sub-view selection is driven by the outer AppShell nav
 * (which exposes the three ontology children directly under Workbench).
 * Everything inside just renders the active child page under the shared
 * QueryClientProvider so cache survives sub-view switches. */
export function OntologyPage() {
  const location = useLocation();
  const view = resolveOntologyView(location.pathname);

  const queryClient = useMemo(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: false, staleTime: 30_000 },
        },
      }),
    [],
  );

  return (
    <QueryClientProvider client={queryClient}>
      <section className="ontology-scope" style={{ padding: "1.5rem", minHeight: "100%", overflowY: "auto" }}>
        {view === "standard-graph" ? <StandardGraphPage /> : null}
        {view === "diff-overview" ? <DifferentiationOverviewPage /> : null}
        {view === "chat" ? <OntologyChatPage /> : null}
      </section>
    </QueryClientProvider>
  );
}
