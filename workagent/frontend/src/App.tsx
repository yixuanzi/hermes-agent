import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { RequireAuth } from "./components/RequireAuth";
import { ChatPage } from "./pages/ChatPage";
import { CronPage } from "./pages/CronPage";
import { LoginPage } from "./pages/LoginPage";
import { MemoryPage } from "./pages/MemoryPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OverviewPage } from "./pages/OverviewPage";

const OntologyPage = lazy(() =>
  import("./pages/OntologyPage").then((m) => ({ default: m.OntologyPage })),
);

function OntologyRoute() {
  return (
    <Suspense
      fallback={
        <div style={{ padding: "2rem", color: "var(--text-muted, #94a3b8)", fontFamily: "monospace" }}>
          加载本体模块…
        </div>
      }
    >
      <OntologyPage />
    </Suspense>
  );
}
import { SessionsPage } from "./pages/SessionsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SkillsPage } from "./pages/SkillsPage";
import { WikiPage } from "./pages/WikiPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/cron" element={<CronPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/wiki" element={<WikiPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/ontology/*" element={<OntologyRoute />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
