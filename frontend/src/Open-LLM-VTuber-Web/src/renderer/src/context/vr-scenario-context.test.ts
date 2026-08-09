import { describe, expect, it } from "vitest";
import {
  acceptVrScenarioState, VrScenarioStateMessage,
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
});
