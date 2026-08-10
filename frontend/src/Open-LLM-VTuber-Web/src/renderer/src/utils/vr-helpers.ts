export interface FloorAabbNode {
  id: string;
  x: number;
  y: number;
  width: number;
  depth: number;
}

export interface Point2 {
  x: number;
  y: number;
}

export interface QuaternionLike {
  x: number;
  y: number;
  z: number;
  w: number;
}

export interface Vector3Like {
  x: number;
  y: number;
  z: number;
}

export interface VrRayHitLike {
  distance: number;
  point: Vector3Like;
}

export const VR_RAY_MAX_DISTANCE = 3;
export const VR_SELECTION_HIGHLIGHT_KEY = "vrSelectionHighlightEnabled";
export const DEFAULT_VR_SELECTION_HIGHLIGHT = true;
export const FURNITURE_PANEL_EXIT_BUFFER_CM = 25;
export const FURNITURE_PANEL_REACH_MULTIPLIER = 1.5;

export interface FurniturePanelNode extends FloorAabbNode {
  reach?: number;
  uiWindow?: { provider?: string };
}

export interface FurniturePanelProximityState {
  visible: ReadonlySet<string>;
  dismissed: ReadonlySet<string>;
}

export interface FurniturePanelLayout {
  width: number;
  height: number;
  textureWidth: number;
  textureHeight: number;
  maxLines: number;
  closePosition: Vector3Like;
  actionY: number;
}

const STANDARD_FURNITURE_PANEL_LAYOUT: FurniturePanelLayout = {
  width: 1.5,
  height: 0.59,
  textureWidth: 768,
  textureHeight: 300,
  maxLines: 5,
  closePosition: { x: 0.63, y: 0.22, z: 0.012 },
  actionY: -0.39,
};

const PLANS_FURNITURE_PANEL_LAYOUT: FurniturePanelLayout = {
  width: 1.5,
  height: 0.95,
  textureWidth: 768,
  textureHeight: 486,
  maxLines: 8,
  closePosition: { x: 0.63, y: 0.4, z: 0.012 },
  actionY: -0.37,
};

export function furniturePanelLayout(provider?: string): FurniturePanelLayout {
  return provider === "plan_status"
    ? PLANS_FURNITURE_PANEL_LAYOUT
    : STANDARD_FURNITURE_PANEL_LAYOUT;
}

export interface FurniturePanelDecisionTarget {
  collection: "plans" | "skills";
  id: string;
}

export interface FurniturePanelContent {
  lines: string[];
  decision: FurniturePanelDecisionTarget | null;
}

const DECISION_STATUS_LABELS: Record<string, string> = {
  approved: "Approved",
  declined: "Declined",
  withdrawn: "Withdrawn",
};

function decisionStatusLabel(status: unknown) {
  if (typeof status === "string" && DECISION_STATUS_LABELS[status]) {
    return DECISION_STATUS_LABELS[status];
  }
  if (typeof status === "string" && status) {
    return `${status.charAt(0).toUpperCase()}${status.slice(1)}`;
  }
  return "";
}

function furnitureItemTitle(item: Record<string, unknown>, idKey: string, titleKey: string) {
  const title = item[titleKey];
  if (typeof title === "string" && title) return title;
  const id = item[idKey];
  return typeof id === "string" ? id : "";
}

function genericFurniturePanelLines(liveData: Record<string, unknown> | null): string[] {
  if (!liveData) return [];
  return Object.entries(liveData).slice(0, 4).map(([key, item]) => {
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
      return `${key}: ${String(item)}`;
    }
    if (Array.isArray(item)) return `${key}: ${item.length} item(s)`;
    return `${key}: available`;
  });
}

/**
 * Turn a furniture window snapshot into panel text and, for approval
 * providers, the next decision the user can make. Plan and skill windows
 * render their own pending/recent lines so the desk reacts visibly when a
 * plan has just been approved or declined.
 */
