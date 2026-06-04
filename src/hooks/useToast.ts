import { useCallback, useState } from "react";
import { createId } from "../utils/id";

export interface ToastMessage {
  id: string;
  tone: "success" | "warning" | "danger" | "info";
  text: string;
}

export function useToast() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const push = useCallback((text: string, tone: ToastMessage["tone"] = "info") => {
    const id = createId("toast");
    setToasts((items) => [...items, { id, tone, text }]);
    window.setTimeout(() => {
      setToasts((items) => items.filter((item) => item.id !== id));
    }, 4500);
  }, []);

  return { toasts, push };
}
