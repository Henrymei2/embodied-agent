import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  clampCaptionText,
  floorToScene,
  furnitureInteractionReach,
  furnitureCenterSceneY,
  furnitureDecalLayouts,
  furniturePanelContent,
  furniturePanelDocument,
  furniturePanelLayout,
  furniturePanelRotationY,
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
  backPanel,
  createPanelViewState,
  navigatePanel,
  panelViewport,
  panelScrollRepeatStep,
  reconcilePanelViewState,
  scrollPanel,
  wrappedPanelLines,
} from "./vr-helpers";
import {
  currentActivityLabel,
  friendlyToolName,
  selectCurrentActivityTool,
} from "./agent-activity-presentation";
import {
  reduceAgentActivityState,
  type AgentActivityState,
} from "../context/agent-activity-context";

describe("VR spatial helpers", () => {
  it("defaults VR selection highlighting on and parses a persisted opt-out", () => {
    expect(parseVrSelectionHighlight(null)).toBe(true);
    expect(parseVrSelectionHighlight("true")).toBe(true);
    expect(parseVrSelectionHighlight("false")).toBe(false);
    expect(parseVrSelectionHighlight("not-json")).toBe(true);
  });

  it("keeps captions within a compact headset-safe line budget", () => {
    expect(clampCaptionText("Short caption", 20, 2)).toEqual(["Short caption"]);
    expect(clampCaptionText("A concise spatial caption that stays below the companion", 20, 2)).toEqual([
      "…caption that stays",
      "below the companion",
    ]);
    expect(clampCaptionText("   ")).toEqual([]);
  });

  it("keeps the end of long unbroken captions", () => {
    expect(clampCaptionText("abcdefghijklmnopqrstuvwx", 8, 2)).toEqual([
      "…jklmnop",
      "qrstuvwx",
    ]);
    expect(clampCaptionText("这是一个没有空格而且需要持续显示后半部分的长字幕", 6, 2)).toEqual([
      "…续显示后半",
      "部分的长字幕",
    ]);
  });

  it("selects and labels the current agent activity", () => {
    const tools = [
      { tool_id: "done", tool_name: "read_notes", status: "completed" as const },
      { tool_id: "queued", tool_name: "create_reminder", status: "pending" as const },
      { tool_id: "active", tool_name: "search_web", status: "running" as const },
    ];
    expect(selectCurrentActivityTool(tools)?.tool_id).toBe("active");
    expect(selectCurrentActivityTool(tools.filter((tool) => tool.status !== "running"))?.tool_id).toBe("queued");
    expect(selectCurrentActivityTool([
      tools[0],
      { tool_id: "latest", tool_name: "send_email", status: "completed" as const },
    ])?.tool_id).toBe("latest");
    expect(friendlyToolName("search_web", "Unknown task")).toBe("Searching web");
    expect(currentActivityLabel(
      { phase: "tools", tools },
      { thinking: "Thinking…", planning: "Planning…", plan: "Executing plan…", unknownTool: "Unknown task" },
    )).toBe("Searching web…");
    expect(currentActivityLabel(
      { phase: "planning", tools: [] },
      { thinking: "Thinking…", planning: "Planning…", plan: "Executing plan…", unknownTool: "Unknown task" },
    )).toBe("Planning…");
    expect(currentActivityLabel(
      { phase: "plan", label: "Archive old emails", tools: [] },
      { thinking: "Thinking…", planning: "Planning…", plan: "Executing plan…", unknownTool: "Unknown task" },
    )).toBe("Archive old emails");
    expect(currentActivityLabel(
      { phase: "plan", tools: [] },
      { thinking: "Thinking…", planning: "Planning…", plan: "Executing plan…", unknownTool: "Unknown task" },
    )).toBe("Executing plan…");
    expect(currentActivityLabel(
      { phase: "thinking", tools: [] },
      { thinking: "Thinking…", planning: "Planning…", plan: "Executing plan…", unknownTool: "Unknown task" },
    )).toBe("Thinking…");
    expect(currentActivityLabel(
      { phase: "idle", tools: [] },
      { thinking: "Thinking…", planning: "Planning…", plan: "Executing plan…", unknownTool: "Unknown task" },
    )).toBeNull();
  });

  it("keeps an autonomy intention separate from lower-level activity", () => {
    let state: AgentActivityState = {
      activity: { phase: "thinking", tools: [] },
      intention: null,
    };
    state = reduceAgentActivityState(state, {
      phase: "plan",
      label: "  Archive old emails  ",
      tools: [],
    });
    expect(state).toEqual({
      activity: { phase: "idle", tools: [] },
      intention: { label: "Archive old emails" },
    });

    const tools = [{
      tool_id: "archive",
      tool_name: "archive_email",
      status: "running" as const,
    }];
    state = reduceAgentActivityState(state, { phase: "tools", tools });
    expect(state.activity).toEqual({ phase: "tools", tools });
    expect(state.intention).toEqual({ label: "Archive old emails" });

    state = reduceAgentActivityState(state, { phase: "thinking", tools: [] });
    expect(state.intention).toEqual({ label: "Archive old emails" });

    state = reduceAgentActivityState(state, { phase: "idle", tools: [] });
    expect(state).toEqual({
      activity: { phase: "idle", tools: [] },
      intention: null,
    });
  });

  it("retains an active intention with a blank fallback label", () => {
    const state = reduceAgentActivityState(
      { activity: { phase: "idle", tools: [] }, intention: null },
      { phase: "plan", label: "   ", tools: [] },
    );
    expect(state.intention).toEqual({ label: "" });
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

  it("makes the Browser panel taller with room for a screenshot", () => {
    const standard = furniturePanelLayout("bookshelf");
    const browser = furniturePanelLayout("browser_status");
    expect(browser.width).toBeGreaterThan(standard.width);
    expect(browser.height).toBeGreaterThan(standard.height);
    expect(browser.textureHeight).toBeGreaterThan(standard.textureHeight);
    expect(browser.maxLines).toBeGreaterThan(standard.maxLines);
    expect(Math.abs(browser.closePosition.y) + 0.07).toBeLessThanOrEqual(browser.height / 2);
  });

  it("faces panels inward from opposite walls without viewer tracking", () => {
    expect(furniturePanelRotationY(20, 1080)).toBe(Math.PI);
    expect(furniturePanelRotationY(1060, 1080)).toBe(0);
  });

  it("uses explicit close controls and reserves body triggers for email paging", () => {
    expect(transientTriggerBehavior("panel")).toBe("none");
    expect(transientTriggerBehavior("email_card", "")).toBe("none");
    expect(transientTriggerBehavior("email_card", "message body")).toBe("page");
    expect(nextEmailPage(0, 1601)).toBe(1);
    expect(nextEmailPage(3, 1601)).toBe(0);
  });

  it("navigates, returns, and restores the previous page scroll", () => {
    const document = {
      rootPageId: "a",
      pages: [
        { id: "a", title: "A", lines: ["1", "2", "3", "4"], actions: [] },
        { id: "b", title: "B", lines: ["detail"], actions: [] },
      ],
    };
    let state = createPanelViewState(document);
    state = scrollPanel(document, state, 3, 2);
    expect(state.scroll).toBe(2);
    state = navigatePanel(document, state, "b");
    expect(state).toMatchObject({ pageId: "b", scroll: 0 });
    expect(backPanel(state)).toMatchObject({ pageId: "a", scroll: 2 });
  });

  it("wraps unspaced text without losing its ending and scrolls to the final line", () => {
    const source = "这是一个没有空格而且足够长的中文字符串用于确认最后的文字不会丢失";
    const wrapped = wrappedPanelLines([source], 8);
    expect(wrapped.join("")).toBe(source);
    const document = {
      rootPageId: "root",
      pages: [{ id: "root", title: "Text", lines: [source], actions: [] }],
    };
    const state = scrollPanel(document, createPanelViewState(document), 3, 99);
    const viewport = panelViewport(document, state, 3);
    expect(viewport.scroll).toBe(viewport.maxScroll);
    expect(wrapped.join("").endsWith("不会丢失")).toBe(true);
  });

  it("applies a scroll dead zone, initial delay, and held-stick repeat", () => {
    expect(panelScrollRepeatStep(0.4, 0, 0, 100)).toEqual({ delta: 0, direction: 0, nextAt: 0 });
    const first = panelScrollRepeatStep(0.8, 0, 0, 100);
    expect(first).toEqual({ delta: 1, direction: 1, nextAt: 450 });
    expect(panelScrollRepeatStep(0.8, first.direction, first.nextAt, 300).delta).toBe(0);
    expect(panelScrollRepeatStep(0.8, first.direction, first.nextAt, 450)).toEqual({
      delta: 1, direction: 1, nextAt: 570,
    });
    expect(panelScrollRepeatStep(-0.8, 1, 570, 460).delta).toBe(-1);
  });

  it("falls back to the root when refreshed content removes the current page", () => {
    const original = {
      rootPageId: "root",
      pages: [
        { id: "root", title: "Root", lines: [], actions: [] },
        { id: "detail", title: "Detail", lines: [], actions: [] },
      ],
    };
    const state = navigatePanel(original, createPanelViewState(original), "detail");
    const refreshed = { rootPageId: "root", pages: [original.pages[0]] };
    expect(reconcilePanelViewState(refreshed, state)).toEqual(createPanelViewState(refreshed));
  });

  it("builds recent-mail, calendar, and complete plan detail pages", () => {
    const mail = furniturePanelDocument("google_gmail_unread", {
      messages: [
        { message_id: "m1", subject: "A very long subject", sender: "Ada", received_at: "Today", snippet: "Preview" },
        { message_id: "m2", subject: "Second", sender: "Bo", received_at: "Today", snippet: "Preview" },
        { message_id: "m3", subject: "Third", sender: "Cy", received_at: "Today", snippet: "Preview" },
      ],
    }, "Mailbox");
    expect(mail.pages[0].actions).toHaveLength(3);
    expect(mail.pages[0].lines.join(" ")).toContain("A very long subject");

    const calendar = furniturePanelDocument("google_calendar_status", {
      events: [{ event_id: "e1", title: "Review", start: "2026-08-11T09:00:00-05:00", end: "2026-08-11T10:00:00-05:00", description: "Full notes" }],
    }, "Calendar");
    expect(calendar.pages.some((page) => page.id === "event-e1" && page.lines.includes("Full notes"))).toBe(true);

    const plans = furniturePanelDocument("plan_status", {
      plan_details: [{ plan_id: "p1", title: "Launch", goal: "Ship safely", steps: ["One", "Two"], notes: "Keep rollback", status: "pending", revision: 2 }],
      pending: [],
    }, "Planning Desk");
    const detail = plans.pages.find((page) => page.id === "plan-p1")!;
    expect(detail.lines).toContain("Goal: Ship safely");
    expect(detail.lines).toContain("2. Two");
    expect(detail.actions.map((action) => action.label)).toEqual(["Approve", "Decline"]);
  });

  it("uses elevation as the furniture centre without adding half-height", () => {
    expect(furnitureCenterSceneY(165)).toBe(1.65);
  });

  it("targets the first pending plan and shows pending count and titles", () => {
    const content = furniturePanelContent("plan_status", {
      available: true,
      pending: [
        { plan_id: "plan-1", title: "Clean the room", status: "pending", created_at: "2026-08-10T00:00:00Z" },
        { plan_id: "plan-2", title: "Write report", status: "pending", created_at: "2026-08-10T00:01:00Z" },
      ],
      pending_count: 2,
      recent: null,
    });
    expect(content.decision).toEqual({ collection: "plans", id: "plan-1" });
    expect(content.lines).toContain("Pending plans: 2");
    expect(content.lines).toContain("• Clean the room");
    expect(content.lines).toContain("• Write report");
  });

  it("stops targeting a plan after it was decided and shows the recent decision", () => {
    const content = furniturePanelContent("plan_status", {
      available: true,
      pending: [],
      pending_count: 0,
      recent: {
        plan_id: "plan-1",
        title: "Clean the room",
        status: "approved",
        created_at: "2026-08-10T00:00:00Z",
      },
    });
    expect(content.decision).toBeNull();
    expect(content.lines).toContain("Pending plans: 0");
    expect(content.lines).toContain("Recent: Clean the room — Approved");
  });

  it("targets the first pending skill slug and lists active skills when idle", () => {
    const pending = furniturePanelContent("skill_status", {
      available: true,
      pending: [{ slug: "transcribe", name: "Transcribe meetings" }],
      pending_count: 1,
      active: [],
      active_count: 0,
    });
    expect(pending.decision).toEqual({ collection: "skills", id: "transcribe" });
    expect(pending.lines).toContain("Pending skills: 1");
    expect(pending.lines).toContain("• Transcribe meetings");

    const idle = furniturePanelContent("skill_status", {
      available: true,
      pending: [],
      pending_count: 0,
      active: [],
      active_count: 3,
    });
    expect(idle.decision).toBeNull();
    expect(idle.lines).toContain("Pending skills: 0");
    expect(idle.lines).toContain("Active skills: 3");
  });

  it("falls back to generic summary lines for other providers without decisions", () => {
    const content = furniturePanelContent("bookshelf", {
      available: true,
      books: 12,
    });
    expect(content.decision).toBeNull();
    expect(content.lines).toContain("books: 12");
    expect(furniturePanelContent("plan_status", null)).toEqual({
      lines: [],
      decision: null,
    });
  });

  it("renders the latest browser activity with an image reference", () => {
    const content = furniturePanelContent("browser_status", {
      available: true,
      tool: "browser_navigate",
      url: "https://example.com",
      text: "A short page summary the agent can read out loud.",
      has_image: true,
      image_url: "/api/furniture/browser/image",
      updated_at: "2026-08-10T22:00:00+00:00",
    });
    expect(content.decision).toBeNull();
    expect(content.lines).toContain("Tool: browser_navigate");
    expect(content.lines).toContain("URL: https://example.com");
    expect(content.lines).toContain("Updated: 2026-08-10 22:00:00 UTC");
    expect(content.imageUrl).toBe("/api/furniture/browser/image");
  });

  it("wraps long browser results into a bounded line budget without an image", () => {
    const content = furniturePanelContent("browser_status", {
      available: true,
      tool: "web_search",
      url: null,
      text: "one two three four five six seven eight nine ten eleven twelve",
      has_image: false,
      updated_at: null,
    });
    expect(content.imageUrl).toBeNull();
    const bodyLines = content.lines.filter((line) => !line.startsWith("Tool:"));
    expect(bodyLines.length).toBeGreaterThan(0);
    expect(bodyLines.length).toBeLessThanOrEqual(4);
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
