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
  summary?: string;
}

export interface VrStageNode {
  id: string;
  type: "furniture" | "avatar" | "prop";
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
  interaction?: StageInteraction & { grabbable?: boolean };
  reach?: number;
  yaw?: number;
  support_id?: string;
}

export interface VrStageDocument {
  version: 3;
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

export type VrTransientState = "world_locked" | "held_by_agent" | "offered" | "held_by_user";
export interface VrTransientEntity {
  id: string;
  kind: "panel" | "email_card";
  state: VrTransientState;
  x: number;
  y: number;
  elevation: number;
  yaw: number;
  title: string;
  blocks: string[];
  sender?: string;
  subject?: string;
  summary?: string;
  body?: string;
  page?: number;
}

export interface VrUserPose {
  x: number;
  y: number;
  head_height: number;
  yaw: number;
  sequence: number;
  updated_at: number;
}

export type VrPropInteractionState = "held_by_agent" | "offered";

export interface VrAvatarMotionPlan {
  sequence: number;
  from: { x: number; y: number };
  to: { x: number; y: number };
  turnDurationMs: number;
  walkDurationMs: number;
  finalTurnDurationMs: number;
  finalFacePoint?: { x: number; y: number };
  totalDurationMs: number;
}

interface VrStageValue {
  stage: VrStageDocument | null;
  motionPlan: VrAvatarMotionPlan | null;
  transients: VrTransientEntity[];
  userPose: VrUserPose | null;
  propStates: Record<string, VrPropInteractionState>;
  setUserPose: (pose: VrUserPose) => void;
  updateTransient: (entity: VrTransientEntity) => void;
  removeTransient: (entityId: string) => void;
  updatePropState: (entityId: string, state: VrPropInteractionState | null) => void;
  resetSessionEntities: () => void;
  setStageFromServer: (value: unknown) => VrFrontendActionResult;
  handleFrontendAction: (request: FrontendActionRequest) => Promise<VrFrontendActionResult>;
}

const VrStageContext = createContext<VrStageValue | null>(null);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

const panelBlockLines = (value: unknown): string[] => {
  if (typeof value === "string") return [value];
  if (!value || typeof value !== "object") return [];
  const block = value as Record<string, unknown>;
  if (block.type === "text" && typeof block.text === "string") return [block.text];
  if (block.type === "list" && Array.isArray(block.items)) return block.items.filter((item): item is string => typeof item === "string").map((item) => `• ${item}`);
  if (block.type === "key_value" && block.items && typeof block.items === "object" && !Array.isArray(block.items)) {
    return Object.entries(block.items as Record<string, unknown>).filter((entry): entry is [string, string] => typeof entry[1] === "string").map(([key, item]) => `${key}: ${item}`);
  }
  return [];
};

export function validateVrStage(value: unknown): value is VrStageDocument {
  return validateVrStageShape(value);
}

const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => (
  Math.hypot(a.x - b.x, a.y - b.y)
);

export const VR_TURN_DURATION_MS = 600;

export const createVrMotionPlan = (
  sequence: number,
  from: { x: number; y: number },
  to: { x: number; y: number },
  moveSpeed: number,
  finalFacePoint?: { x: number; y: number },
): VrAvatarMotionPlan => {
  const travelDistance = distance(from, to);
  const walkDurationMs = travelDistance > 0
    ? Math.round(Math.max(250, travelDistance / moveSpeed * 1000))
    : 0;
  const turnDurationMs = walkDurationMs > 0 ? VR_TURN_DURATION_MS : 0;
  const finalTurnDurationMs = finalFacePoint ? VR_TURN_DURATION_MS : 0;
  return {
    sequence,
    from: { x: from.x, y: from.y },
    to: { x: to.x, y: to.y },
    turnDurationMs,
    walkDurationMs,
    finalTurnDurationMs,
    finalFacePoint,
    totalDurationMs: turnDurationMs + walkDurationMs + finalTurnDurationMs,
  };
};

export const endpointError = (stage: VrStageDocument, x: number, y: number) => {
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

export const parseVrMovePath = (args: Record<string, unknown>) => {
  const path = Array.isArray(args.waypoints)
    ? args.waypoints.filter((item): item is { x: number; y: number } => Boolean(
      item && typeof item === "object" &&
      finite((item as { x?: unknown }).x) && finite((item as { y?: unknown }).y),
    ))
    : [];
  if (!path.length && finite(args.x) && finite(args.y)) path.push({ x: args.x, y: args.y });
  return path;
};

export const successfulVrMoveResult = (
  requestId: string | undefined,
  state: VrStageDocument,
  elapsed: number,
  position: { x: number; y: number },
): VrFrontendActionResult => ({
  request_id: requestId,
  action: "move_to",
  ok: true,
  state,
  arrived: true,
  duration_ms: elapsed,
  position,
});

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
  const [motionPlan, setMotionPlan] = useState<VrAvatarMotionPlan | null>(null);
  const [transients, setTransients] = useState<VrTransientEntity[]>([]);
  const [userPose, setUserPoseState] = useState<VrUserPose | null>(null);
  const [propStates, setPropStates] = useState<Record<string, VrPropInteractionState>>({});
  const stageRef = useRef<VrStageDocument | null>(null);
  const defaultRef = useRef<VrStageDocument | null>(null);
  const transientRef = useRef<VrTransientEntity[]>([]);
  const userPoseRef = useRef<VrUserPose | null>(null);
  const propStateRef = useRef<Record<string, VrPropInteractionState>>({});
  const motionSequenceRef = useRef(0);

  const setUserPose = useCallback((pose: VrUserPose) => {
    userPoseRef.current = pose;
    setUserPoseState(pose);
  }, []);
  const updateTransient = useCallback((entity: VrTransientEntity) => {
    transientRef.current = [...transientRef.current.filter((item) => item.id !== entity.id), entity].slice(-3);
    setTransients(transientRef.current);
  }, []);
  const removeTransient = useCallback((entityId: string) => {
    transientRef.current = transientRef.current.filter((item) => item.id !== entityId);
    setTransients(transientRef.current);
  }, []);
  const updatePropState = useCallback((entityId: string, state: VrPropInteractionState | null) => {
    const next = { ...propStateRef.current };
    if (state) next[entityId] = state;
    else delete next[entityId];
    propStateRef.current = next;
    setPropStates(next);
  }, []);
  const resetSessionEntities = useCallback(() => {
    transientRef.current = [];
    propStateRef.current = {};
    setTransients([]);
    setPropStates({});
  }, []);

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
          actions: ["stage_get_state", "stage_set_state", "stage_patch_state", "stage_reset_state", "stage_list_capabilities", "get_object_position", "move_to", "list_objects", "get_scene_status", "list_physical_entities", "show_vr_panel", "materialize_email", "pick_up_entity", "put_down_entity", "offer_entity", "dismiss_entity", "get_user_location", "move_to_user"],
          node_types: ["avatar", "furniture", "prop"], version: 3,
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
      const path = parseVrMovePath(args);
      if (!path.length) return { request_id, action, ok: false, error: "move_to requires a destination." };
      const target = typeof args.target === "string"
        ? current.nodes.find((node) => node.id === args.target || node.label === args.target)
        : undefined;
      let elapsed = 0;
      for (const [index, point] of path.entries()) {
        const error = endpointError(stageRef.current || current, point.x, point.y);
        if (error) return { request_id, action, ok: false, error, state: stageRef.current || current };
        const active = stageRef.current || current;
        const avatar = active.nodes.find((node) => node.id === "avatar")!;
        motionSequenceRef.current += 1;
        const plan = createVrMotionPlan(
          motionSequenceRef.current,
          avatar,
          point,
          active.world.move_speed,
          index === path.length - 1 && target
            ? { x: target.x, y: target.y }
            : undefined,
        );
        setMotionPlan(plan);
        const next = mergePatch(active, { nodes: [{ id: "avatar", x: point.x, y: point.y, pose: undefined }] });
        const result = commit(next, action, request_id);
        if (!result.ok) return result;
        elapsed += plan.totalDurationMs;
        await new Promise<void>((resolve) => window.setTimeout(resolve, plan.totalDurationMs));
      }
      const finalPoint = path[path.length - 1];
      return successfulVrMoveResult(request_id, stageRef.current || current, elapsed, finalPoint);
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
    if (action === "list_physical_entities") {
      const props = current.nodes.filter((node) => node.type === "prop").map((node) => `${node.id} (prop, on ${node.support_id || "floor"})`);
      const temporary = transientRef.current.map((entity) => `${entity.id} (${entity.kind}, ${entity.state})`);
      return { request_id, action, ok: true, summary: [...props, ...temporary].join("\n") || "No physical entities are present." };
    }
    const freshPose = () => {
      const pose = userPoseRef.current;
      return pose && performance.now() - pose.updated_at <= 3000 ? pose : null;
    };
    const inFrontOfUser = (pose: VrUserPose, distanceCm = 120) => {
      const radians = pose.yaw * Math.PI / 180;
      return { x: pose.x + Math.sin(radians) * distanceCm, y: pose.y - Math.cos(radians) * distanceCm };
    };
    if (action === "show_vr_panel") {
      const pose = freshPose();
      if (!pose) return { request_id, action, ok: false, error: "No fresh VR user pose is available." };
      if (transientRef.current.length >= 3) return { request_id, action, ok: false, error: "The temporary-entity limit is reached." };
      const point = inFrontOfUser(pose);
      const entity: VrTransientEntity = {
        id: `panel_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`,
        kind: "panel", state: "world_locked", x: point.x, y: point.y,
        elevation: Math.max(90, pose.head_height * 100 - 20), yaw: pose.yaw + 180,
        title: String(args.title || "Information").slice(0, 120),
        blocks: Array.isArray(args.blocks) ? args.blocks.slice(0, 6).flatMap(panelBlockLines) : [],
      };
      updateTransient(entity);
      return { request_id, action, ok: true, summary: `Created ${entity.id} in front of the user.` };
    }
    if (action === "materialize_email") {
      if (transientRef.current.length >= 3) return { request_id, action, ok: false, error: "The temporary-entity limit is reached." };
      const mailbox = current.nodes.find((node) => node.id === "mailbox");
      if (!mailbox) return { request_id, action, ok: false, error: "The VR room has no mailbox." };
      const entity: VrTransientEntity = {
        id: `email_card_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`,
        kind: "email_card", state: "world_locked", x: mailbox.x, y: mailbox.y + mailbox.depth / 2 + 20,
        elevation: mailbox.elevation, yaw: 0, title: String(args.subject || "Email").slice(0, 200), blocks: [],
        sender: String(args.sender || "").slice(0, 320), subject: String(args.subject || "").slice(0, 200),
        summary: String(args.summary || "").slice(0, 280), body: String(args.body || "").slice(0, 8000), page: 0,
      };
      updateTransient(entity);
      return { request_id, action, ok: true, summary: `Created ${entity.id} at the mailbox.` };
    }
    if (["pick_up_entity", "put_down_entity", "offer_entity", "dismiss_entity"].includes(String(action))) {
      const entityId = String(args.entity_id || "");
      const entity = transientRef.current.find((item) => item.id === entityId);
      const prop = current.nodes.find((item) => item.id === entityId && item.type === "prop" && item.interaction?.grabbable === true);
      if (!entity && !prop) return { request_id, action, ok: false, error: `Unknown grabbable entity: ${entityId}` };
      if (prop) {
        if (action === "dismiss_entity") return { request_id, action, ok: false, error: "Persistent props cannot be dismissed." };
        const avatar = current.nodes.find((node) => node.id === "avatar")!;
        const state = propStateRef.current[entityId];
        if (action === "pick_up_entity") {
          if (distance(avatar, prop) > 120) return { request_id, action, ok: false, error: "The agent is not close enough to pick up that prop." };
          updatePropState(entityId, "held_by_agent");
          return { request_id, action, ok: true, summary: `The agent picked up ${entityId}.` };
        }
        if (state !== "held_by_agent" && state !== "offered") return { request_id, action, ok: false, error: "The prop is not held by the agent." };
        if (action === "offer_entity") {
          if (!freshPose()) return { request_id, action, ok: false, error: "No fresh VR user pose is available." };
          updatePropState(entityId, "offered");
          return { request_id, action, ok: true, summary: `The agent is offering ${entityId}; acceptance is not assumed.` };
        }
        const target = current.nodes.find((node) => node.type === "furniture" && (node.id === args.target || node.label === args.target));
        const radians = Math.atan2(avatar.x - current.room.width / 2, current.room.depth / 2 - avatar.y);
        const placed = target
          ? { ...prop, x: target.x, y: target.y, elevation: target.elevation + target.height / 2 + prop.height / 2, support_id: target.id }
          : { ...prop, x: avatar.x + Math.sin(radians) * 55, y: avatar.y - Math.cos(radians) * 55, elevation: prop.height / 2, support_id: "floor" };
        const committed = commit(mergePatch(current, { nodes: [placed] }), action, request_id);
        if (!committed.ok) return { ...committed, error: "The requested prop placement failed support or room validation." };
        updatePropState(entityId, null);
        return { ...committed, summary: `Placed ${entityId} on ${target?.label || args.target || "the floor"}.` };
      }
      if (!entity) return { request_id, action, ok: false, error: `Unknown temporary entity: ${entityId}` };
      if (action === "dismiss_entity") {
        removeTransient(entityId);
        return { request_id, action, ok: true, summary: `Dismissed ${entityId}.` };
      }
      const avatar = current.nodes.find((node) => node.id === "avatar")!;
      if (action === "pick_up_entity") {
        if (distance(avatar, entity) > 120) return { request_id, action, ok: false, error: "The agent is not close enough to pick up that entity." };
        updateTransient({ ...entity, state: "held_by_agent" });
        return { request_id, action, ok: true, summary: `The agent picked up ${entityId}.` };
      }
      if (entity.state !== "held_by_agent" && entity.state !== "offered") return { request_id, action, ok: false, error: "The entity is not held by the agent." };
      if (action === "offer_entity") {
        if (!freshPose()) return { request_id, action, ok: false, error: "No fresh VR user pose is available." };
        updateTransient({ ...entity, state: "offered" });
        return { request_id, action, ok: true, summary: `The agent is offering ${entityId}; acceptance is not assumed.` };
      }
      const target = current.nodes.find((node) => node.id === args.target || node.label === args.target);
      updateTransient({ ...entity, state: "world_locked", x: target?.x ?? avatar.x, y: target?.y ?? avatar.y, elevation: target ? target.elevation + target.height / 2 + 35 : 70 });
      return { request_id, action, ok: true, summary: `Placed ${entityId} on ${target?.label || args.target || "the floor"}.` };
    }
    if (action === "get_user_location") {
      const pose = freshPose();
      if (!pose) return { request_id, action, ok: false, error: "No fresh VR user pose is available." };
      const nearest = current.nodes.filter((node) => node.type === "furniture").sort((a, b) => distance(a, pose) - distance(b, pose))[0];
      return { request_id, action, ok: true, summary: `User is at x=${pose.x.toFixed(0)}, y=${pose.y.toFixed(0)}, yaw=${pose.yaw.toFixed(0)} degrees${nearest ? `, near ${nearest.label || nearest.id}` : ""}.` };
    }
    if (action === "move_to_user") {
      let pose = freshPose();
      if (!pose) return { request_id, action, ok: false, error: "No fresh VR user pose is available." };
      let totalDuration = 0;
      let lastTarget: { x: number; y: number } | null = null;
      let result: VrFrontendActionResult = { request_id, action, ok: false };
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const active = stageRef.current || current;
        const candidates = [100, 120, 140].flatMap((radius) => [0, 30, -30, 60, -60].map((offset) => {
          const radians = (pose!.yaw + offset) * Math.PI / 180;
          return { x: pose!.x + Math.sin(radians) * radius, y: pose!.y - Math.cos(radians) * radius };
        }));
        const target = candidates.find((point) => !endpointError(active, point.x, point.y));
        if (!target) return { request_id, action, ok: false, error: "No safe conversational position exists near the user." };
        const avatar = active.nodes.find((node) => node.id === "avatar")!;
        motionSequenceRef.current += 1;
        const plan = createVrMotionPlan(
          motionSequenceRef.current,
          avatar,
          target,
          active.world.move_speed,
          { x: pose.x, y: pose.y },
        );
        setMotionPlan(plan);
        result = commit(mergePatch(active, { nodes: [{ id: "avatar", ...target, pose: undefined }] }), action, request_id);
        if (!result.ok) return result;
        await new Promise<void>((resolve) => window.setTimeout(resolve, plan.totalDurationMs));
        totalDuration += plan.totalDurationMs;
        lastTarget = target;
        const latest = freshPose();
        if (!latest || distance(latest, pose) <= 75) break;
        if (attempt === 1) return { ...result, ok: false, error: "The user moved while the agent approached; no arrival is claimed." };
        pose = latest;
      }
      return { ...result, arrived: true, duration_ms: totalDuration, position: lastTarget || undefined, summary: "The agent moved to a safe position near the user." };
    }
    return { request_id, action, ok: false, error: `Unknown frontend action: ${String(action)}` };
  }, [commit, removeTransient, updatePropState, updateTransient]);

  const value = useMemo(() => ({ stage, motionPlan, transients, userPose, propStates, setUserPose, updateTransient, removeTransient, updatePropState, resetSessionEntities, setStageFromServer, handleFrontendAction }), [stage, motionPlan, transients, userPose, propStates, setUserPose, updateTransient, removeTransient, updatePropState, resetSessionEntities, setStageFromServer, handleFrontendAction]);
  return <VrStageContext.Provider value={value}>{children}</VrStageContext.Provider>;
}

export function useVrStage() {
  const value = useContext(VrStageContext);
  if (!value) throw new Error("useVrStage must be used inside VrStageProvider");
  return value;
}
