import {
  createContext, useCallback, useContext, useMemo, useState,
} from "react";

export type AgentActivityPhase = "thinking" | "planning" | "plan" | "tools" | "idle";
export type AgentToolStatus = "pending" | "running" | "completed" | "error";

export interface AgentActivityTool {
  tool_id: string;
  tool_name: string;
  status: AgentToolStatus;
}

export interface AgentActivity {
  phase: AgentActivityPhase;
  tools: AgentActivityTool[];
  /** Optional intention/plan title shown for the "plan" phase. */
  label?: string;
}

export interface AgentIntention {
  label: string;
}

export interface AgentActivityState {
  activity: AgentActivity;
  intention: AgentIntention | null;
}

interface AgentActivityContextValue {
  activity: AgentActivity;
  intention: AgentIntention | null;
  applyActivity: (activity: AgentActivity) => void;
  clearActivity: () => void;
}

const IDLE_ACTIVITY: AgentActivity = { phase: "idle", tools: [] };
const EMPTY_ACTIVITY_STATE: AgentActivityState = {
  activity: IDLE_ACTIVITY,
  intention: null,
};

/** Keep the autonomy intention alive while lower-level activity frames change. */
export function reduceAgentActivityState(
  state: AgentActivityState,
  frame: AgentActivity,
): AgentActivityState {
  if (frame.phase === "plan") {
    return {
      activity: IDLE_ACTIVITY,
      intention: { label: String(frame.label || "").trim() },
    };
  }
  if (frame.phase === "idle") return EMPTY_ACTIVITY_STATE;
  return { activity: frame, intention: state.intention };
}

const AgentActivityContext = createContext<AgentActivityContextValue | null>(null);

export function AgentActivityProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AgentActivityState>(EMPTY_ACTIVITY_STATE);
  const applyActivity = useCallback((activity: AgentActivity) => {
    setState((current) => reduceAgentActivityState(current, activity));
  }, []);
  const clearActivity = useCallback(() => setState(EMPTY_ACTIVITY_STATE), []);
  const value = useMemo(() => ({
    activity: state.activity,
    intention: state.intention,
    applyActivity,
    clearActivity,
  }), [state, applyActivity, clearActivity]);

  return (
    <AgentActivityContext.Provider value={value}>
      {children}
    </AgentActivityContext.Provider>
  );
}

export function useAgentActivity() {
  const context = useContext(AgentActivityContext);
  if (!context) {
    throw new Error("useAgentActivity must be used within AgentActivityProvider");
  }
  return context;
}
