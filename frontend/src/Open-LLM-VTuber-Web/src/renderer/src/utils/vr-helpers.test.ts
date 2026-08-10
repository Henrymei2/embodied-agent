import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  clampCaptionText,
  floorToScene,
  furnitureInteractionReach,
  furnitureCenterSceneY,
  furnitureDecalLayouts,
  furniturePanelLayout,
  browserEndpoints,
  headRelativeDelta,
  inputSourcesByHand,
  isViewerPositionClear,
  pointInsideAabb,
  isRayGrabbableNode,
  nextEmailPage,
  parseVrSelectionHighlight,
  pressedEdge,
  questLocomotionStick,
  rescaleThumbstick,
  selectVrRayHit,
  selectDockingPoint,
  selectViewerSpawn,
  snapTurnStep,
  transientTriggerBehavior,
  updateFurniturePanelProximity,
  validateVrStageShape,
  viewerForwardFromPose,
  viewerPoseCamera,
  viewerWorldPositionFromPose,
  withRendererMode,
} from "./vr-helpers";

describe("VR spatial helpers", () => {
  it("defaults VR selection highlighting on and parses a persisted opt-out", () => {
    expect(parseVrSelectionHighlight(null)).toBe(true);
    expect(parseVrSelectionHighlight("true")).toBe(true);
    expect(parseVrSelectionHighlight("false")).toBe(false);
    expect(parseVrSelectionHighlight("not-json")).toBe(true);
  });

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
      version: 3,
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
  const hit = (distance: number, x: number, z: number, kind: string) => ({
    distance,
    point: { x, y: 1, z },
    kind,
  });

  it("accepts only nearest hits in the active pointing ray's forward hemisphere", () => {
    const viewer = { x: 0, y: 1.6, z: 0 };
    const forward = { x: 0, y: -0.4, z: -1 };

    expect(selectVrRayHit([hit(2, 0, -2, "front")], viewer, forward)?.kind).toBe("front");
    expect(selectVrRayHit([hit(2, 0, 2, "behind")], viewer, forward)).toBeNull();
    expect(selectVrRayHit([hit(2, 2, 0, "side")], viewer, forward)?.kind).toBe("side");
  });

  it("keeps selection valid after the user turns around", () => {
    const controller = { x: 0, y: 1.4, z: 0 };
    expect(selectVrRayHit(
      [hit(2, 0, 2, "opposite-wall")],
      controller,
      { x: 0, y: 0, z: 1 },
    )?.kind).toBe("opposite-wall");
  });

  it("identifies only explicitly grabbable props as ray-grabbable", () => {
    expect(isRayGrabbableNode({ type: "prop", interaction: { grabbable: true } })).toBe(true);
    expect(isRayGrabbableNode({ type: "furniture", interaction: { grabbable: true } })).toBe(false);
  });

  it("limits selection to three metres", () => {
    const viewer = { x: 0, y: 1.6, z: 0 };
    const forward = { x: 0, y: 0, z: -1 };

    expect(selectVrRayHit([hit(3, 0, -3, "edge")], viewer, forward)?.kind).toBe("edge");
    expect(selectVrRayHit([hit(3.001, 0, -3.001, "far")], viewer, forward)).toBeNull();
  });

  it("never skips a nearer physical surface for a farther interaction", () => {
    const viewer = { x: 0, y: 1.6, z: 0 };
    const forward = { x: 0, y: 0, z: -1 };
    const occluder = hit(1, 0, -1, "occluder");
    const furniture = hit(2, 0, -2, "furniture");
    const panelAction = hit(2.5, 0, -2.5, "panel-action");

    expect(selectVrRayHit([panelAction, furniture, occluder], viewer, forward)?.kind).toBe("occluder");
    expect(selectVrRayHit([panelAction, furniture], viewer, forward)?.kind).toBe("furniture");
  });

  it("reads live headset pose from an eye camera instead of the outer XR rig", () => {
    const eye = new THREE.PerspectiveCamera();
    const xrCamera = new THREE.ArrayCamera([eye]);
    expect(viewerPoseCamera(xrCamera)).toBe(eye);
    expect(viewerPoseCamera(new THREE.ArrayCamera())).toBeInstanceOf(THREE.ArrayCamera);
  });

  it("rotates forward locomotion with the live headset yaw", () => {
    const eye = new THREE.PerspectiveCamera();
    eye.rotation.y = -Math.PI / 2;
    eye.updateMatrixWorld(true);
    const forward = viewerPoseCamera(new THREE.ArrayCamera([eye]))
      .getWorldDirection(new THREE.Vector3());
    forward.y = 0;
    forward.normalize();
    const delta = headRelativeDelta({ x: 0, y: 1 }, forward, 1, 1);
    expect(delta.x).toBeCloseTo(1);
    expect(delta.z).toBeCloseTo(0);
  });

  it("derives current-frame facing directly from XR viewer pose and rig yaw", () => {
    const headYaw = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(0, 1, 0),
      -Math.PI / 2,
    );
    const headFacing = viewerForwardFromPose(headYaw);
    expect(headFacing.x).toBeCloseTo(1);
    expect(headFacing.z).toBeCloseTo(0);

    const rigYaw = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(0, 1, 0),
      Math.PI / 2,
    );
    const combined = viewerForwardFromPose(headYaw, rigYaw);
    expect(combined.x).toBeCloseTo(0);
    expect(combined.z).toBeCloseTo(-1);
  });

  it("composes current-frame headset position with rig translation and yaw", () => {
    const rigYaw = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(0, 1, 0),
      Math.PI / 2,
    );
    const position = viewerWorldPositionFromPose(
      { x: 0, y: 1.7, z: -1 },
      { x: 4, y: 0, z: 2 },
      rigYaw,
    );
    expect(position.x).toBeCloseTo(3);
    expect(position.y).toBeCloseTo(1.7);
    expect(position.z).toBeCloseTo(2);
  });

  it("resolves controllers by handedness regardless of source order", () => {
    const right = { handedness: "right" };
    const left = { handedness: "left" };
    expect(inputSourcesByHand([right, left])).toEqual({ left, right });
  });

  it("normalizes the Quest xr-standard primary stick axes", () => {
    expect(questLocomotionStick(0, -1)).toEqual({ x: 0, y: 1 });
    expect(questLocomotionStick(0, 1)).toEqual({ x: 0, y: -1 });
    expect(questLocomotionStick(-1, 0)).toEqual({ x: -1, y: 0 });
    expect(questLocomotionStick(1, 0)).toEqual({ x: 1, y: 0 });
  });

  it("applies and rescales the thumbstick dead zone", () => {
    expect(rescaleThumbstick(0.1, 0.1)).toEqual({ x: 0, y: 0 });
    expect(rescaleThumbstick(1, 0)).toEqual({ x: 1, y: 0 });
  });

  it("moves relative to horizontal headset facing", () => {
    expect(headRelativeDelta({ x: 0, y: 1 }, { x: 0, z: -1 }, 1.5, 2)).toEqual({ x: 0, z: -3 });
    expect(headRelativeDelta({ x: 1, y: 0 }, { x: 0, z: -1 }, 1.5, 2)).toEqual({ x: 3, z: 0 });
  });

  it("maps all four raw Quest stick directions around headset forward", () => {
    const facing = { x: 0, z: -1 };
    expect(headRelativeDelta(questLocomotionStick(0, -1), facing, 1, 1)).toEqual({ x: 0, z: -1 });
    expect(headRelativeDelta(questLocomotionStick(0, 1), facing, 1, 1)).toEqual({ x: 0, z: 1 });
    const left = headRelativeDelta(questLocomotionStick(-1, 0), facing, 1, 1);
    const right = headRelativeDelta(questLocomotionStick(1, 0), facing, 1, 1);
    expect(left.x).toBe(-1);
    expect(left.z).toBeCloseTo(0);
    expect(right.x).toBe(1);
    expect(right.z).toBeCloseTo(0);
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

describe("VR furniture presentation helpers", () => {
  const furniture = [
    { id: "desk", x: 100, y: 100, width: 100, depth: 60 },
    { id: "shelf", x: 400, y: 100, width: 120, depth: 40 },
  ];

  it("expands the ceiling-bounded semantic furniture reach by fifty percent", () => {
    expect(furnitureInteractionReach(furniture[0], 150)).toBe(165);
    expect(furnitureInteractionReach({ ...furniture[0], reach: 140 }, 120)).toBe(180);
  });

  it("opens independent panels when either the user or agent is near", () => {
    const next = updateFurniturePanelProximity(
      furniture,
      { x: 100, y: 210 },
      { x: 400, y: 220 },
      150,
      { visible: new Set(), dismissed: new Set() },
    );
    expect([...next.visible]).toEqual(["desk", "shelf"]);
  });

  it("keeps visible panels through the exit buffer, then closes them independently", () => {
    const buffered = updateFurniturePanelProximity(
      furniture,
      { x: 100, y: 280 },
      null,
      150,
      { visible: new Set(["desk", "shelf"]), dismissed: new Set() },
    );
    expect([...buffered.visible]).toEqual(["desk"]);

    const outside = updateFurniturePanelProximity(
      furniture,
      { x: 100, y: 291 },
      null,
      150,
      buffered,
    );
    expect([...outside.visible]).toEqual([]);
  });

  it("suppresses a dismissed panel until both occupants leave the buffered area", () => {
    const held = updateFurniturePanelProximity(
      furniture,
      { x: 100, y: 100 },
      { x: 100, y: 230 },
      150,
      { visible: new Set(), dismissed: new Set(["desk"]) },
    );
    expect([...held.visible]).toEqual([]);
    expect([...held.dismissed]).toEqual(["desk"]);

    const cleared = updateFurniturePanelProximity(
      furniture,
      { x: 300, y: 300 },
      { x: 300, y: 300 },
      150,
      held,
    );
    expect([...cleared.dismissed]).toEqual([]);

    const returned = updateFurniturePanelProximity(
      furniture,
      { x: 100, y: 100 },
      null,
      150,
      cleared,
    );
    expect([...returned.visible]).toEqual(["desk"]);
  });

  it("makes the Plans panel taller without widening it and keeps controls inside", () => {
    const standard = furniturePanelLayout("bookshelf");
    const plans = furniturePanelLayout("plan_status");
    expect(plans.width).toBe(standard.width);
    expect(plans.height).toBeGreaterThan(standard.height);
    expect(plans.maxLines).toBeGreaterThan(standard.maxLines);
    expect(Math.abs(plans.closePosition.y) + 0.07).toBeLessThanOrEqual(plans.height / 2);
    expect(Math.abs(plans.actionY) + 0.075).toBeLessThanOrEqual(plans.height / 2);
  });

  it("uses explicit close controls and reserves body triggers for email paging", () => {
    expect(transientTriggerBehavior("panel")).toBe("none");
    expect(transientTriggerBehavior("email_card", "")).toBe("none");
    expect(transientTriggerBehavior("email_card", "message body")).toBe("page");
    expect(nextEmailPage(0, 1601)).toBe(1);
    expect(nextEmailPage(3, 1601)).toBe(0);
  });

  it("uses elevation as the furniture centre without adding half-height", () => {
    expect(furnitureCenterSceneY(165)).toBe(1.65);
  });

  it("places the same contained texture on all four side faces", () => {
    const layouts = furnitureDecalLayouts(
      { id: "bed", x: 480, y: 430, width: 210, depth: 150, height: 65 },
      { width: 230, height: 180 },
    );
    expect(layouts.map((layout) => layout.face)).toEqual(["+x", "-x", "+z", "-z"]);
    layouts.forEach((layout) => {
      expect(layout.width / layout.height).toBeCloseTo(230 / 180);
      expect(layout.height).toBeLessThanOrEqual(65);
    });
    expect(layouts[0].width).toBeLessThanOrEqual(150);
    expect(layouts[1].width).toBeLessThanOrEqual(150);
    expect(layouts[2].width).toBeLessThanOrEqual(210);
    expect(layouts[3].width).toBeLessThanOrEqual(210);
  });
});
