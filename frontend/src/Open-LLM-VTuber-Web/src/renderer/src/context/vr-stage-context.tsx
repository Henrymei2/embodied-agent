import {
  createContext, useCallback, useContext, useMemo, useRef, useState,
} from "react";
import type {
  FrontendActionRequest, FurnitureWindowConfig,
  StageInteraction, StagePose, StageSpritesheet, StageTexture,
} from "@/context/dynamic-stage-context";
import { validateVrStageShape } from "@/utils/vr-helpers";

export interface VrFrontendActionResult {
  request_id?: string;
  action?: string;
  ok: boolean;
  error?: string;
  state?: VrStageDocument;
  position?: { x: number; y: number };
  objects?: VrStageNode[];
  agent_position?: { x: number; y: number };
  reachable_objects?: string[];
  arrived?: boolean;
  duration_ms?: number;
  capabilities?: Record<string, unknown>;
}

export interface VrStageNode {
  id: string;
  type: "furniture" | "avatar";
  label?: string;
  x: number;
  y: number;
  width: number;
  depth: number;
  height: number;
  elevation: number;
  color: string;
  texture?: StageTexture;
  stateTextures?: Partial<Record<"unread", StageTexture>>;
  spritesheet?: StageSpritesheet;
  uiWindow?: FurnitureWindowConfig;
  pose?: StagePose;
  interaction?: StageInteraction;
  reach?: number;
}

export interface VrStageDocument {
  version: 1;
  mode: "vr";
  room: {
    width: number;
    depth: number;
    height: number;
    floor_color: string;
    wall_color: string;
  };
  world: {
    units_per_meter: number;
    reach_ceiling: number;
    move_speed: number;
  };
  nodes: VrStageNode[];
  metadata: Record<string, string | undefined>;
}

interface VrStageValue {
  stage: VrStageDocument | null;
  motionDurationMs: number;
  setStageFromServer: (value: unknown) => VrFrontendActionResult;
  handleFrontendAction: (request: FrontendActionRequest) => Promise<VrFrontendActionResult>;
}

const VrStageContext = createContext<VrStageValue | null>(null);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

export function validateVrStage(value: unknown): value is VrStageDocument {
  return validateVrStageShape(value);
}

const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => (
  Math.hypot(a.x - b.x, a.y - b.y)
);

const endpointError = (stage: VrStageDocument, x: number, y: number) => {
  const avatar = stage.nodes.find((node) => node.id === "avatar");
  if (!avatar) return "VR scene has no avatar.";
  const radius = Math.max(avatar.width, avatar.depth) / 2;
  if (x < radius || x > stage.room.width - radius || y < radius || y > stage.room.depth - radius) {
    return "Agent endpoint is outside the VR room.";
  }
  const collision = stage.nodes.find((node) => node.id !== "avatar" && (
    x >= node.x - node.width / 2 && x <= node.x + node.width / 2 &&
    y >= node.y - node.depth / 2 && y <= node.y + node.depth / 2
  ));
  return collision ? `Agent endpoint is inside ${collision.label || collision.id}.` : null;
};

const mergePatch = (stage: VrStageDocument, patch: Record<string, unknown>): VrStageDocument => {
  const nodePatches = Array.isArray(patch.nodes) ? patch.nodes as Partial<VrStageNode>[] : [];
  const remove = new Set(Array.isArray(patch.remove_node_ids) ? patch.remove_node_ids as string[] : []);
  const existing = stage.nodes
    .filter((node) => !remove.has(node.id))
    .map((node) => {
      const update = nodePatches.find((item) => item.id === node.id);
      return update ? { ...node, ...update } : node;
    });
  for (const node of nodePatches) {
    if (node.id && !existing.some((item) => item.id === node.id)) existing.push(node as VrStageNode);
  }
  return {
    ...stage,
    room: { ...stage.room, ...(patch.room as object || {}) },
    world: { ...stage.world, ...(patch.world as object || {}) },
    nodes: existing,
    metadata: {
      ...stage.metadata,
      ...(patch.metadata as object || {}),
      updated_at: new Date().toISOString(),
    },
  };
};