export function furniturePanelContent(
  provider: string | undefined,
  liveData: Record<string, unknown> | null,
): FurniturePanelContent {
  if (!liveData || typeof liveData !== "object") {
    return { lines: [], decision: null };
  }
  if (provider !== "plan_status" && provider !== "skill_status") {
    return { lines: genericFurniturePanelLines(liveData), decision: null };
  }

  const collection = provider === "plan_status" ? "plans" as const : "skills" as const;
  const noun = collection === "plans" ? "plan" : "skill";
  const pending = Array.isArray(liveData.pending)
    ? liveData.pending.filter((item): item is Record<string, unknown> => (
      Boolean(item) && typeof item === "object"
    ))
    : [];
  const pendingCount = Number.isFinite(liveData.pending_count)
    ? Number(liveData.pending_count)
    : pending.length;
  const firstPending = pending[0];
  const decisionId = collection === "plans"
    ? firstPending?.plan_id
    : firstPending?.slug;
  const decision = typeof decisionId === "string" && decisionId
    ? { collection, id: decisionId }
    : null;

  if (liveData.available === false && typeof liveData.message === "string") {
    return {
      lines: [`${noun === "plan" ? "Plans" : "Skills"} unavailable: ${liveData.message}`],
      decision,
    };
  }

  const lines: string[] = [];
  if (collection === "plans") {
    lines.push(`Pending plans: ${pendingCount}`);
    pending.slice(0, 2).forEach((item) => {
      lines.push(`• ${furnitureItemTitle(item, "plan_id", "title")}`);
    });
    const recent = liveData.recent && typeof liveData.recent === "object"
      ? liveData.recent as Record<string, unknown>
      : null;
    if (recent) {
      const status = decisionStatusLabel(recent.status);
      lines.push(
        `Recent: ${furnitureItemTitle(recent, "plan_id", "title")}${status ? ` — ${status}` : ""}`,
      );
    } else {
      lines.push("Recent: none");
    }
  } else {
    lines.push(`Pending skills: ${pendingCount}`);
    if (firstPending) {
      lines.push(`• ${furnitureItemTitle(firstPending, "slug", "name")}`);
    } else {
      const activeCount = Number.isFinite(liveData.active_count)
        ? Number(liveData.active_count)
        : 0;
      lines.push(`Active skills: ${activeCount}`);
    }
  }
  return { lines, decision };
}

/** Keep wall-mounted panels facing into the room without tracking the viewer.
 * In floor coordinates the north/mailbox wall is the low-y half. */
export function furniturePanelRotationY(nodeY: number, roomDepth: number) {
  return nodeY < roomDepth / 2 ? Math.PI : 0;
}

export function furnitureInteractionReach(
  node: Pick<FurniturePanelNode, "width" | "depth" | "reach">,
  reachCeiling: number,
) {
  const derived = 60 + Math.max(node.width, node.depth) / 2;
  const declared = typeof node.reach === "number" && Number.isFinite(node.reach) && node.reach > 0
    ? node.reach
    : derived;
  return Math.min(reachCeiling, declared) * FURNITURE_PANEL_REACH_MULTIPLIER;
}

function withinFurnitureDistance(
  node: FurniturePanelNode,
  point: Point2 | null,
  limit: number,
) {
  return point !== null && Math.hypot(point.x - node.x, point.y - node.y) <= limit;
}

/**
 * Derive the next set of proximity-driven panels. Visible panels receive an
 * exit buffer, while manually dismissed panels stay suppressed until both the
 * user and agent have left that buffered area.
 */
export function updateFurniturePanelProximity(
  nodes: readonly FurniturePanelNode[],
  user: Point2 | null,
  agent: Point2 | null,
  reachCeiling: number,
  state: FurniturePanelProximityState,
  exitBuffer = FURNITURE_PANEL_EXIT_BUFFER_CM,
): FurniturePanelProximityState {
  const visible = new Set<string>();
  const nodeIds = new Set(nodes.map((node) => node.id));
  const dismissed = new Set([...state.dismissed].filter((id) => nodeIds.has(id)));

  nodes.forEach((node) => {
    const enterLimit = furnitureInteractionReach(node, reachCeiling);
    const exitLimit = enterLimit + exitBuffer;
    const withinEnter = (
      withinFurnitureDistance(node, user, enterLimit) ||
      withinFurnitureDistance(node, agent, enterLimit)
    );
    const withinExit = (
      withinFurnitureDistance(node, user, exitLimit) ||
      withinFurnitureDistance(node, agent, exitLimit)
    );

    if (dismissed.has(node.id)) {
      if (withinExit) return;
      dismissed.delete(node.id);
    }
    if (withinEnter || (state.visible.has(node.id) && withinExit)) {
      visible.add(node.id);
    }
  });

  return { visible, dismissed };
}

