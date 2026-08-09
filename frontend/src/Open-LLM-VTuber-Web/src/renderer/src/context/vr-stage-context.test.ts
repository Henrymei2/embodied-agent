import { describe, expect, it } from "vitest";
import {
  endpointError,
  createVrMotionPlan,
  parseVrMovePath,
  successfulVrMoveResult,
  type VrStageDocument,
} from "./vr-stage-context";

const stage: VrStageDocument = {
  version: 3,
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
  it("accounts for turn, half-speed walking, and optional final facing", () => {
    const ordinary = createVrMotionPlan(1, { x: 0, y: 0 }, { x: 400, y: 0 }, 200);
    expect(ordinary).toMatchObject({
      sequence: 1,
      turnDurationMs: 600,
      walkDurationMs: 2000,
      finalTurnDurationMs: 0,
      totalDurationMs: 2600,
    });

    const towardUser = createVrMotionPlan(
      2, { x: 0, y: 0 }, { x: 400, y: 0 }, 200, { x: 500, y: 0 },
    );
    expect(towardUser.finalFacePoint).toEqual({ x: 500, y: 0 });
    expect(towardUser.totalDurationMs).toBe(3200);

    const towardFurniture = createVrMotionPlan(
      3, { x: 0, y: 0 }, { x: 400, y: 0 }, 200, { x: 400, y: 100 },
    );
    expect(towardFurniture.finalFacePoint).toEqual({ x: 400, y: 100 });
    expect(towardFurniture.finalTurnDurationMs).toBe(600);
  });

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
