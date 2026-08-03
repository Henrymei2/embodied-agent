import { describe, expect, it } from "vitest";
import {
  clampCaptionText,
  floorToScene,
  browserEndpoints,
  headRelativeDelta,
  isViewerPositionClear,
  pointInsideAabb,
  pressedEdge,
  rescaleThumbstick,
  selectDockingPoint,
  selectViewerSpawn,
  snapTurnStep,
  validateVrStageShape,
  withRendererMode,
} from "./vr-helpers";

describe("VR spatial helpers", () => {
  it("keeps captions within a compact headset-safe line budget", () => {
    expect(clampCaptionText("A concise spatial caption that stays below the companion", 20, 2)).toEqual([
      "A concise spatial",
      "caption that stays…",
    ]);
    expect(clampCaptionText("   ")).toEqual([]);
  });

  it("derives secure browser endpoints and registers renderer mode", () => {
    const endpoints = browserEndpoints({ protocol: "https:", host: "agent.lan:12393" });
    expect(endpoints).toEqual({
      baseUrl: "https://agent.lan:12393",
      wsUrl: "wss://agent.lan:12393/client-ws",
    });
    expect(withRendererMode(endpoints.wsUrl, "vr", endpoints.baseUrl)).toBe("wss://agent.lan:12393/client-ws?renderer_mode=vr");
  });

  it("rejects malformed or duplicate-node VR documents", () => {
    const avatar = { id: "avatar", type: "avatar", x: 10, y: 10, width: 10, depth: 10, height: 20, elevation: 10, color: "#fff" };
    const valid = {
      version: 1,
      mode: "vr",
      room: { width: 100, depth: 100, height: 100, floor_color: "#fff", wall_color: "#fff" },
      world: { units_per_meter: 100, reach_ceiling: 220, move_speed: 400 },
      nodes: [avatar],
      metadata: {},
    };
    expect(validateVrStageShape(valid)).toBe(true);
    expect(validateVrStageShape({ ...valid, nodes: [avatar, { ...avatar }] })).toBe(false);
  });

  it("converts centimetre floor coordinates around the room centre", () => {
    expect(floorToScene({ width: 960, depth: 540 }, { x: 580, y: 170 })).toEqual({ x: 1, z: 1 });
  });

  it("uses inclusive floor AABBs", () => {
    const box = { id: "box", x: 100, y: 100, width: 40, depth: 20 };
    expect(pointInsideAabb({ x: 80, y: 90 }, box)).toBe(true);
    expect(pointInsideAabb({ x: 79.9, y: 90 }, box)).toBe(false);
  });

  it("chooses a stable nearest docking candidate", () => {
    const target = { id: "desk", x: 200, y: 200, width: 100, depth: 60 };
    const avatar = { id: "avatar", x: 200, y: 50, width: 70, depth: 20 };
    expect(selectDockingPoint(target, avatar, [target], { width: 500, depth: 500 })).toEqual({ x: 200, y: 125 });
  });

  it("places the viewer at conversation distance without spawning in furniture", () => {
    const avatar = { id: "avatar", x: 35, y: 280, width: 70, depth: 20 };
    expect(selectViewerSpawn(avatar, [], { width: 960, depth: 540 })).toEqual({ x: 210, y: 280 });
    const blocker = { id: "chair", x: 210, y: 280, width: 80, depth: 80 };
    expect(selectViewerSpawn(avatar, [blocker], { width: 960, depth: 540 })).toEqual({ x: 35, y: 455 });
  });

  it("keeps headset locomotion outside furniture clearance zones", () => {
    const desk = { id: "desk", x: 200, y: 200, width: 100, depth: 60 };
    expect(isViewerPositionClear({ x: 300, y: 200 }, [desk], { width: 500, depth: 500 })).toBe(true);
    expect(isViewerPositionClear({ x: 270, y: 200 }, [desk], { width: 500, depth: 500 })).toBe(false);
    expect(isViewerPositionClear({ x: 10, y: 200 }, [], { width: 500, depth: 500 })).toBe(false);
  });
});

describe("Quest controller helpers", () => {
  it("applies and rescales the thumbstick dead zone", () => {
    expect(rescaleThumbstick(0.1, 0.1)).toEqual({ x: 0, y: 0 });
    expect(rescaleThumbstick(1, 0)).toEqual({ x: 1, y: 0 });
  });

  it("moves relative to horizontal headset facing", () => {
    expect(headRelativeDelta({ x: 0, y: 1 }, { x: 0, z: -1 }, 1.5, 2)).toEqual({ x: 0, z: -3 });
  });

  it("requires rearming between 30 degree snap turns", () => {
    const turn = snapTurnStep(0.8, true);
    expect(turn.delta).toBeCloseTo(-Math.PI / 6);
    expect(snapTurnStep(0.8, turn.armed).delta).toBe(0);
    expect(snapTurnStep(0.2, turn.armed).armed).toBe(true);
  });

  it("detects only a button press edge", () => {
    expect(pressedEdge(true, false)).toBe(true);
    expect(pressedEdge(true, true)).toBe(false);
    expect(pressedEdge(false, true)).toBe(false);
  });
});