export function parseVrSelectionHighlight(stored: string | null) {
  if (stored === null) return DEFAULT_VR_SELECTION_HIGHLIGHT;
  try {
    return JSON.parse(stored) === true;
  } catch (_error) {
    return DEFAULT_VR_SELECTION_HIGHLIGHT;
  }
}

export function loadVrSelectionHighlight() {
  try {
    return parseVrSelectionHighlight(localStorage.getItem(VR_SELECTION_HIGHLIGHT_KEY));
  } catch (_error) {
    return DEFAULT_VR_SELECTION_HIGHLIGHT;
  }
}

export function transientTriggerBehavior(kind: "panel" | "email_card", body?: string) {
  return kind === "email_card" && Boolean(body) ? "page" as const : "none" as const;
}

export function nextEmailPage(currentPage: number, bodyLength: number, pageSize = 800) {
  const pages = Math.max(1, Math.ceil(bodyLength / pageSize));
  return (currentPage + 1) % (pages + 1);
}

export interface HandedInputSource {
  handedness: string;
  gamepad?: Gamepad | null;
}

export interface FurnitureDecalNode extends FloorAabbNode {
  height: number;
}

export function inputSourcesByHand<T extends HandedInputSource>(sources: Iterable<T>) {
  const ordered = [...sources];
  return {
    left: ordered.find((source) => source.handedness === "left"),
    right: ordered.find((source) => source.handedness === "right"),
  };
}

/** The outer WebXR ArrayCamera follows the locomotion rig; an eye camera
 * carries the live headset pose. Either eye has the same useful facing vector. */
export function viewerPoseCamera(xrCamera: ArrayCamera): Camera {
  return xrCamera.cameras[0] || xrCamera;
}

function rotateVector(
  vector: { x: number; y: number; z: number },
  quaternion: QuaternionLike,
) {
  const uv = {
    x: quaternion.y * vector.z - quaternion.z * vector.y,
    y: quaternion.z * vector.x - quaternion.x * vector.z,
    z: quaternion.x * vector.y - quaternion.y * vector.x,
  };
  const uuv = {
    x: quaternion.y * uv.z - quaternion.z * uv.y,
    y: quaternion.z * uv.x - quaternion.x * uv.z,
    z: quaternion.x * uv.y - quaternion.y * uv.x,
  };
  return {
    x: vector.x + 2 * (quaternion.w * uv.x + uuv.x),
    y: vector.y + 2 * (quaternion.w * uv.y + uuv.y),
    z: vector.z + 2 * (quaternion.w * uv.z + uuv.z),
  };
}

/** Current-frame headset forward from XR reference space into scene space. */
export function viewerForwardFromPose(
  viewerOrientation: QuaternionLike,
  rigOrientation?: QuaternionLike,
) {
  let forward = rotateVector({ x: 0, y: 0, z: -1 }, viewerOrientation);
  if (rigOrientation) forward = rotateVector(forward, rigOrientation);
  const magnitude = Math.hypot(forward.x, forward.z);
  return magnitude
    ? { x: forward.x / magnitude, z: forward.z / magnitude }
    : { x: 0, z: 0 };
}

/** Compose the current XRFrame viewer position with the locomotion rig. The
 * renderer camera is updated later in the frame, so it cannot authoritatively
 * drive same-frame proximity checks. */
export function viewerWorldPositionFromPose(
  viewerPosition: Vector3Like,
  rigPosition: Vector3Like,
  rigOrientation: QuaternionLike,
) {
  const rotated = rotateVector(viewerPosition, rigOrientation);
  return {
    x: rigPosition.x + rotated.x,
    y: rigPosition.y + rotated.y,
    z: rigPosition.z + rotated.z,
  };
}

