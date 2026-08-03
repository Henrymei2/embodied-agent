import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";

export type RendererMode = "detecting" | "choosing" | "normal" | "vr-starting" | "vr";

interface RendererModeValue {
  mode: RendererMode;
  xrSession: XRSession | null;
  xrSupported: boolean | null;
  error: string | null;
  enterVr: () => Promise<void>;
  useNormal: () => void;
  leaveVr: () => Promise<void>;
}

const RendererModeContext = createContext<RendererModeValue | null>(null);

export function RendererModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<RendererMode>(window.api ? "normal" : "detecting");
  const [xrSession, setXrSession] = useState<XRSession | null>(null);
  const [xrSupported, setXrSupported] = useState<boolean | null>(window.api ? false : null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (window.api) return undefined;
    let live = true;
    const detect = async () => {
      try {
        const supported = Boolean(
          navigator.xr && await navigator.xr.isSessionSupported("immersive-vr"),
        );
        if (live) {
          setXrSupported(supported);
          setMode(supported ? "choosing" : "normal");
        }
      } catch (_error) {
        if (live) {
          setXrSupported(false);
          setMode("normal");
        }
      }
    };
    void detect();
    return () => { live = false; };
  }, []);

  const enterVr = useCallback(async () => {
    setError(null);
    setMode("vr-starting");
    try {
      if (!navigator.xr) throw new Error("WebXR is unavailable in this browser.");
      const session = await navigator.xr.requestSession("immersive-vr", {
        requiredFeatures: ["local-floor"],
        optionalFeatures: ["bounded-floor", "hand-tracking"],
      });
      session.addEventListener("end", () => {
        setXrSession(null);
        setMode("normal");
      }, { once: true });
      setXrSession(session);
      setXrSupported(true);
      setMode("vr");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setMode(xrSupported ? "choosing" : "normal");
    }
  }, [xrSupported]);

  const useNormal = useCallback(() => {
    setError(null);
    setMode("normal");
  }, []);

  const leaveVr = useCallback(async () => {
    if (xrSession) await xrSession.end();
  }, [xrSession]);

  const value = useMemo(() => ({
    mode, xrSession, xrSupported, error, enterVr, useNormal, leaveVr,
  }), [mode, xrSession, xrSupported, error, enterVr, useNormal, leaveVr]);

  return (
    <RendererModeContext.Provider value={value}>
      {children}
    </RendererModeContext.Provider>
  );
}

export function useRendererMode() {
  const value = useContext(RendererModeContext);
  if (!value) throw new Error("useRendererMode must be used inside RendererModeProvider");
  return value;
}

