import { useCallback, useEffect, useRef, useState } from "react";
import { apiErrorMessage } from "../api/client";
import { getCameraStreamStatus, startCameraStream, stopCameraStream } from "../api/assistant";
import type { CameraStreamStatus } from "../api/types";

const AUTO_PREF_KEY = "lelamp_camera_stream_auto";
const AUTO_PREF_EVENT = "lelamp_camera_stream_auto_change";

function autoEnabledByDefault(): boolean {
  return localStorage.getItem(AUTO_PREF_KEY) !== "off";
}

export function useCameraStream(autoStart = false, options: {
  cameraIndex?: number;
  width?: number;
  height?: number;
  backend?: string;
} = {}) {
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

  const start = useCallback(async () => {
    if (startPromiseRef.current) return startPromiseRef.current;
    setLoading(true);
    const promise = startCameraStream({
      camera_index: options.cameraIndex,
      width: options.width,
      height: options.height,
      backend: options.backend,
    })
      .then((result) => {
        setStatus(result.data);
        setError("");
        return result.data;
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

  const enableAuto = useCallback(async () => {
    localStorage.setItem(AUTO_PREF_KEY, "on");
    setAutoEnabled(true);
    window.dispatchEvent(new Event(AUTO_PREF_EVENT));
    return start();
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