/**
 * Resolve the nearest physical ray hit and reject it when it falls outside the
 * active pointing ray's forward horizontal hemisphere. Callers must provide
 * the same live origin and direction used for the raycast so physical turns do
 * not get compared with a stale headset orientation. Only the nearest surface
 * is ever returned, so callers cannot skip an occluder behind it.
 */
export function selectVrRayHit<T extends VrRayHitLike>(
  hits: readonly T[],
  viewerPosition: Vector3Like,
  viewerForward: Vector3Like,
  maxDistance = VR_RAY_MAX_DISTANCE,
): T | null {
  if (!hits.length || !Number.isFinite(maxDistance) || maxDistance < 0) return null;
  const nearest = hits.reduce((closest, hit) => (
    hit.distance < closest.distance ? hit : closest
  ));
  if (!Number.isFinite(nearest.distance) || nearest.distance < 0 || nearest.distance > maxDistance) return null;

  const forwardMagnitude = Math.hypot(viewerForward.x, viewerForward.z);
  if (!forwardMagnitude) return null;
  const targetX = nearest.point.x - viewerPosition.x;
  const targetZ = nearest.point.z - viewerPosition.z;
  const horizontalDot = (
    targetX * viewerForward.x + targetZ * viewerForward.z
  ) / forwardMagnitude;

  // A zero dot product is the exact side boundary and remains selectable.
  return horizontalDot >= -Number.EPSILON ? nearest : null;
}

export function isRayGrabbableNode(
  node: { type?: string; interaction?: { grabbable?: boolean } } | undefined,
): node is { type: "prop"; interaction: { grabbable: true } } {
  return node?.type === "prop" && node.interaction?.grabbable === true;
}

/** Normalize WebXR's xr-standard primary stick into semantic room axes.
 * Axis 2 is left/right; axis 3 is down/forward-negative. */
export function questLocomotionStick(axis2: number, axis3: number): Point2 {
  return { x: axis2, y: axis3 === 0 ? 0 : -axis3 };
}

export function furnitureCenterSceneY(elevation: number, unitsPerMeter = 100) {
  return elevation / unitsPerMeter;
}

export function furnitureDecalLayouts(
  node: FurnitureDecalNode,
  image: { width: number; height: number },
  offset = 0.5,
) {
  const safeImageWidth = Math.max(1, image.width);
  const safeImageHeight = Math.max(1, image.height);
  const contain = (faceWidth: number) => {
    const scale = Math.min(
      faceWidth / safeImageWidth,
      node.height / safeImageHeight,
    );
    return {
      width: safeImageWidth * scale,
      height: safeImageHeight * scale,
    };
  };
  const xFace = contain(node.depth);
  const zFace = contain(node.width);
  return [
    { ...xFace, x: node.width / 2 + offset, z: 0, rotationY: Math.PI / 2, face: "+x" },
    { ...xFace, x: -node.width / 2 - offset, z: 0, rotationY: -Math.PI / 2, face: "-x" },
    { ...zFace, x: 0, z: node.depth / 2 + offset, rotationY: 0, face: "+z" },
    { ...zFace, x: 0, z: -node.depth / 2 - offset, rotationY: Math.PI, face: "-z" },
  ] as const;
}

/** Keep the latest headset-safe lines so a long spoken response advances. */
export function clampCaptionText(value: string, maxCharacters = 42, maxLines = 3) {
  const words = value.trim().split(/\s+/).filter(Boolean);
  if (!words.length || maxCharacters < 2 || maxLines < 1) return [];

  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const fragments = word.length > maxCharacters
      ? word.match(new RegExp(`.{1,${maxCharacters}}`, "g")) || [word]
      : [word];
    for (const fragment of fragments) {
      const candidate = current ? `${current} ${fragment}` : fragment;
      if (candidate.length <= maxCharacters) {
        current = candidate;
        continue;
      }
      if (current) lines.push(current);
      current = fragment;
    }
  }
  if (current) lines.push(current);

  const visible = lines.slice(-maxLines);
  if (lines.length > maxLines && visible.length) {
    visible[0] = `…${visible[0].slice(-(maxCharacters - 1))}`;
  }
  return visible;
}

