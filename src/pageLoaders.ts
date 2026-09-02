import type { ComponentType } from "react";

type PageModule = Record<string, ComponentType>;
type PageLoader = () => Promise<PageModule>;

export const pageLoaders = {
  "/dashboard": () => import("./pages/DashboardPage"),
  "/shared": () => import("./pages/SharedSpacePage"),
  "/assistant": () => import("./pages/AssistantPage"),
  "/meeting": () => import("./pages/MeetingPage"),
  "/documents": () => import("./pages/DocumentsPage"),
  "/scan": () => import("./pages/ScanPage"),
  "/results": () => import("./pages/ResultCenterPage"),
  "/pot": () => import("./pages/PotPage"),
  "/wiki": () => import("./pages/WikiPage"),
  "/projection": () => import("./pages/ProjectionPage"),
  "/checklist": () => import("./pages/ProductChecklistPage"),
  "/validation": () => import("./pages/ValidationPage"),
  "/desktop": () => import("./pages/DesktopAutomationPage"),
  "/remote": () => import("./pages/RemoteComputerPage"),
  "/scene": () => import("./pages/ScenePage"),
  "/mobile": () => import("./pages/MobileBridgePage"),
  "/smart-home": () => import("./pages/SmartHomePage"),
  "/voice": () => import("./pages/VoicePage"),
  "/motors": () => import("./pages/MotorControlPage"),
  "/hardware": () => import("./pages/HardwarePage"),
  "/audit": () => import("./pages/AuditPage"),
  "/settings": () => import("./pages/SettingsPage"),
} satisfies Record<string, PageLoader>;

export type PagePath = keyof typeof pageLoaders;

export function preloadPage(path: string): void {
  const loader = pageLoaders[path as PagePath];
  if (loader) void loader();
}
