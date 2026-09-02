import { createBrowserRouter, Navigate, useLocation, useRouteError } from "react-router-dom";
import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from "react";
import { App } from "./App";
import { pageLoaders } from "./pageLoaders";

function page<T extends Record<string, ComponentType>>(loader: () => Promise<T>, name: keyof T) {
  return lazy(async () => ({ default: (await loader())[name] }));
}

const DashboardPage = page(pageLoaders["/dashboard"], "DashboardPage");
const SharedSpacePage = page(pageLoaders["/shared"], "SharedSpacePage");
const AssistantPage = page(pageLoaders["/assistant"], "AssistantPage");
const MeetingPage = page(pageLoaders["/meeting"], "MeetingPage");
const DocumentsPage = page(pageLoaders["/documents"], "DocumentsPage");
const ScanPage = page(pageLoaders["/scan"], "ScanPage");
const ResultCenterPage = page(pageLoaders["/results"], "ResultCenterPage");
const PotPage = page(pageLoaders["/pot"], "PotPage");
const WikiPage = page(pageLoaders["/wiki"], "WikiPage");
const ProjectionPage = page(pageLoaders["/projection"], "ProjectionPage");
const ProductChecklistPage = page(pageLoaders["/checklist"], "ProductChecklistPage");
const ValidationPage = page(pageLoaders["/validation"], "ValidationPage");
const DesktopAutomationPage = page(pageLoaders["/desktop"], "DesktopAutomationPage");
const RemoteComputerPage = page(pageLoaders["/remote"], "RemoteComputerPage");
const ScenePage = page(pageLoaders["/scene"], "ScenePage");
const MobileBridgePage = page(pageLoaders["/mobile"], "MobileBridgePage");
const SmartHomePage = page(pageLoaders["/smart-home"], "SmartHomePage");
const VoicePage = page(pageLoaders["/voice"], "VoicePage");
const HardwarePage = page(pageLoaders["/hardware"], "HardwarePage");
const MotorControlPage = page(pageLoaders["/motors"], "MotorControlPage");
const AuditPage = page(pageLoaders["/audit"], "AuditPage");
const SettingsPage = page(pageLoaders["/settings"], "SettingsPage");

function deferred(Page: LazyExoticComponent<ComponentType>) {
  return <Suspense fallback={<RouteLoading />}><Page /></Suspense>;
}

function RouteLoading() {
  return (
    <main className="route-loading" aria-busy="true" aria-live="polite">
      <span className="route-loading__title" />
      <span className="route-loading__summary" />
      <section className="route-loading__grid">
        <span /><span /><span />
      </section>
      <span className="sr-only">正在加载页面…</span>
    </main>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    errorElement: <ChineseRouteError />,
    children: [
      { index: true, element: <RedirectToDashboard /> },
      { path: "dashboard", element: deferred(DashboardPage) },
      { path: "shared", element: deferred(SharedSpacePage) },
      { path: "assistant", element: deferred(AssistantPage) },
      { path: "meeting", element: deferred(MeetingPage) },
      { path: "documents", element: deferred(DocumentsPage) },
      { path: "scan", element: deferred(ScanPage) },
      { path: "results", element: deferred(ResultCenterPage) },
      { path: "pot", element: deferred(PotPage) },
      { path: "wiki", element: deferred(WikiPage) },
      { path: "projection", element: deferred(ProjectionPage) },
      { path: "checklist", element: deferred(ProductChecklistPage) },
      { path: "validation", element: deferred(ValidationPage) },
      { path: "desktop", element: deferred(DesktopAutomationPage) },
      { path: "remote", element: deferred(RemoteComputerPage) },
      { path: "scene", element: deferred(ScenePage) },
      { path: "mobile", element: deferred(MobileBridgePage) },
      { path: "smart-home", element: deferred(SmartHomePage) },
      { path: "voice", element: deferred(VoicePage) },
      { path: "motors", element: deferred(MotorControlPage) },
      { path: "hardware", element: deferred(HardwarePage) },
      { path: "audit", element: deferred(AuditPage) },
      { path: "settings", element: deferred(SettingsPage) },
      { path: "*", element: <RedirectToDashboard /> },
    ],
  },
]);

function RedirectToDashboard() {
  const location = useLocation();
  return <Navigate to={{ pathname: "/dashboard", search: location.search }} replace />;
}

function ChineseRouteError() {
  const error = useRouteError();
  const detail = error instanceof Error ? error.message : "页面暂时无法加载。";
  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24, background: "#f5f5f7" }}>
      <section style={{ width: "min(520px, 100%)", padding: 32, borderRadius: 20, background: "#fff", boxShadow: "0 12px 40px rgba(0,0,0,.08)" }}>
        <h1 style={{ margin: "0 0 12px", fontSize: 24 }}>页面加载失败</h1>
        <p style={{ color: "#666", lineHeight: 1.7 }}>{detail}</p>
        <button type="button" onClick={() => window.location.reload()} style={{ marginTop: 12, padding: "10px 18px", border: 0, borderRadius: 10, color: "#fff", background: "#0071e3" }}>
          重新加载
        </button>
      </section>
    </main>
  );
}
