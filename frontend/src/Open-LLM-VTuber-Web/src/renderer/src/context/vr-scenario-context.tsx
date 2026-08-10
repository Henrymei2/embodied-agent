import {
  createContext, useCallback, useContext, useMemo, useState,
} from "react";
import { wsService } from "@/services/websocket-service";

export interface VrScenarioDefinition {
  id: string;
  title: string;
  summary: string;
  description: string;
  setup: string[];
  sequence: string[];
  demonstrates: string[];
  simulated: boolean;
  reset_notice: string;
}

export type VrScenarioPhase =
  | "preparing" | "waiting_event" | "planning" | "running"
  | "awaiting_approval" | "completed" | "failed"
  | "cancelling" | "cancelled";

export interface VrScenarioRun {
  run_id: string;
  scenario_id: string;
  phase: VrScenarioPhase;
  progress_summary: string;
  started_at: string;
  updated_at: string;
  error?: string | null;
}

export interface VrScenarioStateMessage {
  version: 1;
  revision: number;
  scenarios: VrScenarioDefinition[];
  active_run: VrScenarioRun | null;
  error?: string;
}

export type VrScenarioMenuPage = "catalog" | "confirm" | "status";
export type VrScenarioMenuAction = "details" | "back" | "status" | "start" | "cancel";

export const initialVrScenarioMenuPage = (
  activeRun: VrScenarioRun | null,
): VrScenarioMenuPage => activeRun ? "status" : "catalog";

export const nextVrScenarioMenuPage = (
  current: VrScenarioMenuPage,
  action: VrScenarioMenuAction,
): VrScenarioMenuPage => {
  if (action === "details") return "confirm";
  if (action === "back") return "catalog";
  if (action === "status" || action === "start") return "status";
  return current;
};

interface VrScenarioValue extends VrScenarioStateMessage {
  receiveState: (message: VrScenarioStateMessage) => void;
  refresh: () => void;
  start: (scenarioId: string) => void;
  cancel: (runId: string) => void;
}

const VrScenarioContext = createContext<VrScenarioValue | null>(null);
const makeRequestId = () => globalThis.crypto?.randomUUID?.()
  ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;

export const acceptVrScenarioState = (
  current: VrScenarioStateMessage,
  message: VrScenarioStateMessage,
) => (
  message.version === 1
  && Array.isArray(message.scenarios)
  && message.revision >= current.revision
    ? message
    : current
);

export function VrScenarioProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<VrScenarioStateMessage>({ version: 1, revision: 0, scenarios: [], active_run: null });
  const receiveState = useCallback((message: VrScenarioStateMessage) => {
    setState((current) => acceptVrScenarioState(current, message));
  }, []);
  const refresh = useCallback(() => wsService.sendMessage({ type: "vr-scenario-get-state", version: 1 }), []);
  const start = useCallback((scenarioId: string) => wsService.sendMessage({
    type: "vr-scenario-start", version: 1, scenario_id: scenarioId, request_id: makeRequestId(),
  }), []);
  const cancel = useCallback((runId: string) => wsService.sendMessage({
    type: "vr-scenario-cancel", version: 1, run_id: runId, request_id: makeRequestId(),
  }), []);
  const value = useMemo(() => ({ ...state, receiveState, refresh, start, cancel }), [state, receiveState, refresh, start, cancel]);
  return <VrScenarioContext.Provider value={value}>{children}</VrScenarioContext.Provider>;
}

export function useVrScenario() {
  const value = useContext(VrScenarioContext);
  if (!value) throw new Error("useVrScenario must be used inside VrScenarioProvider");
  return value;
}
