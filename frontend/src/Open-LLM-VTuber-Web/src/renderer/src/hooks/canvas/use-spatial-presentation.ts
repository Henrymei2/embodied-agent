import { useCallback, useEffect, useState } from "react";

export type SpatialCapability = "checking" | "webxr-ready" | "preview-only";

const spatialModeRequested = () => {
  const params = new URLSearchParams(window.location.search);
  return params.get("spatial") === "1" || params.get("presentation") === "vr";
};

const setSpatialQuery = (enabled: boolean) => {
  const url = new URL(window.location.href);
  if (enabled) url.searchParams.set("spatial", "1");
  else {
    url.searchParams.delete("spatial");
    if (url.searchParams.get("presentation") === "vr") {
      url.searchParams.delete("presentation");
    }
  }
  window.history.replaceState({}, "", url);
};

export function useSpatialPresentation() {
  const [enabled, setEnabled] = useState(spatialModeRequested);

  useEffect(() => {
    document.documentElement.dataset.spatialPresentation = enabled ? "true" : "false";
    return () => {
      delete document.documentElement.dataset.spatialPresentation;
    };
  }, [enabled]);

  const enter = useCallback(async () => {
    setEnabled(true);
    setSpatialQuery(true);
    try {
      if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
    } catch {
      // Headset browsers often own fullscreen themselves; the spatial layout
      // remains usable when the browser declines this optional enhancement.
    }
  }, []);

  const exit = useCallback(async () => {
    setEnabled(false);
    setSpatialQuery(false);
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
    } catch {
      // Exiting the spatial layout must never depend on fullscreen support.
    }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.fullscreenElement) void exit();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [enabled, exit]);

  return { enabled, enter, exit };
}