export function browserEndpoints(location: Pick<Location, "protocol" | "host">) {
  const secure = location.protocol === "https:";
  return {
    baseUrl: `${secure ? "https" : "http"}://${location.host}`,
    wsUrl: `${secure ? "wss" : "ws"}://${location.host}/client-ws`,
  };
}

export function withRendererMode(url: string, mode: "normal" | "vr", base: string) {
  const parsed = new URL(url, base);
  parsed.searchParams.set("renderer_mode", mode);
  return parsed.toString();
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

export function validateVrStageShape(value: unknown) {
  if (!value || typeof value !== "object") return false;
  const stage = value as Record<string, unknown>;
  if (stage.version !== 3 || stage.mode !== "vr" || !stage.room || !stage.world || !Array.isArray(stage.nodes) || stage.nodes.length > 80) return false;
  const room = stage.room as Record<string, unknown>;
  const world = stage.world as Record<string, unknown>;
  if (![room.width, room.depth, room.height].every(finite) ||
      ![world.units_per_meter, world.reach_ceiling, world.move_speed].every(finite) ||
      (room.width as number) <= 0 || (room.depth as number) <= 0 || (room.height as number) <= 0 ||
      typeof room.floor_color !== "string" || typeof room.wall_color !== "string" ||
      !stage.metadata || typeof stage.metadata !== "object") return false;
  const ids = new Set<string>();
  const labels = new Set<string>();
  let avatars = 0;
  for (const rawNode of stage.nodes) {
    if (!rawNode || typeof rawNode !== "object") return false;
    const node = rawNode as Record<string, unknown>;
    if (typeof node.id !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(node.id) || ids.has(node.id)) return false;
    ids.add(node.id);
    if (node.id === "avatar") {
      avatars += 1;
      if (node.type !== "avatar") return false;
    } else if (node.type !== "furniture" && node.type !== "prop") return false;
    if (node.type === "prop") {
      const interaction = node.interaction as Record<string, unknown> | undefined;
      if (interaction?.grabbable !== true || typeof node.support_id !== "string" || !finite(node.yaw)) return false;
    } else if ((node.interaction as Record<string, unknown> | undefined)?.grabbable !== undefined) return false;
    const label = typeof node.label === "string" && node.label ? node.label : node.id;
    if (labels.has(label.toLocaleLowerCase())) return false;
    labels.add(label.toLocaleLowerCase());
    if (![node.x, node.y, node.width, node.depth, node.height, node.elevation].every(finite)) return false;
    if ((node.width as number) <= 0 || (node.depth as number) <= 0 || (node.height as number) <= 0) return false;
    if ((node.x as number) - (node.width as number) / 2 < 0 ||
        (node.x as number) + (node.width as number) / 2 > (room.width as number) ||
        (node.y as number) - (node.depth as number) / 2 < 0 ||
        (node.y as number) + (node.depth as number) / 2 > (room.depth as number) ||
        (node.elevation as number) - (node.height as number) / 2 < 0 ||
        (node.elevation as number) + (node.height as number) / 2 > (room.height as number) ||
        typeof node.color !== "string") return false;
  }
  if (avatars !== 1) return false;
  const avatar = stage.nodes.find((node) => (node as Record<string, unknown>).id === "avatar") as unknown as FloorAabbNode;
  return !stage.nodes.some((node) => {
    const furniture = node as FloorAabbNode;
    return furniture.id !== "avatar" && pointInsideAabb(avatar, furniture);
  });
}

export function floorToScene(
  room: { width: number; depth: number },
  point: Point2,
  unitsPerMeter = 100,
) {
  return {
    x: (point.x - room.width / 2) / unitsPerMeter,
    z: (room.depth / 2 - point.y) / unitsPerMeter,
  };
}

export function rescaleThumbstick(x: number, y: number, deadZone = 0.2) {
  const magnitude = Math.hypot(x, y);
  if (magnitude <= deadZone) return { x: 0, y: 0 };
  const scaled = Math.min(1, (magnitude - deadZone) / (1 - deadZone));
  return { x: x / magnitude * scaled, y: y / magnitude * scaled };
}

export function headRelativeDelta(
  stick: Point2,
  horizontalFacing: { x: number; z: number },
  speed: number,
  elapsedSeconds: number,
) {
  const magnitude = Math.hypot(horizontalFacing.x, horizontalFacing.z);
  if (!magnitude) return { x: 0, z: 0 };
  const forwardX = horizontalFacing.x / magnitude;
  const forwardZ = horizontalFacing.z / magnitude;
  const rightX = -forwardZ;
  const rightZ = forwardX;
  return {
    x: (rightX * stick.x + forwardX * stick.y) * speed * elapsedSeconds,
    z: (rightZ * stick.x + forwardZ * stick.y) * speed * elapsedSeconds,
  };
}

export function snapTurnStep(
  axis: number,
  armed: boolean,
  threshold = 0.7,
  rearm = 0.3,
  angleRadians = Math.PI / 6,
) {
  if (armed && Math.abs(axis) >= threshold) {
    return { armed: false, delta: axis > 0 ? -angleRadians : angleRadians };
  }
  if (!armed && Math.abs(axis) <= rearm) return { armed: true, delta: 0 };
  return { armed, delta: 0 };
}

export const pressedEdge = (pressed: boolean, wasPressed: boolean) => pressed && !wasPressed;

export function pointInsideAabb(point: Point2, node: FloorAabbNode) {
  return point.x >= node.x - node.width / 2 && point.x <= node.x + node.width / 2 &&
    point.y >= node.y - node.depth / 2 && point.y <= node.y + node.depth / 2;
}

export function isViewerPositionClear(
  point: Point2,
  furniture: FloorAabbNode[],
  room: { width: number; depth: number },
  viewerRadius = 25,
) {
  return point.x >= viewerRadius && point.x <= room.width - viewerRadius &&
    point.y >= viewerRadius && point.y <= room.depth - viewerRadius &&
    !furniture.some((node) => (
      point.x >= node.x - node.width / 2 - viewerRadius &&
      point.x <= node.x + node.width / 2 + viewerRadius &&
      point.y >= node.y - node.depth / 2 - viewerRadius &&
      point.y <= node.y + node.depth / 2 + viewerRadius
    ));
}

export function selectViewerSpawn(
  avatar: FloorAabbNode,
  furniture: FloorAabbNode[],
  room: { width: number; depth: number },
  distance = 175,
  viewerRadius = 25,
) {
  const candidates = [
    { x: avatar.x + distance, y: avatar.y },
    { x: avatar.x - distance, y: avatar.y },
    { x: avatar.x, y: avatar.y + distance },
    { x: avatar.x, y: avatar.y - distance },
  ];
  const clear = candidates.find((point) => (
    isViewerPositionClear(point, furniture, room, viewerRadius)
  ));
  return clear || {
    x: Math.min(room.width - viewerRadius, Math.max(viewerRadius, avatar.x + distance)),
    y: Math.min(room.depth - viewerRadius, Math.max(viewerRadius, avatar.y)),
  };
}

export function selectDockingPoint(
  target: FloorAabbNode,
  avatar: FloorAabbNode,
  furniture: FloorAabbNode[],
  room: { width: number; depth: number },
  gap = 10,
) {
  const radius = Math.max(avatar.width, avatar.depth) / 2;
  const candidates = [
    { x: target.x, y: target.y - target.depth / 2 - radius - gap },
    { x: target.x + target.width / 2 + radius + gap, y: target.y },
    { x: target.x, y: target.y + target.depth / 2 + radius + gap },
    { x: target.x - target.width / 2 - radius - gap, y: target.y },
  ];
  return candidates
    .map((point, order) => ({ point, order }))
    .filter(({ point }) => (
      point.x >= radius && point.x <= room.width - radius &&
      point.y >= radius && point.y <= room.depth - radius &&
      !furniture.some((node) => node.id !== target.id && pointInsideAabb(point, node))
    ))
    .sort((a, b) => (
      Math.hypot(a.point.x - avatar.x, a.point.y - avatar.y) -
      Math.hypot(b.point.x - avatar.x, b.point.y - avatar.y) || a.order - b.order
    ))[0]?.point || null;
}
import type { ArrayCamera, Camera } from "three";
