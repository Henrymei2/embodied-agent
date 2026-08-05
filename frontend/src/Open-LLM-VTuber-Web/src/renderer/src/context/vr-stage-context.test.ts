import { describe, expect, it } from "vitest";
import {
  endpointError,
  parseVrMovePath,
  successfulVrMoveResult,
  type VrStageDocument,
} from "./vr-stage-context";

const stage: VrStageDocument = {
  version: 1,
  mode: "vr",
  room: { width: 500, depth: 500, height: 300, floor_color: "#fff", wall_color: "#fff" },
  world: { units_per_meter: 100, reach_ceiling: 220, move_speed: 400 },
  nodes: [
    { id: "desk", type: "furniture", x: 250, y: 250, width: 100, depth: 80, height: 80, elevation: 40, color: "#fff" },
    { id: "avatar", type: "avatar", x: 80, y: 80, width: 40, depth: 40, height: 160, elevation: 80, color: "#fff" },
  ],
  metadata: {},
};

describe("VR frontend move action", () => {
  it("prefers valid waypoints and drops malformed points", () => {
    expect(parseVrMovePath({
      x: 10,
      y: 20,
      waypoints: [{ x: 100, y: 110 }, { x: "bad", y: 2 }, null],
    })).toEqual([{ x: 100, y: 110 }]);
  });

  it("rejects endpoints inside furniture", () => {
    expect(endpointError(stage, 250, 250)).toContain("inside desk");
    expect(endpointError(stage, 150, 150)).toBeNull();
  });

  it("returns the verified arrival contract", () => {
    expect(successfulVrMoveResult("request-1", stage, 500, { x: 150, y: 150 })).toMatchObject({
      request_id: "request-1",
      action: "move_to",
      ok: true,
      arrived: true,
      duration_ms: 500,
      position: { x: 150, y: 150 },
      state: stage,
    });
  });
});
