import { describe, expect, it } from "vitest";
import {
  acceptVrScenarioState,
  initialVrScenarioMenuPage,
  nextVrScenarioIndex,
  nextVrScenarioMenuPage,
  VrScenarioRun,
  VrScenarioStateMessage,
} from "./vr-scenario-context";

const state = (revision: number): VrScenarioStateMessage => ({
  version: 1,
  revision,
  scenarios: [],
  active_run: null,
});

describe("VR scenario protocol state", () => {
  it("ignores an out-of-order snapshot", () => {
    const current = state(8);
    expect(acceptVrScenarioState(current, state(7))).toBe(current);
  });

  it("accepts a duplicate or newer idempotent snapshot", () => {
    expect(acceptVrScenarioState(state(8), state(8)).revision).toBe(8);
    expect(acceptVrScenarioState(state(8), state(9)).revision).toBe(9);
  });

  it("opens an existing run on status and can return to the catalogue", () => {
    const run: VrScenarioRun = {
      run_id: "run-1",
      scenario_id: "email-changes-agent-plan",
      phase: "completed",
      progress_summary: "Finished",
      started_at: "2026-08-09T12:00:00Z",
      updated_at: "2026-08-09T12:01:00Z",
    };
    expect(initialVrScenarioMenuPage(run)).toBe("status");
    expect(nextVrScenarioMenuPage("status", "back")).toBe("catalog");
  });

  it("moves from catalogue to confirmation and then status after start", () => {
    expect(initialVrScenarioMenuPage(null)).toBe("catalog");
    expect(nextVrScenarioMenuPage("catalog", "details")).toBe("confirm");
    expect(nextVrScenarioMenuPage("confirm", "start")).toBe("status");
  });

  it("cycles through every scenario in either direction", () => {
    expect(nextVrScenarioIndex(0, 3, "next")).toBe(1);
    expect(nextVrScenarioIndex(1, 3, "next")).toBe(2);
    expect(nextVrScenarioIndex(2, 3, "next")).toBe(0);
    expect(nextVrScenarioIndex(0, 3, "previous")).toBe(2);
    expect(nextVrScenarioIndex(0, 0, "next")).toBe(0);
    expect(nextVrScenarioMenuPage("catalog", "next")).toBe("catalog");
  });
});
