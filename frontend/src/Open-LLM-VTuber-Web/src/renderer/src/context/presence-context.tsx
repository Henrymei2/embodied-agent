import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLocalStorage } from "@/hooks/utils/use-local-storage";
import { wsService } from "@/services/websocket-service";

// How often coarse screen context is published for just-in-time help.
const SCREEN_CONTEXT_INTERVAL_MS = 5 * 60_000;

interface ForegroundWindow {
  app?: string;
  title?: string;
}

// Live desktop capture is intentionally optional: a plain browser cannot see
// other applications, so screen context is only published when the electron
// main process exposes a coarse foreground-window reader on window.api. Wiring
// a real reader (via an OS/native call) is the one remaining step for live
// desktop capture; the backend, tool, and dedup pipeline are already in place.
interface ScreenContextBridge {
  getActiveWindow?: () =>
    | ForegroundWindow
    | null
    | Promise<ForegroundWindow | null>;
}

async function readForegroundWindow(): Promise<ForegroundWindow | null> {
  const bridge = (window as { api?: ScreenContextBridge }).api;
  if (!bridge?.getActiveWindow) return null;
  try {
    const result = await bridge.getActiveWindow();
    if (!result) return null;
    const app = typeof result.app === "string" ? result.app.trim() : "";
    const title = typeof result.title === "string" ? result.title.trim() : "";
    if (!app && !title) return null;
    return { app: app || undefined, title: title || undefined };
  } catch {
    return null;
  }
}

export type PresenceFeedback = "helpful" | "dismissed" | "snoozed";

export interface PresenceReceipt {
  receipt_id: string;
  created_at: string;
  direction: string;
  action: "silent" | "ask" | "speak";
  score: number;
  attention_cost: number;
  reason: string;
  status: "queued" | "running" | "succeeded" | "failed" | "canceled";
  error: string | null;
  override_reason?: string | null;
  signals: {
    user_present: boolean;
    activity: "active" | "recently-active" | "idle" | "away" | "unknown";
    focus_mode: boolean;
    quiet_minutes: number | null;
    hour_of_day: number | null;
  };
  feedback: PresenceFeedback | null;
  feedback_at: string | null;
}

export interface PresenceState {
  type: "presence-state";
  enabled: boolean;
  mode?: "enforced" | "observe_only";
  attention_budget?: number;
  attention_budget_max?: number;
  attention_refill_per_hour?: number;
  snoozed?: boolean;
  snoozed_until?: string | null;
  direction_affinity?: Record<string, number>;
  receipts: PresenceReceipt[];
}

interface PresenceContextValue {
  state: PresenceState;
  focusMode: boolean;
  latestReceipt: PresenceReceipt | null;
  updateState: (state: PresenceState) => void;
  receiveReceipt: (receipt: PresenceReceipt, budgetRemaining?: number) => void;
  setFocusMode: (enabled: boolean) => void;
  sendFeedback: (
    receiptId: string,
    feedback: PresenceFeedback,
    snoozeMinutes?: number,
  ) => void;
}

const DEFAULT_STATE: PresenceState = {
  type: "presence-state",
  enabled: false,
  mode: "enforced",
  attention_budget: 0,
  attention_budget_max: 0,
  attention_refill_per_hour: 0,
  snoozed: false,
  snoozed_until: null,
  direction_affinity: {},
  receipts: [],
};

const PresenceContext = createContext<PresenceContextValue | null>(null);