export function VrStageProvider({ children }: { children: React.ReactNode }) {
  const [stage, setStage] = useState<VrStageDocument | null>(null);
  const [motionDurationMs, setMotionDurationMs] = useState(900);
  const stageRef = useRef<VrStageDocument | null>(null);
  const defaultRef = useRef<VrStageDocument | null>(null);

  const commit = useCallback((candidate: unknown, action?: string, requestId?: string): VrFrontendActionResult => {
    if (!validateVrStage(candidate)) {
      return { request_id: requestId, action, ok: false, error: "Invalid VR stage document." };
    }
    stageRef.current = candidate;
    setStage(candidate);
    return { request_id: requestId, action, ok: true, state: candidate };
  }, []);

  const setStageFromServer = useCallback((value: unknown) => {
    const result = commit(value);
    if (result.ok && !defaultRef.current) defaultRef.current = value as VrStageDocument;
    return result;
  }, [commit]);

  const handleFrontendAction = useCallback(async ({ request_id, action, args = {} }: FrontendActionRequest) => {
    const current = stageRef.current;
    if (!current) return { request_id, action, ok: false, error: "VR stage has not loaded." };
    if (action === "stage_get_state") return { request_id, action, ok: true, state: current };
    if (action === "stage_set_state") return commit(args.state, action, request_id);
    if (action === "stage_patch_state") {
      if (!args.patch || typeof args.patch !== "object") return { request_id, action, ok: false, error: "stage_patch_state requires args.patch." };
      return commit(mergePatch(current, args.patch as Record<string, unknown>), action, request_id);
    }
    if (action === "stage_reset_state") return commit(defaultRef.current, action, request_id);
    if (action === "stage_list_capabilities") {
      return {
        request_id, action, ok: true,
        capabilities: {
          actions: ["stage_get_state", "stage_set_state", "stage_patch_state", "stage_reset_state", "stage_list_capabilities", "get_object_position", "move_to", "list_objects", "get_scene_status"],
          node_types: ["avatar", "panel"], version: 1,
          texture: "front-face texture", spritesheet: "avatar billboard spritesheet",
          furniture_window: "in-world panel", pose: "standing | sitting",
          world: "grounded VR world", move: "waypoint movement",
          patch_shape: "room, world, nodes, remove_node_ids, metadata",
        },
      };
    }
    if (action === "get_object_position") {
      const node = current.nodes.find((item) => item.id === args.name || item.label === args.name);
      return node
        ? { request_id, action, ok: true, position: { x: node.x, y: node.y } }
        : { request_id, action, ok: false, error: `Unknown object: ${String(args.name)}` };
    }
    if (action === "move_to") {
      const path = Array.isArray(args.waypoints)
        ? args.waypoints.filter((item): item is { x: number; y: number } => Boolean(item && typeof item === "object" && finite((item as { x?: unknown }).x) && finite((item as { y?: unknown }).y)))
        : [];
      if (!path.length && finite(args.x) && finite(args.y)) path.push({ x: args.x, y: args.y });
      if (!path.length) return { request_id, action, ok: false, error: "move_to requires a destination." };
      let elapsed = 0;
      for (const point of path) {
        const error = endpointError(stageRef.current || current, point.x, point.y);
        if (error) return { request_id, action, ok: false, error, state: stageRef.current || current };
        const active = stageRef.current || current;
        const avatar = active.nodes.find((node) => node.id === "avatar")!;
        const duration = Math.round(Math.min(1800, Math.max(250, distance(avatar, point) / active.world.move_speed * 1000)));
        setMotionDurationMs(duration);
        const next = mergePatch(active, { nodes: [{ id: "avatar", x: point.x, y: point.y, pose: undefined }] });
        const result = commit(next, action, request_id);
        if (!result.ok) return result;
        elapsed += duration;
        await new Promise<void>((resolve) => window.setTimeout(resolve, duration));
      }
      const finalPoint = path[path.length - 1];
      return { request_id, action, ok: true, state: stageRef.current || current, arrived: true, duration_ms: elapsed, position: finalPoint };
    }
    if (action === "list_objects") {
      const avatar = current.nodes.find((node) => node.id === "avatar");
      const limit = finite(args.distance) ? args.distance : null;
      const objects = current.nodes.filter((node) => node.id !== "avatar" && (!avatar || limit === null || distance(avatar, node) <= limit));
      return { request_id, action, ok: true, objects };
    }
    if (action === "get_scene_status") {
      const avatar = current.nodes.find((node) => node.id === "avatar");
      return {
        request_id, action, ok: true,
        agent_position: avatar ? { x: avatar.x, y: avatar.y } : undefined,
        reachable_objects: avatar ? current.nodes.filter((node) => node.id !== "avatar" && distance(avatar, node) <= (node.reach || Math.min(current.world.reach_ceiling, 60 + Math.max(node.width, node.depth) / 2))).map((node) => node.label || node.id) : [],
      };
    }
    return { request_id, action, ok: false, error: `Unknown frontend action: ${String(action)}` };
  }, [commit]);

  const value = useMemo(() => ({ stage, motionDurationMs, setStageFromServer, handleFrontendAction }), [stage, motionDurationMs, setStageFromServer, handleFrontendAction]);
  return <VrStageContext.Provider value={value}>{children}</VrStageContext.Provider>;
}

export function useVrStage() {
  const value = useContext(VrStageContext);
  if (!value) throw new Error("useVrStage must be used inside VrStageProvider");
  return value;
}
