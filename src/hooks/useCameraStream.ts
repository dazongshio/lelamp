import { useCallback, useEffect, useRef, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getCameraStreamStatus, startCameraStream, stopCameraStream } from "../api/assistant";
import type { CameraStreamStatus } from "../api/types";

const AUTO_PREF_KEY = "lelamp_camera_stream_auto";
const AUTO_PREF_EVENT = "lelamp_camera_stream_auto_change";

type CameraStreamOptions = {
  cameraIndex?: number;
  width?: number;
  height?: number;
  backend?: string;
};

const CAMERA_STREAM_SETTLED_STATUSES = new Set(["online", "error", "failed", "blocked", "unavailable", "stopped"]);

function autoEnabledByDefault(): boolean {
  return localStorage.getItem(AUTO_PREF_KEY) !== "off";
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForCameraStreamReady(initial: CameraStreamStatus): Promise<CameraStreamStatus> {
  let current = initial;
  for (let attempt = 0; attempt < 12; attempt += 1) {
    if (CAMERA_STREAM_SETTLED_STATUSES.has(String(current.status || ""))) return current;
    await sleep(500);
    current = (await getCameraStreamStatus()).data;
  }
  return current;
}

export function useCameraStream(autoStart = false, options: CameraStreamOptions = {}) {
  const [status, setStatus] = useState<CameraStreamStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [autoEnabled, setAutoEnabled] = useState(autoEnabledByDefault);
  const [error, setError] = useState("");
  const startPromiseRef = useRef<Promise<CameraStreamStatus | null> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const result = await getCameraStreamStatus();
      setStatus(result.data);
      setError("");
      return result.data;
    } catch (err) {
      setError(apiErrorMessage(err));
      return null;
    }
  }, []);

  const start = useCallback(async (overrides: CameraStreamOptions = {}) => {
    if (startPromiseRef.current) return startPromiseRef.current;
    setLoading(true);
    const nextOptions = { ...options, ...overrides };
    const promise = startCameraStream({
      camera_index: nextOptions.cameraIndex,
      width: nextOptions.width,
      height: nextOptions.height,
      backend: nextOptions.backend,
    })
      .then(async (result) => {
        const nextStatus = await waitForCameraStreamReady(result.data);
        setStatus(nextStatus);
        setError("");
        return nextStatus;
      })
      .catch((err) => {
        setError(apiErrorMessage(err));
        return null;
      })
      .finally(() => {
        setLoading(false);
        startPromiseRef.current = null;
      });
    startPromiseRef.current = promise;
    return promise;
  }, [options.backend, options.cameraIndex, options.height, options.width]);

  const ensureStarted = useCallback(async () => {
    if (!autoEnabledByDefault()) {
      setAutoEnabled(false);
      return status;
    }
    if (!autoEnabled) return status;
    if (["online", "starting"].includes(String(status?.status || ""))) return status;
    return start();
  }, [autoEnabled, start, status]);

  const stop = useCallback(async () => {
    localStorage.setItem(AUTO_PREF_KEY, "off");
    setAutoEnabled(false);
    window.dispatchEvent(new Event(AUTO_PREF_EVENT));
    setLoading(true);
    try {
      const result = await stopCameraStream();
      setStatus(result.data);
      setError("");
      return result.data;
    } catch (err) {
      setError(apiErrorMessage(err));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const enableAuto = useCallback(async (overrides: CameraStreamOptions = {}) => {
    localStorage.setItem(AUTO_PREF_KEY, "on");
    setAutoEnabled(true);
    window.dispatchEvent(new Event(AUTO_PREF_EVENT));
    return start(overrides);
  }, [start]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const syncAutoPreference = () => {
      setAutoEnabled(autoEnabledByDefault());
      void refresh();
    };
    const syncStoragePreference = (event: StorageEvent) => {
      if (event.key === AUTO_PREF_KEY) syncAutoPreference();
    };
    window.addEventListener(AUTO_PREF_EVENT, syncAutoPreference);
    window.addEventListener("storage", syncStoragePreference);
    return () => {
      window.removeEventListener(AUTO_PREF_EVENT, syncAutoPreference);
      window.removeEventListener("storage", syncStoragePreference);
    };
  }, [refresh]);

  useEffect(() => {
    if (autoStart && autoEnabled) {
      void ensureStarted();
    }
  }, [autoEnabled, autoStart, ensureStarted]);

  return {
    status,
    loading,
    autoEnabled,
    error,
    isRunning: String(status?.status || "") === "online",
    previewUrl: status?.browser_preview_url || status?.preview_url || "",
    streamUrl: status?.browser_stream_url || status?.stream_url || "",
    snapshotUrl: status?.browser_snapshot_url || status?.snapshot_url || "",
    cameraIndex: status?.camera_index,
    refresh,
    ensureStarted,
    start: enableAuto,
    stop,
  };
}
