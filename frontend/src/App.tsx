import { lazy, Suspense } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/app-shell";
import { RequireAdmin } from "@/components/layout/require-admin";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/sonner";
import { useMe } from "@/hooks/use-me";

const FleetStatusPage   = lazy(() => import("@/pages/fleet-status"));
const AgentChainsPage   = lazy(() => import("@/pages/agent-chains"));
const ThreatsAlertsPage = lazy(() => import("@/pages/threats-alerts"));
const NetworkMapPage    = lazy(() => import("@/pages/network-map"));
const UsageCostPage     = lazy(() => import("@/pages/usage-cost"));
const SessionsPage      = lazy(() => import("@/pages/sessions"));
const AuditLogPage      = lazy(() => import("@/pages/audit-log"));
const CompliancePage    = lazy(() => import("@/pages/compliance"));
const AuditApiPage      = lazy(() => import("@/pages/audit-api"));
const AgentDetailPage   = lazy(() => import("@/pages/agent-detail"));
const AgentsListPage    = lazy(() => import("@/pages/agents-list"));
const HostDetailPage    = lazy(() => import("@/pages/host-detail"));
const HostsListPage     = lazy(() => import("@/pages/hosts-list"));
const AgentActivityPage = lazy(() => import("@/pages/agent-activity"));
const UsersPage         = lazy(() => import("@/pages/users"));
const SettingsPage      = lazy(() => import("@/pages/settings"));
const McpServersPage    = lazy(() => import("@/pages/mcp-servers"));
const AdminActionsPage  = lazy(() => import("@/pages/admin-actions"));
const PoliciesPage      = lazy(() => import("@/pages/policies"));
const DlpRulesPage      = lazy(() => import("@/pages/dlp-rules"));
const ProfilePage       = lazy(() => import("@/pages/profile"));
const RoutingLayout     = lazy(() => import("@/pages/routing"));
const LlmRoutingPage    = lazy(() => import("@/pages/llm-routing"));
const LabelsPage        = lazy(() => import("@/pages/labels"));
const SettingsOverview  = lazy(() => import("@/pages/settings/overview"));
const SettingsRuntime   = lazy(() => import("@/pages/settings/runtime"));
const SettingsEmail     = lazy(() => import("@/pages/settings/email"));
const SettingsSigning   = lazy(() => import("@/pages/settings/signing"));
const SettingsLedger    = lazy(() => import("@/pages/settings/ledger"));
const NotFoundPage      = lazy(() => import("@/pages/not-found"));

