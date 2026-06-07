import { createBrowserRouter, Navigate, useLocation } from "react-router-dom";
import { App } from "./App";
import { DashboardPage } from "./pages/DashboardPage";
import { SharedSpacePage } from "./pages/SharedSpacePage";
import { AssistantPage } from "./pages/AssistantPage";
import { MeetingPage } from "./pages/MeetingPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { WikiPage } from "./pages/WikiPage";
import { ProjectionPage } from "./pages/ProjectionPage";
import { ProductChecklistPage } from "./pages/ProductChecklistPage";
import { ValidationPage } from "./pages/ValidationPage";
import { DesktopAutomationPage } from "./pages/DesktopAutomationPage";
import { ScenePage } from "./pages/ScenePage";
import { MobileBridgePage } from "./pages/MobileBridgePage";
import { SmartHomePage } from "./pages/SmartHomePage";
import { VoicePage } from "./pages/VoicePage";
import { HardwarePage } from "./pages/HardwarePage";
import { MotorControlPage } from "./pages/MotorControlPage";
import { AuditPage } from "./pages/AuditPage";
import { SettingsPage } from "./pages/SettingsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <RedirectToDashboard /> },
      { path: "dashboard", element: <DashboardPage /> },
      { path: "shared", element: <SharedSpacePage /> },
      { path: "assistant", element: <AssistantPage /> },
      { path: "meeting", element: <MeetingPage /> },
      { path: "documents", element: <DocumentsPage /> },
      { path: "wiki", element: <WikiPage /> },
      { path: "projection", element: <ProjectionPage /> },
      { path: "checklist", element: <ProductChecklistPage /> },
      { path: "validation", element: <ValidationPage /> },
      { path: "desktop", element: <DesktopAutomationPage /> },
      { path: "scene", element: <ScenePage /> },
      { path: "mobile", element: <MobileBridgePage /> },
      { path: "smart-home", element: <SmartHomePage /> },
      { path: "voice", element: <VoicePage /> },
      { path: "motors", element: <MotorControlPage /> },
      { path: "hardware", element: <HardwarePage /> },
      { path: "audit", element: <AuditPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <RedirectToDashboard /> },
    ],
  },
]);

function RedirectToDashboard() {
  const location = useLocation();
  return <Navigate to={{ pathname: "/dashboard", search: location.search }} replace />;
}
