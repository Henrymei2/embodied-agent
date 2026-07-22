import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

export type StageNodeType = "shape" | "text" | "image" | "avatar" | "panel";
export type StageAssetFit = "contain" | "cover";
export type StageDirection = "down" | "left" | "right" | "up";

export interface StagePosition {
  x: number;
  y: number;
}

export interface StageSize {
  width: number;
  height: number;
}

export interface StageNode {
  id: string;
  type: StageNodeType;
  label?: string;
  x: number;
  y: number;
  width: number;
  height: number;
  zIndex?: number;
  text?: string;
  src?: string;
  alt?: string;
  texture?: StageTexture;
  spritesheet?: StageSpritesheet;
  uiWindow?: FurnitureWindowConfig;
  style?: StageNodeStyle;
}

// Live data sources a furniture hover window may render. Each one is backed by
// a read-only backend route the window polls while it is open. Keep in sync
// with UI_WINDOW_PROVIDERS in stage_state_manager.py.
export const UI_WINDOW_PROVIDERS = [
  "google_calendar_status",
  "todo_list",
  "timer_status",
] as const;

export type FurnitureWindowProvider = (typeof UI_WINDOW_PROVIDERS)[number];

export interface FurnitureWindowConfig {
  title: string;
  body?: string;
  provider?: FurnitureWindowProvider;
}

export interface StageTexture {
  src: string;
  fit?: StageAssetFit;
  alt?: string;
}

export interface StageSpriteAnimation {
  row: number;
  frames: number[];
}

export interface StageSpritesheet {
  src: string;
  frameWidth: number;
  frameHeight: number;
  fps?: number;
  animations: Record<string, StageSpriteAnimation>;
  defaultAnimations?: Partial<Record<"idle" | "walk", string>>;
}

export interface StageNodeStyle {
  background?: string;
  borderColor?: string;
  color?: string;
  borderRadius?: number;
  opacity?: number;
  fontSize?: number;
  fontWeight?: "400" | "500" | "600" | "700" | "800";
}

export interface DynamicStageDocument {
  version: 4;
  canvas: {
    width: number;
    height: number;
    background: string;
    theme?: Record<string, string>;
  };
  nodes: StageNode[];
  metadata: {
    conf_uid?: string;
    created_at?: string;
    updated_at?: string;
    updated_by?: string;
  };
}

export interface StagePatch {
  canvas?: Partial<DynamicStageDocument["canvas"]>;
  nodes?: StageNode[];
  remove_node_ids?: string[];
  metadata?: Partial<DynamicStageDocument["metadata"]>;
}

export interface FrontendActionRequest {
  request_id?: string;
  action?: string;
  args?: Record<string, unknown>;
}

export interface FrontendActionResult {
  request_id?: string;
  action?: string;
  ok: boolean;
  error?: string;
  errors?: string[];
  state?: DynamicStageDocument;
  summary?: string;
  position?: StagePosition;
  objects?: StageNode[];
  agent_position?: StagePosition;
  reachable_objects?: string[];
  capabilities?: {
    actions: string[];
    node_types: StageNodeType[];
    version: number;
    texture: string;
    spritesheet: string;
    furniture_window: string;
    move_duration_ms: number;
    patch_shape: string;
  };
}

interface DynamicStageContextValue {
  stage: DynamicStageDocument;
  validationErrors: string[];
  setStageFromServer: (stage: unknown) => FrontendActionResult;
  handleFrontendAction: (
    request: FrontendActionRequest,
  ) => Promise<FrontendActionResult>;
}

const ROOM_WIDTH = 960;
const ROOM_HEIGHT = 540;
const STAGE_VERSION = 4;
const DEFAULT_MOVE_DURATION_MS = 900;
const REACHABLE_DISTANCE = 150;

const nowIso = () => new Date().toISOString();