function PageFallback() {
  return (
    <div className="space-y-4 p-2">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-4 w-64" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

function w(el: React.ReactNode) {
  return <Suspense fallback={<PageFallback />}>{el}</Suspense>;
}

function RoleRedirect() {
  const { isAdmin, isAuditor, isLoading } = useMe();
  if (isLoading) return <PageFallback />;
  if (isAdmin) return <Navigate to="/workforce-status" replace />;
  if (isAuditor) return <Navigate to="/threats-alerts" replace />;
  return <Navigate to="/threats-alerts" replace />;
}

function RequireAuditor({ children }: { children: React.ReactNode }) {
  const { isAuditor, isAdmin, isLoading } = useMe();
  if (isLoading) return <PageFallback />;
  if (!isAuditor && !isAdmin) return <Navigate to="/" replace />;
  return <>{children}</>;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, staleTime: 30_000 },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
          <Suspense fallback={null}>
            <Routes>
              <Route element={<AppShell />}>
                <Route index element={<RoleRedirect />} />
                {/* Shared: Admin + Auditor */}
                <Route path="agent-chains"   element={w(<AgentChainsPage />)} />
                <Route path="threats-alerts" element={w(<ThreatsAlertsPage />)} />
                {/* Email alert links land here — same page, but with the
                    alert preselected and its detail sheet open. */}
                <Route path="alerts/:alertId" element={w(<ThreatsAlertsPage />)} />
                {/* Admin-only */}
                {/* Shared with auditors (read-only). Writes stay admin-gated in
                    the pages + API; RequireAuditor admits admins too. */}
                <Route path="workforce-status" element={<RequireAuditor>{w(<FleetStatusPage />)}</RequireAuditor>} />
                <Route path="network-map"    element={<RequireAuditor>{w(<NetworkMapPage />)}</RequireAuditor>} />
                <Route path="usage-cost"     element={<RequireAuditor>{w(<UsageCostPage />)}</RequireAuditor>} />
                {/* Routing shell: one entry, LLM Providers + MCP Servers as
                    sub-tabs (same pattern as Settings). */}
                <Route path="routing"        element={<RequireAuditor>{w(<RoutingLayout />)}</RequireAuditor>}>
                  <Route index               element={w(<LlmRoutingPage />)} />
                  <Route path="mcp-servers"  element={w(<McpServersPage />)} />
                </Route>
                <Route path="labels"         element={<RequireAuditor>{w(<LabelsPage />)}</RequireAuditor>} />
                <Route path="policies"       element={<RequireAuditor>{w(<PoliciesPage />)}</RequireAuditor>} />
                {/* Admin-only */}
                <Route path="dlp-rules"      element={<RequireAdmin>{w(<DlpRulesPage />)}</RequireAdmin>} />
                <Route path="users"          element={<RequireAdmin>{w(<UsersPage />)}</RequireAdmin>} />
                {/* Settings is a layout shell with a left rail; each child
                    route renders one focused panel via <Outlet />. */}
                <Route path="settings"       element={<RequireAdmin>{w(<SettingsPage />)}</RequireAdmin>}>
                  <Route index               element={w(<SettingsOverview />)} />
                  <Route path="runtime"      element={w(<SettingsRuntime />)} />
                  <Route path="email"        element={w(<SettingsEmail />)} />
                  <Route path="signing"      element={w(<SettingsSigning />)} />
                  <Route path="ledger"       element={w(<SettingsLedger />)} />
                  <Route path="admin-actions" element={w(<AdminActionsPage />)} />
                </Route>
                {/* Auditor-only */}
                <Route path="sessions"            element={<RequireAuditor>{w(<SessionsPage />)}</RequireAuditor>} />
                <Route path="sessions/:sessionId" element={<RequireAuditor>{w(<SessionsPage />)}</RequireAuditor>} />
                <Route path="audit-log"           element={<RequireAuditor>{w(<AuditLogPage />)}</RequireAuditor>} />
                <Route path="compliance"          element={<RequireAuditor>{w(<CompliancePage />)}</RequireAuditor>} />
                <Route path="compliance/api-docs" element={<RequireAuditor>{w(<AuditApiPage />)}</RequireAuditor>} />
                <Route path="agent-activity"      element={<RequireAuditor>{w(<AgentActivityPage />)}</RequireAuditor>} />
                {/* Entity list + detail routes. The list pages are
                    top-level browseable surfaces; detail pages are
                    deep-linkable views for one agent / one host. */}
                <Route path="agents"              element={<RequireAuditor>{w(<AgentsListPage />)}</RequireAuditor>} />
                <Route path="agents/:agentId"     element={<RequireAuditor>{w(<AgentDetailPage />)}</RequireAuditor>} />
                <Route path="hosts"               element={<RequireAuditor>{w(<HostsListPage />)}</RequireAuditor>} />
                <Route path="hosts/:identifier"   element={<RequireAuditor>{w(<HostDetailPage />)}</RequireAuditor>} />
                {/* Both roles */}
                <Route path="profile" element={w(<ProfilePage />)} />
                {/* Redirects from old URLs */}
                <Route path="overview"         element={<Navigate to="/workforce-status" replace />} />
                {/* Old slug → keep existing bookmarks / email links working. */}
                <Route path="fleet-status"     element={<Navigate to="/workforce-status" replace />} />
                <Route path="integrity"        element={<Navigate to="/compliance"     replace />} />
                <Route path="timeline"         element={<Navigate to="/audit-log"      replace />} />
                <Route path="tokens"           element={<Navigate to="/usage-cost"     replace />} />
                <Route path="agent-topology"   element={<Navigate to="/network-map"    replace />} />
                <Route path="agent-topology/*" element={<Navigate to="/network-map"    replace />} />
                <Route path="dlp"              element={<Navigate to="/threats-alerts" replace />} />
                <Route path="admin-actions"    element={<Navigate to="/settings/admin-actions" replace />} />
                <Route path="ai-providers"     element={<Navigate to="/routing" replace />} />
                <Route path="llm-routing"      element={<Navigate to="/routing" replace />} />
                <Route path="mcp-servers"      element={<Navigate to="/routing/mcp-servers" replace />} />
                <Route path="*"                element={w(<NotFoundPage />)} />
              </Route>
            </Routes>
          </Suspense>
        </BrowserRouter>
        <Toaster position="top-right" richColors />
    </QueryClientProvider>
  );
}
