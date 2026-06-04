import { Outlet } from "react-router-dom";
import { AppShell } from "./layout/AppShell";

export function App() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
