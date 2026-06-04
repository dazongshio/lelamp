import { useEffect } from "react";

export function usePolling(callback: () => void | Promise<void>, intervalMs: number, enabled = true) {
  useEffect(() => {
    if (!enabled) return undefined;
    const id = window.setInterval(() => {
      void callback();
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [callback, enabled, intervalMs]);
}
