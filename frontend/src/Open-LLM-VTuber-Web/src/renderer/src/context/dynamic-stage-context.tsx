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
// Keep in sync with AVATAR_POSES in stage_state_manager.py.
export const AVATAR_POSES = ["standing", "sitting"] as const;
export type StagePose = (typeof AVATAR_POSES)[number];

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
  // How close the agent must stand to use this object, in world units.
  // Omitted means "derive it from the footprint"; see world_model.py, which
  // is the authority. Never above the room's reach_ceiling.
  reach?: number;
  zIndex?: number;
  text?: string;
  src?: string;
  alt?: string;
  texture?: StageTexture;
  stateTextures?: Partial<Record<"unread", StageTexture>>;
  spritesheet?: StageSpritesheet;
  uiWindow?: FurnitureWindowConfig;
  style?: StageNodeStyle;
  pose?: StagePose;
  interaction?: StageInteraction;
}

// The room as something other than a picture: what a unit means, how far the
// agent can reach, how fast it walks. Keep in sync with world_model.py, which
// owns these defaults; a renderer that is not this one reads this block
// instead of the canvas.
export interface StageWorld {
  units_per_meter?: number;
  reach_ceiling?: number;
  move_speed?: number;
}

// What a click on an object does. Deliberately a flag and nothing else:
// the prompt behind a click is composed on the backend from the stored
// label, so the stage cannot carry text into a conversation.
export interface StageInteraction {
  on_click?: boolean;
}

// Live data sources a furniture hover window may render. Most are backed by a
// backend route the window polls while it is open; `room_layout` is rendered
// from the stage document this context already holds. Keep in sync with
// UI_WINDOW_PROVIDERS in stage_state_manager.py.
export const UI_WINDOW_PROVIDERS = [
  "google_calendar_status",
  "todo_list",
  "timer_status",
  "google_gmail_unread",
  "bookshelf",
  "reading_status",
  "plan_status",
  "room_layout",
  "skill_status",
  "drawing_board_image",
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
  version: 8;
  canvas: {
    width: number;
    height: number;
    background: string;
    texture?: StageTexture;
    theme?: Record<string, string>;
  };
  world?: StageWorld;
  nodes: StageNode[];
  metadata: {
    conf_uid?: string;
    created_at?: string;
    updated_at?: string;
    updated_by?: string;
    theme_revision?: string;
  };
}

export interface StagePatch {
  canvas?: Partial<DynamicStageDocument["canvas"]>;
  world?: StageWorld;
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
  arrived?: boolean;
  duration_ms?: number;
  capabilities?: {
    actions: string[];
    node_types: StageNodeType[];
    version: number;
    texture: string;
    spritesheet: string;
    furniture_window: string;
    pose: string;
    world: string;
    move: string;
    patch_shape: string;
  };
}

interface DynamicStageContextValue {
  stage: DynamicStageDocument;
  validationErrors: string[];
  /** How long the avatar's current leg takes, so the sprite and the CSS agree. */
  motionDurationMs: number;
  setStageFromServer: (stage: unknown) => FrontendActionResult;
  handleFrontendAction: (
    request: FrontendActionRequest,
  ) => Promise<FrontendActionResult>;
}

const ROOM_WIDTH = 960;
const ROOM_HEIGHT = 540;
const STAGE_VERSION = 8;
// Keep in sync with world_model.py, which owns these.
const UNITS_PER_METER = 100;
const REACH_CEILING = 150;
const MOVE_SPEED = 400;
const PERSONAL_REACH = 60;
// A hop should feel immediate and a room crossing should not stall the demo,
// so a distance-derived walk is clamped at both ends.
const MIN_MOVE_DURATION_MS = 250;
const MAX_MOVE_DURATION_MS = 1800;
const DEFAULT_MOVE_DURATION_MS = 900;
// Keep in sync with STAGE_THEME_REVISION in stage_state_manager.py: the backend
// rebuilds any stored room whose revision differs from its own.
const STAGE_THEME_REVISION = "cozy-bedroom-v2";

const nowIso = () => new Date().toISOString();