export const createDefaultStage = (confUid?: string): DynamicStageDocument => ({
  version: STAGE_VERSION,
  canvas: {
    width: ROOM_WIDTH,
    height: ROOM_HEIGHT,
    background: "linear-gradient(180deg, #d7ecf3 0%, #d7ecf3 48%, #c6a983 48%, #c6a983 100%)",
    theme: {
      floorLine: "rgba(88, 67, 48, 0.32)",
      floorStripe: "rgba(104, 75, 46, 0.16)",
    },
  },
  nodes: [
    {
      id: "computer",
      type: "panel",
      label: "computer",
      x: 260,
      y: 255,
      width: 150,
      height: 104,
      zIndex: 2,
      text: "computer",
      texture: {
        src: "./stage-assets/computer.png",
        fit: "contain",
        alt: "computer",
      },
      style: {
        background: "rgba(79, 70, 229, 0.88)",
        borderColor: "rgba(199, 210, 254, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 14,
        fontWeight: "700",
      },
    },
    {
      id: "calendar",
      type: "panel",
      label: "calendar",
      x: 480,
      y: 420,
      width: 118,
      height: 92,
      zIndex: 2,
      text: "calendar",
      texture: {
        src: "./stage-assets/calendar.png",
        fit: "contain",
        alt: "calendar",
      },
      uiWindow: {
        title: "Google Calendar",
        body: "Connection status for the primary calendar.",
        provider: "google_calendar_status",
      },
      style: {
        background: "rgba(13, 148, 136, 0.9)",
        borderColor: "rgba(153, 246, 228, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 14,
        fontWeight: "700",
      },
    },
    {
      id: "bed",
      type: "panel",
      label: "bed",
      x: 700,
      y: 270,
      width: 188,
      height: 118,
      zIndex: 2,
      text: "bed",
      texture: {
        src: "./stage-assets/bed.png",
        fit: "contain",
        alt: "bed",
      },
      style: {
        background: "rgba(225, 29, 72, 0.86)",
        borderColor: "rgba(254, 205, 211, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 14,
        fontWeight: "700",
      },
    },
    {
      id: "todo_list",
      type: "panel",
      label: "todo_list",
      x: 110,
      y: 420,
      width: 112,
      height: 132,
      zIndex: 2,
      text: "todo list",
      uiWindow: {
        title: "Todo list",
        body: "Open items the agent is tracking.",
        provider: "todo_list",
      },
      style: {
        background: "rgba(217, 119, 6, 0.9)",
        borderColor: "rgba(253, 230, 138, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 14,
        fontWeight: "700",
      },
    },
    {
      id: "hourglass",
      type: "panel",
      label: "hourglass",
      x: 860,
      y: 150,
      width: 72,
      height: 100,
      zIndex: 2,
      text: "hourglass",
      uiWindow: {
        title: "Timers",
        body: "Timers the agent has set.",
        provider: "timer_status",
      },
      style: {
        background: "rgba(124, 58, 237, 0.88)",
        borderColor: "rgba(221, 214, 254, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "window",
      type: "panel",
      label: "window",
      x: 110,
      y: 140,
      width: 140,
      height: 96,
      zIndex: 2,
      text: "window",
      uiWindow: {
        title: "Window",
        body: "The agent checks the weather from here, using a public forecast service that needs no account.",
      },
      style: {
        background: "rgba(56, 189, 248, 0.85)",
        borderColor: "rgba(224, 242, 254, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 14,
        fontWeight: "700",
      },
    },
    {
      id: "avatar",
      type: "avatar",
      label: "avatar",
      x: 480,
      y: 270,
      width: 70,
      height: 92,
      zIndex: 3,
      text: "avatar",
      spritesheet: {
        src: "./stage-assets/avatar.png",
        frameWidth: 48,
        frameHeight: 64,
        fps: 6,
        defaultAnimations: {
          idle: "idle_down",
          walk: "walk_down",
        },
        animations: {
          idle_down: { row: 0, frames: [0, 1, 2, 1] },
          idle_left: { row: 1, frames: [0, 1, 2, 1] },
          idle_right: { row: 2, frames: [0, 1, 2, 1] },
          idle_up: { row: 3, frames: [0, 1, 2, 1] },
          walk_down: { row: 4, frames: [0, 1, 2, 3] },
          walk_left: { row: 5, frames: [0, 1, 2, 3] },
          walk_right: { row: 6, frames: [0, 1, 2, 3] },
          walk_up: { row: 7, frames: [0, 1, 2, 3] },
        },
      },
      style: {
        background: "#2563eb",
        borderColor: "rgba(191, 219, 254, 0.9)",
        color: "#172554",
        fontSize: 12,
        fontWeight: "700",
      },
    },
  ],
  metadata: {
    conf_uid: confUid,
    created_at: nowIso(),
    updated_at: nowIso(),
    updated_by: "default",
  },
});

const DynamicStageContext = createContext<DynamicStageContextValue | null>(null);

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === "object" && value !== null && !Array.isArray(value)
);

