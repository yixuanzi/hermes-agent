import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { RequireAuth } from "./components/RequireAuth";
import { ChatPage } from "./pages/ChatPage";
import { CronPage } from "./pages/CronPage";
import { LoginPage } from "./pages/LoginPage";
import { McpServersPage } from "./pages/McpServersPage";
import { MemoryPage } from "./pages/MemoryPage";
import { SkillHubPage } from "./pages/SkillHubPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OverviewPage } from "./pages/OverviewPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ServiceEntryPage } from "./pages/ServiceEntryPage";
import { SsoCallbackPage } from "./pages/SsoCallbackPage";
import { UsersPage } from "./pages/UsersPage";

const OntologyPage = lazy(() =>
  import("./pages/OntologyPage").then((m) => ({ default: m.OntologyPage })),
);

function OntologyRoute() {
  return (
    <Suspense
      fallback={
        <div style={{ padding: "2rem", color: "var(--text-muted, #94a3b8)", fontFamily: "monospace" }}>
          Loading ontology module…
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
      <Route path="/" element={<ServiceEntryPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/sso/callback" element={<SsoCallbackPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/cron" element={<CronPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/wiki" element={<WikiPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          {/* 旧本体对话入口收编到统一聊天页（quick 参数自动插入 @[instruct_ontology] 草稿）。
              必须放在 /ontology/* 通配前才能命中。 */}
          <Route
            path="/ontology/chat"
            element={<Navigate to="/chat?quick=instruct_ontology" replace />}
          />
          <Route path="/ontology/*" element={<OntologyRoute />} />
          <Route path="/integrator/mcp-servers" element={<McpServersPage />} />
          <Route path="/integrator/skillhub" element={<SkillHubPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/users" element={<UsersPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