export const createDefaultStage = (confUid?: string): DynamicStageDocument => ({
  version: STAGE_VERSION,
  canvas: {
    width: ROOM_WIDTH,
    height: ROOM_HEIGHT,
    background: "#e8c98f",
    texture: {
      src: "./stage-assets/bedroom-background.png",
      fit: "cover",
      alt: "cozy pixel-art bedroom",
    },
  },
  world: {
    units_per_meter: UNITS_PER_METER,
    reach_ceiling: REACH_CEILING,
    move_speed: MOVE_SPEED,
  },
  nodes: [
    {
      id: "computer",
      type: "panel",
      label: "computer",
      x: 120,
      y: 370,
      width: 230,
      height: 190,
      zIndex: 2,
      interaction: { on_click: true },
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
      x: 425,
      y: 110,
      width: 110,
      height: 130,
      zIndex: 2,
      interaction: { on_click: true },
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
      x: 480,
      y: 465,
      width: 210,
      height: 150,
      zIndex: 3,
      interaction: { on_click: true },
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
      id: "archive_shelf",
      type: "panel",
      label: "archive shelf",
      x: 360,
      y: 370,
      width: 125,
      height: 110,
      zIndex: 2,
      interaction: { on_click: true },
      text: "archive shelf",
      texture: {
        src: "./stage-assets/archive-shelf.png",
        fit: "contain",
        alt: "archive shelf",
      },
      style: {
        background: "rgba(146, 64, 14, 0.88)",
        borderColor: "rgba(254, 215, 170, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "mailbox",
      type: "panel",
      label: "mailbox",
      x: 900,
      y: 110,
      width: 96,
      height: 88,
      zIndex: 2,
      interaction: { on_click: true },
      text: "mailbox",
      texture: {
        src: "./stage-assets/mailbox-empty.png",
        fit: "contain",
        alt: "mailbox",
      },
      stateTextures: {
        unread: {
          src: "./stage-assets/mailbox-unread.png",
          fit: "contain",
          alt: "mailbox with unread mail",
        },
      },
      uiWindow: {
        title: "Unread Gmail",
        body: "Unread messages for the connected Google account.",
        provider: "google_gmail_unread",
      },
      style: {
        background: "rgba(71, 85, 105, 0.9)",
        borderColor: "rgba(203, 213, 225, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "todo_list",
      type: "panel",
      label: "todo_list",
      x: 265,
      y: 110,
      width: 105,
      height: 125,
      zIndex: 2,
      interaction: { on_click: true },
      text: "todo list",
      texture: {
        src: "./stage-assets/todo-list.png",
        fit: "contain",
        alt: "todo list",
      },
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
      x: 585,
      y: 110,
      width: 65,
      height: 90,
      zIndex: 2,
      interaction: { on_click: true },
      text: "hourglass",
      texture: {
        src: "./stage-assets/hourglass.png",
        fit: "contain",
        alt: "hourglass",
      },
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
      x: 105,
      y: 110,
      width: 185,
      height: 155,
      zIndex: 2,
      interaction: { on_click: true },
      text: "window",
      texture: {
        src: "./stage-assets/window.png",
        fit: "contain",
        alt: "bedroom window",
      },
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
      id: "bookshelf",
      type: "panel",
      label: "bookshelf",
      x: 600,
      y: 370,
      width: 120,
      height: 150,
      zIndex: 2,
      interaction: { on_click: true },
      text: "bookshelf",
      texture: {
        src: "./stage-assets/bookshelf.png",
        fit: "contain",
        alt: "bookshelf",
      },
      uiWindow: {
        title: "Bookshelf",
        body: "Books the agent has shelved to read.",
        provider: "bookshelf",
      },
      style: {
        background: "rgba(120, 53, 15, 0.9)",
        borderColor: "rgba(253, 230, 138, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "reading_chair",
      type: "panel",
      label: "reading chair",
      x: 240,
      y: 477,
      width: 120,
      height: 125,
      zIndex: 3,
      interaction: { on_click: true },
      text: "reading chair",
      texture: {
        src: "./stage-assets/reading-chair.png",
        fit: "contain",
        alt: "reading chair",
      },
      uiWindow: {
        title: "Reading chair",
        body: "The book being read, and the notes taken so far.",
        provider: "reading_status",
      },
      style: {
        background: "rgba(47, 125, 117, 0.9)",
        borderColor: "rgba(204, 251, 241, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "writing_desk",
      type: "panel",
      label: "writing desk",
      x: 840,
      y: 370,
      width: 150,
      height: 120,
      zIndex: 2,
      interaction: { on_click: true },
      text: "writing desk",
      texture: {
        src: "./stage-assets/writing-desk.png",
        fit: "contain",
        alt: "writing desk",
      },
      uiWindow: {
        title: "Plans",
        body: "Plans the agent has drafted, awaiting your decision.",
        provider: "plan_status",
      },
      style: {
        background: "rgba(180, 118, 62, 0.9)",
        borderColor: "rgba(254, 240, 204, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "sofa",
      type: "panel",
      label: "sofa",
      x: 720,
      y: 480,
      width: 190,
      height: 105,
      zIndex: 3,
      interaction: { on_click: true },
      text: "sofa",
      texture: {
        src: "./stage-assets/sofa.png",
        fit: "contain",
        alt: "sofa",
      },
      uiWindow: {
        title: "Sofa",
        body: "Where the agent sits when there is nothing to do. It still hears you, and walking anywhere stands it up.",
      },
      style: {
        background: "rgba(124, 74, 99, 0.9)",
        borderColor: "rgba(245, 214, 228, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "design_board",
      type: "panel",
      label: "design board",
      x: 745,
      y: 110,
      width: 120,
      height: 110,
      zIndex: 2,
      interaction: { on_click: true },
      text: "design board",
      texture: {
        src: "./stage-assets/design-board.png",
        fit: "contain",
        alt: "design board",
      },
      uiWindow: {
        title: "Room layout",
        body: "Every object in the room, and which are fixed.",
        provider: "room_layout",
      },
      style: {
        background: "rgba(146, 100, 46, 0.9)",
        borderColor: "rgba(254, 226, 176, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "tool_rack",
      type: "panel",
      label: "tool rack",
      // The one wall spot left that keeps its own 150-unit radius.
      x: 503,
      y: 245,
      width: 120,
      height: 100,
      zIndex: 2,
      interaction: { on_click: true },
      text: "tool rack",
      texture: {
        src: "./stage-assets/tool-rack.png",
        fit: "contain",
        alt: "tool rack",
      },
      uiWindow: {
        title: "Skills",
        body: "Skills the agent has written for itself, and drafts waiting on you.",
        provider: "skill_status",
      },
      style: {
        background: "rgba(75, 85, 99, 0.9)",
        borderColor: "rgba(226, 232, 240, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "drawing_board",
      type: "panel",
      label: "drawing_board",
      x: 480,
      y: 477,
      width: 115,
      height: 125,
      zIndex: 2,
      text: "drawing board",
      uiWindow: {
        title: "Drawing Board",
        body: "The latest image generated by the agent.",
        provider: "drawing_board_image",
      },
      style: {
        background: "rgba(99, 102, 241, 0.88)",
        borderColor: "rgba(199, 210, 254, 0.9)",
        color: "#ffffff",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: "700",
      },
    },
    {
      id: "avatar",
      type: "avatar",
      label: "avatar",
      x: 60,
      y: 515,
      width: 70,
      height: 92,
      zIndex: 4,
      interaction: { on_click: true },
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
          sit_down: { row: 8, frames: [0, 1, 2, 1] },
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
    theme_revision: STAGE_THEME_REVISION,
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

// The name an object actually answers to. A blank label is not a name, so the
// id stands in — otherwise clearing a label would let a second object share it
// unnoticed. Mirrors effective_label() in world_model.py.
const effectiveLabel = (node: Record<string, unknown>): string | undefined => {
  const label = node.label;
  if (typeof label === "string") {
    const normalized = label.trim().replace(/\s+/g, " ");
    if (normalized) return normalized;
  }
  return typeof node.id === "string" && node.id ? node.id : undefined;
};

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

const validateStateTextures = (stateTextures: unknown, nodeId: string, errors: string[]) => {
  if (stateTextures === undefined) return;
  if (!isRecord(stateTextures)) {
    errors.push(`Node ${nodeId} stateTextures must be an object.`);
    return;
  }
  Object.entries(stateTextures).forEach(([key, texture]) => {
    if (key !== "unread") {
      errors.push(`Node ${nodeId} stateTextures.${key} is not supported.`);
      return;
    }
    validateTexture(texture, `${nodeId} stateTextures.${key}`, errors);
  });
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

// Returns the reach ceiling in force, so node reach can be checked against it.
const validateWorld = (world: unknown, errors: string[]): number => {
  if (world === undefined) return REACH_CEILING;
  if (!isRecord(world)) {
    errors.push("Stage world must be an object.");
    return REACH_CEILING;
  }

  const limits: Record<string, [number, number]> = {
    units_per_meter: [1, 10000],
    reach_ceiling: [1, 4096],
    move_speed: [1, 10000],
  };
  Object.keys(world).forEach((key) => {
    if (!(key in limits)) errors.push(`Stage world.${key} is not allowed.`);
  });
  Object.entries(limits).forEach(([key, [low, high]]) => {
    if (world[key] === undefined) return;
    if (!isFiniteNumber(world[key]) || (world[key] as number) < low || (world[key] as number) > high) {
      errors.push(`Stage world.${key} must be ${low}-${high}.`);
    }
  });

  const ceiling = world.reach_ceiling;
  return isFiniteNumber(ceiling) && ceiling >= 1 && ceiling <= 4096 ? ceiling : REACH_CEILING;
};

// A node may shrink its own reach but never claim more than the ceiling:
// otherwise a placed object could be usable from across the room and hand the
// agent every other object's tools from where it stands.
const validateReach = (reach: unknown, nodeId: string, ceiling: number, errors: string[]) => {
  if (reach === undefined) return;
  if (!isFiniteNumber(reach) || reach <= 0) {
    errors.push(`Node ${nodeId} reach must be a positive number.`);
    return;
  }
  if (reach > ceiling) {
    errors.push(`Node ${nodeId} reach cannot exceed ${ceiling}.`);
  }
};

/** How close the agent must stand to an object that does not say. */
export const derivedReach = (node: StageNode, ceiling = REACH_CEILING): number => {
  if (isFiniteNumber(node.reach) && node.reach > 0) {
    return Math.min(node.reach, ceiling);
  }
  const halfSpan = Math.max(Math.abs(node.width), Math.abs(node.height)) / 2;
  return Math.min(ceiling, Math.round((PERSONAL_REACH + halfSpan) * 10) / 10);
};

const migrateStageDocument = (candidate: unknown): unknown => {
  if (!isRecord(candidate)) {
    return candidate;
  }
  if (candidate.version === STAGE_VERSION) {
    return candidate;
  }
  if (![1, 2, 3, 4, 5, 6, 7].includes(candidate.version as number)) {
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
      // A stored animation map describes the shipped image file, which gained a
      // seated row in v6. A custom sheet — any other src — keeps its own map.
      const storedSheet = isRecord(node.spritesheet) ? node.spritesheet : undefined;
      const usesShippedSheet = Boolean(
        storedSheet && defaultNode.spritesheet &&
        storedSheet.src === defaultNode.spritesheet.src,
      );
      return {
        ...defaultNode,
        ...node,
        texture: node.texture ?? defaultNode.texture,
        stateTextures: node.stateTextures ?? defaultNode.stateTextures,
        spritesheet: usesShippedSheet
          ? defaultNode.spritesheet
          : (node.spritesheet ?? defaultNode.spritesheet),
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
    // Merged rather than replaced, so a document written before the world
    // block existed gains every setting instead of only the ones it names.
    world: {
      ...defaults.world,
      ...(isRecord(candidate.world) ? candidate.world : {}),
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
    validateTexture(candidate.canvas.texture, "canvas", errors);
  }

  const reachCeiling = validateWorld(candidate.world, errors);

  if (!Array.isArray(candidate.nodes)) {
    errors.push("Stage nodes must be an array.");
  } else if (candidate.nodes.length > 80) {
    errors.push("Stage can contain at most 80 nodes.");
  } else {
    const ids = new Set<string>();
    // Tools are gated on an object's label, so two objects answering to one
    // name would let a decoy stand in for the real thing. Matches the
    // backend's rule in stage_state_manager.validate_stage.
    const labels = new Map<string, string>();
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
      const name = effectiveLabel(node);
      if (name !== undefined) {
        const key = name.toLowerCase();
        const owner = labels.get(key);
        if (owner !== undefined) {
          errors.push(
            `Node ${id || index} label duplicates ${owner}; object labels must be unique.`,
          );
        } else {
          labels.set(key, String(id || index));
        }
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
      validateStateTextures(node.stateTextures, String(id || index), errors);
      validateSpritesheet(node.spritesheet, String(id || index), errors);
      validateUiWindow(node.uiWindow, String(id || index), errors);
      validateNodeStyle(node.style, String(id || index), errors);
      validateReach(node.reach, String(id || index), reachCeiling, errors);
      if (node.pose !== undefined && node.pose !== null && !AVATAR_POSES.includes(node.pose as StagePose)) {
        errors.push(`Node ${id || index} pose is not supported.`);
      }
      if (node.interaction !== undefined) {
        if (!isRecord(node.interaction)) {
          errors.push(`Node ${id || index} interaction must be an object.`);
        } else {
          Object.entries(node.interaction).forEach(([key, value]) => {
            if (key !== "on_click") {
              errors.push(`Node ${id || index} interaction.${key} is not allowed.`);
            } else if (typeof value !== "boolean") {
              errors.push(`Node ${id || index} interaction.on_click must be true or false.`);
            }
          });
        }
      }
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
    // Field-level merge: a patch that carries one field (a pose, a position)
    // must not drop the texture or spritesheet the node already had.
    .map((node) => {
      const incoming = incomingNodes.get(node.id);
      return incoming ? { ...node, ...incoming } : node;
    });

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
    world: {
      ...current.world,
      ...(patch.world || {}),
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

/** How long one leg of a walk should take, from how far it is. */
const legDurationMs = (distance: number, stage: DynamicStageDocument) => {
  const speed = stage.world?.move_speed || MOVE_SPEED;
  return Math.round(
    Math.min(MAX_MOVE_DURATION_MS, Math.max(MIN_MOVE_DURATION_MS, (distance / speed) * 1000)),
  );
};

/** Read a waypoint list off an action's arguments, ignoring anything malformed. */
const readWaypoints = (value: unknown): StagePosition[] => {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => (
    isRecord(entry) && isFiniteNumber(entry.x) && isFiniteNumber(entry.y)
      ? [{ x: entry.x, y: entry.y }]
      : []
  ));
};

export function DynamicStageProvider({ children }: { children: React.ReactNode }) {
  const [stage, setStage] = useState<DynamicStageDocument>(() => createDefaultStage());
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [motionDurationMs, setMotionDurationMs] = useState(DEFAULT_MOVE_DURATION_MS);
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
            pose: "avatar nodes may include pose: standing | sitting. move_to clears it.",
            furniture_window: "nodes may include uiWindow: { title, body?, provider? }; providers are allowlisted.",
            world: "world: { units_per_meter, reach_ceiling, move_speed }. A node may set reach, never above reach_ceiling; otherwise it is derived from the footprint.",
            move: "move_to accepts args.waypoints: [{x, y}]; each leg is timed from world.move_speed and the result reports arrived and duration_ms.",
            patch_shape: "args.patch may include canvas, world, nodes, remove_node_ids, metadata. Node assets use safe URL/data-image/relative src strings.",
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

        // The caller may send a path; the destination is the last leg either
        // way, so an old-style request still works and a future navmesh can
        // route around the furniture without changing anything here.
        const requested = readWaypoints(args.waypoints);
        const path = requested.length > 0 ? requested : [destination];
        const finalStop = path[path.length - 1];

        let result: FrontendActionResult | null = null;
        let elapsed = 0;
        for (const waypoint of path) {
          const avatar = stageRef.current.nodes.find((node) => node.id === "avatar");
          const duration = legDurationMs(
            avatar ? distanceBetween(avatar, waypoint) : 0,
            stageRef.current,
          );
          const nextStage = applyStagePatch(stageRef.current, {
            nodes: stageRef.current.nodes.map((node) => (
              node.id === "avatar"
                // Walking stands the avatar up, so a seated sprite is never
                // animated mid-stride.
                ? { ...node, x: waypoint.x, y: waypoint.y, pose: undefined }
                : node
            )),
          });
          // The sprite and the CSS tween read this, so both last exactly as
          // long as the leg the agent is actually walking.
          setMotionDurationMs(duration);
          result = commitStage(nextStage, action, requestId);
          if (!result.ok) return result;
          elapsed += duration;
          await new Promise<void>((resolve) => {
            window.setTimeout(resolve, duration);
          });
        }

        const avatar = stageRef.current.nodes.find((node) => node.id === "avatar");
        return {
          ...(result as FrontendActionResult),
          arrived: Boolean(avatar && distanceBetween(avatar, finalStop) <= 1),
          duration_ms: elapsed,
          position: { x: finalStop.x, y: finalStop.y },
        };
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
        const ceiling = stageRef.current.world?.reach_ceiling ?? REACH_CEILING;
        return {
          request_id: requestId,
          action,
          ok: true,
          agent_position: avatar ? { x: avatar.x, y: avatar.y } : undefined,
          reachable_objects: stageRef.current.nodes
            .filter((node) => node.id !== "avatar")
            // Each object sets its own reach from its footprint, so a bed is
            // usable from further away than an hourglass.
            .filter((node) => (
              Boolean(avatar) && distanceBetween(avatar as StageNode, node) <= derivedReach(node, ceiling)
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
    motionDurationMs,
    setStageFromServer,
    handleFrontendAction,
  }), [handleFrontendAction, motionDurationMs, setStageFromServer, stage, validationErrors]);

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
