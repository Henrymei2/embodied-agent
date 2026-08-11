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

interface AgentActivityContextValue {
  activity: AgentActivity;
  setActivity: (activity: AgentActivity) => void;
  clearActivity: () => void;
}

const IDLE_ACTIVITY: AgentActivity = { phase: "idle", tools: [] };

const AgentActivityContext = createContext<AgentActivityContextValue | null>(null);

export function AgentActivityProvider({ children }: { children: React.ReactNode }) {
  const [activity, setActivity] = useState<AgentActivity>(IDLE_ACTIVITY);
  const clearActivity = useCallback(() => setActivity(IDLE_ACTIVITY), []);
  const value = useMemo(() => ({
    activity,
    setActivity,
    clearActivity,
  }), [activity, clearActivity]);

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
