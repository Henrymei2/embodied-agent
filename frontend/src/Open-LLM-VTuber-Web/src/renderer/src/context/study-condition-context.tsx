import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

/**
 * Which pillars of the companion are switched on for this session.
 *
 * The backend decides — see `src/open_llm_vtuber/study.py` — and announces it
 * on connection. The frontend's only job is to honor the embodiment ablation
 * by not drawing the room, because a hidden pillar that is still visible is
 * not an ablation.
 *
 * Everything is on unless the backend says otherwise, so ordinary use is
 * untouched by any of this.
 */
export interface StudyCondition {
  label: string;
  embodiment: boolean;
  memory: boolean;
  autonomy: boolean;
}

const FULL: StudyCondition = {
  label: "full",
  embodiment: true,
  memory: true,
  autonomy: true,
};

interface StudyConditionContextValue {
  condition: StudyCondition;
  setConditionFromServer: (message: unknown) => void;
}

const StudyConditionContext = createContext<StudyConditionContextValue>({
  condition: FULL,
  setConditionFromServer: () => undefined,
});

const readFlag = (value: unknown, fallback: boolean) => (
  typeof value === "boolean" ? value : fallback
);

export function StudyConditionProvider({ children }: { children: React.ReactNode }) {
  const [condition, setCondition] = useState<StudyCondition>(FULL);

  const setConditionFromServer = useCallback((message: unknown) => {
    if (typeof message !== "object" || message === null) return;
    const payload = message as Record<string, unknown>;
    setCondition({
      label: typeof payload.condition === "string" ? payload.condition : "full",
      embodiment: readFlag(payload.embodiment, true),
      memory: readFlag(payload.memory, true),
      autonomy: readFlag(payload.autonomy, true),
    });
  }, []);

  const value = useMemo(
    () => ({ condition, setConditionFromServer }),
    [condition, setConditionFromServer],
  );

  return (
    <StudyConditionContext.Provider value={value}>
      {children}
    </StudyConditionContext.Provider>
  );
}

export function useStudyCondition() {
  return useContext(StudyConditionContext);
}
