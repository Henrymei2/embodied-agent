import { useEffect, useCallback } from "react";
import { useInterrupt } from "@/hooks/utils/use-interrupt";
import { useMicToggle } from "./use-mic-toggle";
import { useForceIgnoreMouse } from "@/hooks/utils/use-force-ignore-mouse";
import { useMode } from "@/context/mode-context";

export function useIpcHandlers() {
  const { handleMicToggle } = useMicToggle();
  const { interrupt } = useInterrupt();
  const { setForceIgnoreMouse } = useForceIgnoreMouse();
  const { mode } = useMode();
  const isPet = mode === 'pet';

  const micToggleHandler = useCallback(() => {
    handleMicToggle();
  }, [handleMicToggle]);

  const interruptHandler = useCallback(() => {
    interrupt();
  }, [interrupt]);

  // Handler for force ignore mouse state changes from main process
  const forceIgnoreMouseChangedHandler = useCallback(
    (_event: Electron.IpcRendererEvent, isForced: boolean) => {
      console.log("Force ignore mouse changed:", isForced);
      setForceIgnoreMouse(isForced);
    },
    [setForceIgnoreMouse],
  );

  // Handle toggle force ignore mouse from menu
  const toggleForceIgnoreMouseHandler = useCallback(() => {
    (window.api as any).toggleForceIgnoreMouse();
  }, []);

  useEffect(() => {
    if (!window.electron?.ipcRenderer) return;
    if (!isPet) return;

    window.electron.ipcRenderer.removeAllListeners("mic-toggle");
    window.electron.ipcRenderer.removeAllListeners("interrupt");
    window.electron.ipcRenderer.removeAllListeners("toggle-force-ignore-mouse");
    window.electron.ipcRenderer.removeAllListeners("force-ignore-mouse-changed");

    window.electron.ipcRenderer.on("mic-toggle", micToggleHandler);
    window.electron.ipcRenderer.on("interrupt", interruptHandler);
    window.electron.ipcRenderer.on(
      "toggle-force-ignore-mouse",
      toggleForceIgnoreMouseHandler,
    );
    window.electron.ipcRenderer.on(
      "force-ignore-mouse-changed",
      forceIgnoreMouseChangedHandler,
    );

    return () => {
      window.electron?.ipcRenderer.removeAllListeners("mic-toggle");
      window.electron?.ipcRenderer.removeAllListeners("interrupt");
      window.electron?.ipcRenderer.removeAllListeners("toggle-force-ignore-mouse");
      window.electron?.ipcRenderer.removeAllListeners("force-ignore-mouse-changed");
    };
  }, [
    micToggleHandler,
    interruptHandler,
    toggleForceIgnoreMouseHandler,
    forceIgnoreMouseChangedHandler,
    isPet,
  ]);
}