export function PresenceProvider({
  children,
}: {
  children: ReactNode;
}): JSX.Element {
  const [state, setState] = useState<PresenceState>(DEFAULT_STATE);
  const [latestReceipt, setLatestReceipt] = useState<PresenceReceipt | null>(
    null,
  );
  const [focusModeValue, setFocusModeValue] = useLocalStorage<boolean>(
    "presenceFocusMode",
    false,
  );
  const lastActivityAt = useRef(Date.now());

  const publishPresence = useCallback(() => {
    if (wsService.getCurrentState() !== "OPEN") return;
    wsService.sendMessage({
      type: "presence-context",
      idle_seconds: Math.max(0, (Date.now() - lastActivityAt.current) / 1000),
      user_present: document.visibilityState === "visible",
      focus_mode: focusModeValue,
    });
  }, [focusModeValue]);

  const publishScreenContext = useCallback(async () => {
    if (wsService.getCurrentState() !== "OPEN") return;
    const foreground = await readForegroundWindow();
    if (!foreground) return; // browser/no-bridge: nothing coarse to report
    wsService.sendMessage({
      type: "screen-context",
      app: foreground.app,
      title: foreground.title,
      idle_seconds: Math.max(0, (Date.now() - lastActivityAt.current) / 1000),
    });
  }, []);

  useEffect(() => {
    const markActivity = () => {
      lastActivityAt.current = Date.now();
    };
    const handleVisibility = () => publishPresence();
    const events: (keyof WindowEventMap)[] = [
      "keydown",
      "pointerdown",
      "mousemove",
      "touchstart",
    ];
    events.forEach((eventName) =>
      window.addEventListener(eventName, markActivity),
    );
    document.addEventListener("visibilitychange", handleVisibility);

    const intervalId = window.setInterval(publishPresence, 60_000);
    const stateSubscription = wsService.onStateChange((wsState) => {
      if (wsState === "OPEN") {
        wsService.sendMessage({ type: "request-presence-state" });
        publishPresence();
      }
    });

    return () => {
      events.forEach((eventName) =>
        window.removeEventListener(eventName, markActivity),
      );
      document.removeEventListener("visibilitychange", handleVisibility);
      window.clearInterval(intervalId);
      stateSubscription.unsubscribe();
    };
  }, [publishPresence]);

  useEffect(() => {
    const publish = () => {
      void publishScreenContext();
    };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") publish();
    };
    const intervalId = window.setInterval(publish, SCREEN_CONTEXT_INTERVAL_MS);
    document.addEventListener("visibilitychange", handleVisibility);
    const stateSubscription = wsService.onStateChange((wsState) => {
      if (wsState === "OPEN") publish();
    });
    publish();

    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibility);
      stateSubscription.unsubscribe();
    };
  }, [publishScreenContext]);

  const updateState = useCallback((nextState: PresenceState) => {
    setState((current) => ({ ...current, ...nextState }));
    if (nextState.receipts.length > 0) {
      setLatestReceipt(nextState.receipts[0]);
    }
  }, []);

  const receiveReceipt = useCallback(
    (receipt: PresenceReceipt, budgetRemaining?: number) => {
      setLatestReceipt(receipt);
      setState((current) => ({
        ...current,
        attention_budget: budgetRemaining ?? current.attention_budget,
        receipts: [
          receipt,
          ...current.receipts.filter(
            (item) => item.receipt_id !== receipt.receipt_id,
          ),
        ],
      }));
    },
    [],
  );

  const setFocusMode = useCallback(
    (enabled: boolean) => {
      setFocusModeValue(enabled);
      if (wsService.getCurrentState() === "OPEN") {
        wsService.sendMessage({
          type: "presence-context",
          idle_seconds: Math.max(
            0,
            (Date.now() - lastActivityAt.current) / 1000,
          ),
          user_present: document.visibilityState === "visible",
          focus_mode: enabled,
        });
      }
    },
    [setFocusModeValue],
  );

  const sendFeedback = useCallback(
    (receiptId: string, feedback: PresenceFeedback, snoozeMinutes?: number) => {
      wsService.sendMessage({
        type: "autonomy-feedback",
        receipt_id: receiptId,
        feedback,
        ...(snoozeMinutes !== undefined && { snooze_minutes: snoozeMinutes }),
      });
    },
    [],
  );

  const value = useMemo(
    () => ({
      state,
      focusMode: focusModeValue,
      latestReceipt,
      updateState,
      receiveReceipt,
      setFocusMode,
      sendFeedback,
    }),
    [
      state,
      focusModeValue,
      latestReceipt,
      updateState,
      receiveReceipt,
      setFocusMode,
      sendFeedback,
    ],
  );

  return (
    <PresenceContext.Provider value={value}>
      {children}
    </PresenceContext.Provider>
  );
}

export function usePresence(): PresenceContextValue {
  const context = useContext(PresenceContext);
  if (!context) {
    throw new Error("usePresence must be used within a PresenceProvider");
  }
  return context;
}