const isFiniteNumber = (value: unknown): value is number => (
  typeof value === "number" && Number.isFinite(value)
);

const isStageNodeType = (value: unknown): value is StageNodeType => (
  value === "shape" ||
  value === "text" ||
  value === "image" ||
  value === "avatar" ||
  value === "panel"
);

const isAssetFit = (value: unknown): value is StageAssetFit => (
  value === undefined || value === "contain" || value === "cover"
);

const isSafeText = (value: unknown, maxLength = 500) => (
  value === undefined || (typeof value === "string" && value.length <= maxLength)
);

const isSafeColor = (value: unknown) => (
  value === undefined ||
  (
    typeof value === "string" &&
    value.length <= 180 &&
    (/^(#|rgb|rgba|hsl|hsla|linear-gradient|[a-zA-Z])/.test(value))
  )
);

const isSafeAssetSrc = (value: unknown) => (
  typeof value === "string" &&
  value.length > 0 &&
  value.length <= 1200 &&
  !/^javascript:/i.test(value) &&
  (
    /^https?:\/\//i.test(value) ||
    /^data:image\/(?:png|jpeg|jpg|webp|gif|svg\+xml);base64,/i.test(value) ||
    /^(?:\.{0,2}\/)?[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*$/.test(value)
  )
);

const validateTexture = (texture: unknown, nodeId: string, errors: string[]) => {
  if (texture === undefined) {
    return;
  }
  if (!isRecord(texture)) {
    errors.push(`Node ${nodeId} texture must be an object.`);
    return;
  }
  const allowedKeys = new Set(["src", "fit", "alt"]);
  Object.keys(texture).forEach((key) => {
    if (!allowedKeys.has(key)) {
      errors.push(`Node ${nodeId} texture.${key} is not allowed.`);
    }
  });
  if (!isSafeAssetSrc(texture.src)) {
    errors.push(`Node ${nodeId} texture.src is invalid.`);
  }
  if (!isAssetFit(texture.fit)) {
    errors.push(`Node ${nodeId} texture.fit must be contain or cover.`);
  }
  if (!isSafeText(texture.alt, 160)) {
    errors.push(`Node ${nodeId} texture.alt contains unsafe text length.`);
  }
};

const validateUiWindow = (uiWindow: unknown, nodeId: string, errors: string[]) => {
  if (uiWindow === undefined) return;
  if (!isRecord(uiWindow)) {
    errors.push(`Node ${nodeId} uiWindow must be an object.`);
    return;
  }
  const allowedKeys = new Set(["title", "body", "provider"]);
  Object.keys(uiWindow).forEach((key) => {
    if (!allowedKeys.has(key)) errors.push(`Node ${nodeId} uiWindow.${key} is not allowed.`);
  });
  if (typeof uiWindow.title !== "string" || uiWindow.title.trim().length === 0 || uiWindow.title.length > 120 || /[<>]/.test(uiWindow.title)) {
    errors.push(`Node ${nodeId} uiWindow.title must contain 1-120 characters.`);
  }
  if (!isSafeText(uiWindow.body, 500) || (typeof uiWindow.body === "string" && /[<>]/.test(uiWindow.body))) {
    errors.push(`Node ${nodeId} uiWindow.body contains unsafe text.`);
  }
  if (
    uiWindow.provider !== undefined &&
    !UI_WINDOW_PROVIDERS.includes(uiWindow.provider as FurnitureWindowProvider)
  ) {
    errors.push(`Node ${nodeId} uiWindow.provider is not supported.`);
  }
};

const validateSpritesheet = (spritesheet: unknown, nodeId: string, errors: string[]) => {
  if (spritesheet === undefined) {
    return;
  }
  if (!isRecord(spritesheet)) {
    errors.push(`Node ${nodeId} spritesheet must be an object.`);
    return;
  }
  const allowedKeys = new Set([
    "src",
    "frameWidth",
    "frameHeight",
    "fps",
    "animations",
    "defaultAnimations",
  ]);
  Object.keys(spritesheet).forEach((key) => {
    if (!allowedKeys.has(key)) {
      errors.push(`Node ${nodeId} spritesheet.${key} is not allowed.`);
    }
  });
  if (!isSafeAssetSrc(spritesheet.src)) {
    errors.push(`Node ${nodeId} spritesheet.src is invalid.`);
  }
  if (!isFiniteNumber(spritesheet.frameWidth) || spritesheet.frameWidth < 1 || spritesheet.frameWidth > 1024) {
    errors.push(`Node ${nodeId} spritesheet.frameWidth must be 1-1024.`);
  }
  if (!isFiniteNumber(spritesheet.frameHeight) || spritesheet.frameHeight < 1 || spritesheet.frameHeight > 1024) {
    errors.push(`Node ${nodeId} spritesheet.frameHeight must be 1-1024.`);
  }
  if (spritesheet.fps !== undefined && (!isFiniteNumber(spritesheet.fps) || spritesheet.fps < 1 || spritesheet.fps > 30)) {
    errors.push(`Node ${nodeId} spritesheet.fps must be 1-30.`);
  }
  if (!isRecord(spritesheet.animations) || Object.keys(spritesheet.animations).length === 0) {
    errors.push(`Node ${nodeId} spritesheet.animations must be a non-empty object.`);
  } else if (Object.keys(spritesheet.animations).length > 64) {
    errors.push(`Node ${nodeId} spritesheet.animations can contain at most 64 animations.`);
  } else {
    Object.entries(spritesheet.animations).forEach(([name, animation]) => {
      if (!/^[A-Za-z0-9_-]{1,80}$/.test(name)) {
        errors.push(`Node ${nodeId} spritesheet animation name ${name} is invalid.`);
      }
      if (!isRecord(animation)) {
        errors.push(`Node ${nodeId} spritesheet animation ${name} must be an object.`);
        return;
      }
      if (!isFiniteNumber(animation.row) || animation.row < 0 || animation.row > 256) {
        errors.push(`Node ${nodeId} spritesheet animation ${name}.row must be 0-256.`);
      }
      if (!Array.isArray(animation.frames) || animation.frames.length === 0 || animation.frames.length > 120) {
        errors.push(`Node ${nodeId} spritesheet animation ${name}.frames must contain 1-120 frames.`);
      } else {
        animation.frames.forEach((frame) => {
          if (!isFiniteNumber(frame) || frame < 0 || frame > 256 || Math.floor(frame) !== frame) {
            errors.push(`Node ${nodeId} spritesheet animation ${name}.frames contains an invalid frame.`);
          }
        });
      }
    });
  }
  if (spritesheet.defaultAnimations !== undefined) {
    if (!isRecord(spritesheet.defaultAnimations)) {
      errors.push(`Node ${nodeId} spritesheet.defaultAnimations must be an object.`);
    } else {
      Object.entries(spritesheet.defaultAnimations).forEach(([key, value]) => {
        if (!["idle", "walk"].includes(key) || typeof value !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(value)) {
          errors.push(`Node ${nodeId} spritesheet.defaultAnimations.${key} is invalid.`);
        }
      });
    }
  }
};

const migrateStageDocument = (candidate: unknown): unknown => {
  if (!isRecord(candidate)) {
    return candidate;
  }
  if (candidate.version === STAGE_VERSION) {
    return candidate;
  }
  if (candidate.version !== 1 && candidate.version !== 2 && candidate.version !== 3) {
    return candidate;
  }

  const defaults = createDefaultStage(
    isRecord(candidate.metadata) && typeof candidate.metadata.conf_uid === "string"
      ? candidate.metadata.conf_uid
      : undefined,
  );
  const defaultNodes = new Map(defaults.nodes.map((node) => [node.id, node]));
  const nodes = Array.isArray(candidate.nodes)
    ? candidate.nodes.map((node) => {
      if (!isRecord(node) || typeof node.id !== "string") {
        return node;
      }
      const defaultNode = defaultNodes.get(node.id);
      if (!defaultNode) {
        return node;
      }
      return {
        ...defaultNode,
        ...node,
        texture: node.texture ?? defaultNode.texture,
        spritesheet: node.spritesheet ?? defaultNode.spritesheet,
        uiWindow: node.uiWindow ?? defaultNode.uiWindow,
        style: {
          ...(defaultNode.style || {}),
          ...(isRecord(node.style) ? node.style : {}),
        },
      };
    })
    : defaults.nodes;

  defaultNodes.forEach((defaultNode, id) => {
    if (!nodes.some((node) => isRecord(node) && node.id === id)) {
      nodes.push(defaultNode);
    }
  });

  return {
    ...defaults,
    ...candidate,
    version: STAGE_VERSION,
    canvas: {
      ...defaults.canvas,
      ...(isRecord(candidate.canvas) ? candidate.canvas : {}),
    },
    nodes,
    metadata: {
      ...defaults.metadata,
      ...(isRecord(candidate.metadata) ? candidate.metadata : {}),
      updated_at: nowIso(),
      updated_by: "migration",
    },
  };
};

const validateNodeStyle = (style: unknown, nodeId: string, errors: string[]) => {
  if (style === undefined) {
    return;
  }
  if (!isRecord(style)) {
    errors.push(`Node ${nodeId} style must be an object.`);
    return;
  }

  const allowedKeys = new Set([
    "background",
    "borderColor",
    "color",
    "borderRadius",
    "opacity",
    "fontSize",
    "fontWeight",
  ]);
  Object.keys(style).forEach((key) => {
    if (!allowedKeys.has(key)) {
      errors.push(`Node ${nodeId} style.${key} is not allowed.`);
    }
  });

  if (!isSafeColor(style.background)) {
    errors.push(`Node ${nodeId} style.background is invalid.`);
  }
  if (!isSafeColor(style.borderColor)) {
    errors.push(`Node ${nodeId} style.borderColor is invalid.`);
  }
  if (!isSafeColor(style.color)) {
    errors.push(`Node ${nodeId} style.color is invalid.`);
  }
  if (style.borderRadius !== undefined && (!isFiniteNumber(style.borderRadius) || style.borderRadius < 0 || style.borderRadius > 80)) {
    errors.push(`Node ${nodeId} style.borderRadius must be 0-80.`);
  }
  if (style.opacity !== undefined && (!isFiniteNumber(style.opacity) || style.opacity < 0 || style.opacity > 1)) {
    errors.push(`Node ${nodeId} style.opacity must be 0-1.`);
  }
  if (style.fontSize !== undefined && (!isFiniteNumber(style.fontSize) || style.fontSize < 8 || style.fontSize > 96)) {
    errors.push(`Node ${nodeId} style.fontSize must be 8-96.`);
  }
  if (style.fontWeight !== undefined && !["400", "500", "600", "700", "800"].includes(String(style.fontWeight))) {
    errors.push(`Node ${nodeId} style.fontWeight is invalid.`);
  }
};

export const validateStageDocument = (candidate: unknown): {
  ok: boolean;
  errors: string[];
  stage?: DynamicStageDocument;
} => {
  candidate = migrateStageDocument(candidate);
  const errors: string[] = [];
  if (!isRecord(candidate)) {
    return { ok: false, errors: ["Stage document must be an object."] };
  }

  if (candidate.version !== STAGE_VERSION) {
    errors.push(`Stage version must be ${STAGE_VERSION}.`);
  }

  if (!isRecord(candidate.canvas)) {
    errors.push("Stage canvas must be an object.");
  } else {
    if (!isFiniteNumber(candidate.canvas.width) || candidate.canvas.width < 320 || candidate.canvas.width > 4096) {
      errors.push("Canvas width must be 320-4096.");
    }
    if (!isFiniteNumber(candidate.canvas.height) || candidate.canvas.height < 240 || candidate.canvas.height > 4096) {
      errors.push("Canvas height must be 240-4096.");
    }
    if (!isSafeColor(candidate.canvas.background)) {
      errors.push("Canvas background is invalid.");
    }
  }

  if (!Array.isArray(candidate.nodes)) {
    errors.push("Stage nodes must be an array.");
  } else if (candidate.nodes.length > 80) {
    errors.push("Stage can contain at most 80 nodes.");
  } else {
    const ids = new Set<string>();
    candidate.nodes.forEach((node, index) => {
      if (!isRecord(node)) {
        errors.push(`Node ${index} must be an object.`);
        return;
      }
      const id = node.id;
      if (typeof id !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(id)) {
        errors.push(`Node ${index} id is invalid.`);
      } else if (ids.has(id)) {
        errors.push(`Node id ${id} is duplicated.`);
      } else {
        ids.add(id);
      }
      if (!isStageNodeType(node.type)) {
        errors.push(`Node ${id || index} type is invalid.`);
      }
      ["x", "y", "width", "height"].forEach((key) => {
        if (!isFiniteNumber(node[key])) {
          errors.push(`Node ${id || index} ${key} must be numeric.`);
        }
      });
      if (isFiniteNumber(node.width) && (node.width < 1 || node.width > 4096)) {
        errors.push(`Node ${id || index} width is out of bounds.`);
      }
      if (isFiniteNumber(node.height) && (node.height < 1 || node.height > 4096)) {
        errors.push(`Node ${id || index} height is out of bounds.`);
      }
      if (node.zIndex !== undefined && (!isFiniteNumber(node.zIndex) || node.zIndex < 0 || node.zIndex > 1000)) {
        errors.push(`Node ${id || index} zIndex must be 0-1000.`);
      }
      if (!isSafeText(node.label, 80) || !isSafeText(node.text) || !isSafeText(node.alt, 160)) {
        errors.push(`Node ${id || index} contains unsafe text length.`);
      }
      if (node.src !== undefined && !isSafeAssetSrc(node.src)) {
        errors.push(`Node ${id || index} src is invalid.`);
      }
      validateTexture(node.texture, String(id || index), errors);
      validateSpritesheet(node.spritesheet, String(id || index), errors);
      validateUiWindow(node.uiWindow, String(id || index), errors);
      validateNodeStyle(node.style, String(id || index), errors);
    });
  }

  if (!isRecord(candidate.metadata)) {
    errors.push("Stage metadata must be an object.");
  }

  if (errors.length > 0) {
    return { ok: false, errors };
  }

  return { ok: true, errors: [], stage: candidate as unknown as DynamicStageDocument };
};

const applyStagePatch = (
  current: DynamicStageDocument,
  patch: StagePatch,
): DynamicStageDocument => {
  const removeIds = new Set(patch.remove_node_ids || []);
  const incomingNodes = new Map((patch.nodes || []).map((node) => [node.id, node]));
  const mergedNodes = current.nodes
    .filter((node) => !removeIds.has(node.id))
    .map((node) => incomingNodes.get(node.id) || node);

  (patch.nodes || []).forEach((node) => {
    if (!current.nodes.some((existing) => existing.id === node.id)) {
      mergedNodes.push(node);
    }
  });

  return {
    ...current,
    canvas: {
      ...current.canvas,
      ...(patch.canvas || {}),
    },
    nodes: mergedNodes,
    metadata: {
      ...current.metadata,
      ...(patch.metadata || {}),
      updated_at: nowIso(),
      updated_by: patch.metadata?.updated_by || "frontend_action",
    },
  };
};

const distanceBetween = (a: StagePosition, b: StagePosition) => {
  const deltaX = a.x - b.x;
  const deltaY = a.y - b.y;
  return Math.sqrt(deltaX * deltaX + deltaY * deltaY);
};

export function DynamicStageProvider({ children }: { children: React.ReactNode }) {
  const [stage, setStage] = useState<DynamicStageDocument>(() => createDefaultStage());
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const stageRef = useRef(stage);

  const setStageSynced = useCallback((nextStage: DynamicStageDocument) => {
    stageRef.current = nextStage;
    setStage(nextStage);
    setValidationErrors([]);
  }, []);

  const setStageFromServer = useCallback((candidate: unknown): FrontendActionResult => {
    const validation = validateStageDocument(candidate);
    if (!validation.ok || !validation.stage) {
      setValidationErrors(validation.errors);
      return { ok: false, errors: validation.errors, error: validation.errors.join(" ") };
    }
    setStageSynced(validation.stage);
    return { ok: true, state: validation.stage, summary: "Stage loaded." };
  }, [setStageSynced]);

  const commitStage = useCallback((candidate: unknown, action?: string, requestId?: string): FrontendActionResult => {
    const validation = validateStageDocument(candidate);
    if (!validation.ok || !validation.stage) {
      setValidationErrors(validation.errors);
      return {
        request_id: requestId,
        action,
        ok: false,
        errors: validation.errors,
        error: validation.errors.join(" "),
      };
    }
    setStageSynced(validation.stage);
    return {
      request_id: requestId,
      action,
      ok: true,
      state: validation.stage,
      summary: "Stage updated.",
    };
  }, [setStageSynced]);

  const handleFrontendAction = useCallback(async ({
    request_id: requestId,
    action,
    args = {},
  }: FrontendActionRequest): Promise<FrontendActionResult> => {
    switch (action) {
      case "stage_get_state":
        return {
          request_id: requestId,
          action,
          ok: true,
          state: stageRef.current,
          summary: "Stage state returned.",
        };

      case "stage_set_state":
        return commitStage(args.state, action, requestId);

      case "stage_patch_state": {
        if (!isRecord(args.patch)) {
          return {
            request_id: requestId,
            action,
            ok: false,
            error: "stage_patch_state requires args.patch.",
          };
        }
        return commitStage(applyStagePatch(stageRef.current, args.patch as StagePatch), action, requestId);
      }

      case "stage_reset_state": {
        const confUid = typeof args.conf_uid === "string"
          ? args.conf_uid
          : stageRef.current.metadata.conf_uid;
        return commitStage(createDefaultStage(confUid), action, requestId);
      }

      case "stage_list_capabilities":
        return {
          request_id: requestId,
          action,
          ok: true,
          capabilities: {
            actions: [
              "stage_get_state",
              "stage_set_state",
              "stage_patch_state",
              "stage_reset_state",
              "stage_list_capabilities",
              "get_object_position",
              "move_to",
              "list_objects",
              "get_scene_status",
            ],
            node_types: ["shape", "text", "image", "avatar", "panel"],
            version: STAGE_VERSION,
            texture: "nodes may include texture: { src, fit, alt }.",
            spritesheet: "avatar nodes may include spritesheet: { src, frameWidth, frameHeight, fps, animations, defaultAnimations }.",
            furniture_window: "nodes may include uiWindow: { title, body?, provider? }; providers are allowlisted.",
            move_duration_ms: DEFAULT_MOVE_DURATION_MS,
            patch_shape: "args.patch may include canvas, nodes, remove_node_ids, metadata. Node assets use safe URL/data-image/relative src strings.",
          },
        };

      case "get_object_position": {
        const name = args.name;
        if (typeof name !== "string") {
          return { request_id: requestId, action, ok: false, error: "get_object_position requires args.name" };
        }
        const object = stageRef.current.nodes.find((node) => node.id === name || node.label === name);
        if (!object) {
          return { request_id: requestId, action, ok: false, error: `Unknown object: ${name}` };
        }
        return { request_id: requestId, action, ok: true, position: { x: object.x, y: object.y } };
      }

      case "move_to": {
        const target = args.target;
        let destination: StagePosition | null = null;
        if (typeof target === "string") {
          const object = stageRef.current.nodes.find((node) => node.id === target || node.label === target);
          if (!object) {
            return { request_id: requestId, action, ok: false, error: `Unknown target: ${target}` };
          }
          destination = { x: object.x, y: object.y };
        } else if (isFiniteNumber(args.x) && isFiniteNumber(args.y)) {
          destination = { x: args.x, y: args.y };
        }
        if (!destination) {
          return {
            request_id: requestId,
            action,
            ok: false,
            error: "move_to requires args.target or numeric args.x and args.y",
          };
        }

        const nextStage = applyStagePatch(stageRef.current, {
          nodes: stageRef.current.nodes.map((node) => (
            node.id === "avatar"
              ? { ...node, x: destination.x, y: destination.y }
            : node
          )),
        });
        const result = commitStage(nextStage, action, requestId);
        if (result.ok) {
          await new Promise<void>((resolve) => {
            window.setTimeout(resolve, DEFAULT_MOVE_DURATION_MS);
          });
        }
        return result;
      }

      case "list_objects": {
        const distance = args.distance;
        if (distance !== undefined && !isFiniteNumber(distance)) {
          return { request_id: requestId, action, ok: false, error: "list_objects args.distance must be numeric" };
        }
        const avatar = stageRef.current.nodes.find((node) => node.id === "avatar");
        const objects = stageRef.current.nodes.filter((node) => (
          node.id !== "avatar" &&
          (
            distance === undefined ||
            !avatar ||
            distanceBetween({ x: avatar.x, y: avatar.y }, { x: node.x, y: node.y }) <= distance
          )
        ));
        return { request_id: requestId, action, ok: true, objects };
      }

      case "get_scene_status": {
        const avatar = stageRef.current.nodes.find((node) => node.id === "avatar");
        return {
          request_id: requestId,
          action,
          ok: true,
          agent_position: avatar ? { x: avatar.x, y: avatar.y } : undefined,
          reachable_objects: stageRef.current.nodes
            .filter((node) => node.id !== "avatar")
            .filter((node) => (
              !avatar || distanceBetween(avatar, node) <= REACHABLE_DISTANCE
            ))
            .map((node) => node.label || node.id),
        };
      }

      default:
        return {
          request_id: requestId,
          action,
          ok: false,
          error: `Unknown frontend action: ${action}`,
        };
    }
  }, [commitStage]);

  const contextValue = useMemo<DynamicStageContextValue>(() => ({
    stage,
    validationErrors,
    setStageFromServer,
    handleFrontendAction,
  }), [handleFrontendAction, setStageFromServer, stage, validationErrors]);

  return (
    <DynamicStageContext.Provider value={contextValue}>
      {children}
    </DynamicStageContext.Provider>
  );
}

export function useDynamicStage() {
  const context = useContext(DynamicStageContext);
  if (!context) {
    throw new Error("useDynamicStage must be used within a DynamicStageProvider");
  }
  return context;
}
