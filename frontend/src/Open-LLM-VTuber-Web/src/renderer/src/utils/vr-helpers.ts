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

/**
 * Break spoken captions into a small number of headset-safe lines. The final
 * line is truncated rather than allowing a panel to grow into the companion's
 * sight line.
 */
export function clampCaptionText(value: string, maxCharacters = 42, maxLines = 3) {
  const words = value.trim().split(/\s+/).filter(Boolean);
  if (!words.length || maxCharacters < 2 || maxLines < 1) return [];

  const lines: string[] = [];
  let current = "";
  let consumed = 0;
  for (const word of words) {
    const fragments = word.length > maxCharacters
      ? word.match(new RegExp(`.{1,${maxCharacters}}`, "g")) || [word]
      : [word];
    for (const fragment of fragments) {
      const candidate = current ? `${current} ${fragment}` : fragment;
      if (candidate.length <= maxCharacters) {
        current = candidate;
        consumed += 1;
        continue;
      }
      if (current) lines.push(current);
      if (lines.length === maxLines) break;
      current = fragment;
      consumed += 1;
    }
    if (lines.length === maxLines) break;
  }
  if (lines.length < maxLines && current) lines.push(current);

  const fragmentCount = words.reduce(
    (total, word) => total + Math.max(1, Math.ceil(word.length / maxCharacters)),
    0,
  );
  if (consumed < fragmentCount && lines.length) {
    const last = lines.length - 1;
    lines[last] = `${lines[last].slice(0, Math.max(1, maxCharacters - 1)).trimEnd()}…`;
  }
  return lines;
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
  if (stage.version !== 2 || stage.mode !== "vr" || !stage.room || !stage.world || !Array.isArray(stage.nodes) || stage.nodes.length > 80) return false;
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
