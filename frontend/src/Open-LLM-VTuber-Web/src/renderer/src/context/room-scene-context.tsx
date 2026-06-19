import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

export interface ScenePosition {
  x: number;
  y: number;
}

export interface SceneObject extends ScenePosition {
  label: "computer" | "calendar" | "bed";
}

export interface FrontendActionRequest {
  action?: string;
  args?: Record<string, unknown>;
}

export interface FrontendActionResult {
  ok: boolean;
  error?: string;
  position?: ScenePosition;
  objects?: SceneObject[];
  agent_position?: ScenePosition;
  reachable_objects?: string[];
}

interface RoomSceneContextValue {
  roomSize: ScenePosition;
  avatarPosition: ScenePosition;
  objects: SceneObject[];
  moveAvatarTo: (position: ScenePosition) => Promise<void>;
  handleFrontendAction: (
    request: FrontendActionRequest,
  ) => Promise<FrontendActionResult>;
}

const ROOM_SIZE: ScenePosition = { x: 960, y: 540 };
const INITIAL_AVATAR_POSITION: ScenePosition = { x: 480, y: 270 };
const MOVE_DURATION_MS = 900;

const SCENE_OBJECTS: SceneObject[] = [
  { label: "computer", x: 260, y: 255 },
  { label: "calendar", x: 480, y: 420 },
  { label: "bed", x: 700, y: 270 },
];

const RoomSceneContext = createContext<RoomSceneContextValue | null>(null);

const isFiniteNumber = (value: unknown): value is number => (
  typeof value === "number" && Number.isFinite(value)
);

const distanceBetween = (a: ScenePosition, b: ScenePosition) => {
  const deltaX = a.x - b.x;
  const deltaY = a.y - b.y;
  return Math.sqrt(deltaX * deltaX + deltaY * deltaY);
};

export function RoomSceneProvider({ children }: { children: React.ReactNode }) {
  const [avatarPosition, setAvatarPosition] = useState(INITIAL_AVATAR_POSITION);
  const avatarPositionRef = useRef(INITIAL_AVATAR_POSITION);
  const moveTimeoutRef = useRef<number | null>(null);
  const moveResolveRef = useRef<(() => void) | null>(null);

  const setAvatarPositionSynced = useCallback((position: ScenePosition) => {
    avatarPositionRef.current = position;
    setAvatarPosition(position);
  }, []);

  const moveAvatarTo = useCallback((position: ScenePosition) => new Promise<void>((resolve) => {
    if (moveTimeoutRef.current !== null) {
      window.clearTimeout(moveTimeoutRef.current);
      moveTimeoutRef.current = null;
    }
    if (moveResolveRef.current) {
      moveResolveRef.current();
      moveResolveRef.current = null;
    }

    const boundedPosition = {
      x: Math.max(0, Math.min(ROOM_SIZE.x, position.x)),
      y: Math.max(0, Math.min(ROOM_SIZE.y, position.y)),
    };

    setAvatarPositionSynced(boundedPosition);
    moveResolveRef.current = resolve;
    moveTimeoutRef.current = window.setTimeout(() => {
      moveTimeoutRef.current = null;
      moveResolveRef.current = null;
      resolve();
    }, MOVE_DURATION_MS);
  }), [setAvatarPositionSynced]);

  const handleFrontendAction = useCallback(async ({
    action,
    args = {},
  }: FrontendActionRequest): Promise<FrontendActionResult> => {
    switch (action) {
      case "get_object_position": {
        const name = args.name;
        if (typeof name !== "string") {
          return { ok: false, error: "get_object_position requires args.name" };
        }

        const object = SCENE_OBJECTS.find((item) => item.label === name);
        if (!object) {
          return { ok: false, error: `Unknown object: ${name}` };
        }

        return { ok: true, position: { x: object.x, y: object.y } };
      }

      case "move_to": {
        const target = args.target;
        let destination: ScenePosition | null = null;

        if (typeof target === "string") {
          const object = SCENE_OBJECTS.find((item) => item.label === target);
          if (!object) {
            return { ok: false, error: `Unknown target: ${target}` };
          }
          destination = { x: object.x, y: object.y };
        } else if (isFiniteNumber(args.x) && isFiniteNumber(args.y)) {
          destination = { x: args.x, y: args.y };
        }

        if (!destination) {
          return {
            ok: false,
            error: "move_to requires args.target or numeric args.x and args.y",
          };
        }

        await moveAvatarTo(destination);
        return { ok: true };
      }

      case "list_objects": {
        const distance = args.distance;
        if (distance !== undefined && !isFiniteNumber(distance)) {
          return { ok: false, error: "list_objects args.distance must be numeric" };
        }

        const objects = SCENE_OBJECTS.filter((object) => (
          distance === undefined ||
          distanceBetween(avatarPositionRef.current, object) <= distance
        ));
        return { ok: true, objects };
      }

      case "get_scene_status":
        return {
          ok: true,
          agent_position: avatarPositionRef.current,
          reachable_objects: SCENE_OBJECTS.map((object) => object.label),
        };

      default:
        return { ok: false, error: `Unknown frontend action: ${action}` };
    }
  }, [moveAvatarTo]);

  const contextValue = useMemo<RoomSceneContextValue>(() => ({
    roomSize: ROOM_SIZE,
    avatarPosition,
    objects: SCENE_OBJECTS,
    moveAvatarTo,
    handleFrontendAction,
  }), [avatarPosition, handleFrontendAction, moveAvatarTo]);

  return (
    <RoomSceneContext.Provider value={contextValue}>
      {children}
    </RoomSceneContext.Provider>
  );
}

export function useRoomScene() {
  const context = useContext(RoomSceneContext);
  if (!context) {
    throw new Error("useRoomScene must be used within a RoomSceneProvider");
  }
  return context;
}
