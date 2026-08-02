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
  if (stage.version !== 1 || stage.mode !== "vr" || !stage.room || !stage.world || !Array.isArray(stage.nodes) || stage.nodes.length > 80) return false;
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
    } else if (node.type !== "furniture") return false;
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
