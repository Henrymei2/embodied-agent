import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Box, Button, Image, Spinner, Text } from "@chakra-ui/react";
import {
  StageDirection,
  StageNode,
  StageSpriteAnimation,
  StageSpritesheet,
  useDynamicStage,
} from "@/context/dynamic-stage-context";
import { useWebSocket } from "@/context/websocket-context";

const toPercent = (value: number, total: number) => `${(value / total) * 100}%`;
const MOVE_DURATION_MS = 900;
const HOVER_DELAY_MS = 250;
const CLOSE_DELAY_MS = 140;

interface GoogleStatus {
  configured: boolean;
  connected: boolean;
  status: "connected" | "authorization_required" | "unconfigured" | "error";
  message: string;
}

const statusCache = new Map<string, { expires: number; value: GoogleStatus }>();

function FurnitureWindow({
  node,
  anchor,
  open,
  onEnter,
  onLeave,
}: {
  node: StageNode;
  anchor: HTMLElement | null;
  open: boolean;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const { baseUrl } = useWebSocket();
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const config = node.uiWindow;
  const provider = config?.provider;

  const loadStatus = useCallback(async (force = false) => {
    if (provider !== "google_calendar_status") return;
    const cacheKey = `${baseUrl}:${provider}`;
    const cached = statusCache.get(cacheKey);
    if (!force && cached && cached.expires > Date.now()) {
      setStatus(cached.value);
      return;
    }
    setLoading(true);
    try {
      const response = await fetch(`${baseUrl}/api/integrations/google/status`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("status request failed");
      const value = await response.json() as GoogleStatus;
      statusCache.set(cacheKey, { expires: Date.now() + 15_000, value });
      setStatus(value);
    } catch (_error) {
      setStatus({
        configured: true,
        connected: false,
        status: "error",
        message: "Connection status is unavailable.",
      });
    } finally {
      setLoading(false);
    }
  }, [baseUrl, provider]);

  useEffect(() => {
    if (open) void loadStatus();
  }, [loadStatus, open]);

  useEffect(() => {
    if (!open || provider !== "google_calendar_status") return undefined;
    const refresh = () => void loadStatus(true);
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [loadStatus, open, provider]);

  if (!open || !anchor || !config) return null;
  const rect = anchor.getBoundingClientRect();
  const width = 288;
  const left = Math.max(12, Math.min(window.innerWidth - width - 12, rect.left + rect.width / 2 - width / 2));
  const showBelow = rect.top < 190;
  const position = showBelow
    ? { top: rect.bottom + 10 }
    : { bottom: window.innerHeight - rect.top + 10 };

  const authorize = () => {
    const url = new URL(
      "/api/integrations/google/authorize",
      baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`,
    ).toString();
    if (window.api?.openGoogleAuthorization) {
      void window.api.openGoogleAuthorization(url);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  };

  return createPortal(
    <Box
      role="dialog"
      aria-label={config.title}
      position="fixed"
      left={`${left}px`}
      {...position}
      width={`${width}px`}
      maxHeight="min(320px, calc(100vh - 24px))"
      overflowY="auto"
      bg="rgba(15, 23, 42, 0.97)"
      color="white"
      border="1px solid rgba(148, 163, 184, 0.5)"
      borderRadius="12px"
      boxShadow="0 20px 50px rgba(0, 0, 0, 0.38)"
      p="16px"
      zIndex={10000}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onClick={(event) => event.stopPropagation()}
    >
      <Text fontWeight="700" fontSize="md">{config.title}</Text>
      {config.body && <Text mt="6px" fontSize="sm" color="gray.300">{config.body}</Text>}
      {provider === "google_calendar_status" && (
        <Box mt="12px" aria-live="polite">
          {!status ? (
            <Spinner size="sm" aria-label="Loading Google Calendar status" />
          ) : (
            <>
              <Text fontSize="sm" color={status?.connected ? "green.300" : "orange.200"}>
                {status?.message || "Connection status is unavailable."}
              </Text>
              {loading && <Spinner mt="8px" size="xs" aria-label="Refreshing Google Calendar status" />}
              <Button mt="12px" size="sm" onClick={authorize}>
                {status.connected ? "Reauthenticate with Google" : "Authenticate with Google"}
              </Button>
            </>
          )}
        </Box>
      )}
    </Box>,
    document.body,
  );
}

function useFurnitureWindow(node: StageNode) {
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const openTimer = useRef<number | null>(null);
  const closeTimer = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const setElectronInteractive = useCallback((interactive: boolean) => {
    window.api?.updateComponentHover?.(`furniture-${node.id}`, interactive);
  }, [node.id]);

  const clearTimers = useCallback(() => {
    if (openTimer.current !== null) window.clearTimeout(openTimer.current);
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    openTimer.current = null;
    closeTimer.current = null;
  }, []);
  const enter = useCallback((immediate = false) => {
    if (!node.uiWindow) return;
    setElectronInteractive(true);
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    if (immediate) setOpen(true);
    else openTimer.current = window.setTimeout(() => setOpen(true), HOVER_DELAY_MS);
  }, [node.uiWindow, setElectronInteractive]);
  const leave = useCallback(() => {
    if (openTimer.current !== null) window.clearTimeout(openTimer.current);
    closeTimer.current = window.setTimeout(() => {
      setOpen(false);
      setElectronInteractive(false);
    }, CLOSE_DELAY_MS);
  }, [setElectronInteractive]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        setElectronInteractive(false);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      clearTimers();
      setElectronInteractive(false);
    };
  }, [clearTimers, setElectronInteractive]);

  return {
    anchorRef,
    open,
    enter,
    leave,
    toggle: () => node.uiWindow && setOpen((current) => !current),
  };
}

const getAnimation = (
  spritesheet: StageSpritesheet,
  preferredName: string,
  state: "idle" | "walk",
): { name: string; animation: StageSpriteAnimation } | null => {
  const fallbackNames = [
    preferredName,
    spritesheet.defaultAnimations?.[state],
    "idle_down",
    Object.keys(spritesheet.animations)[0],
  ].filter((name): name is string => Boolean(name));

  const animationName = fallbackNames.find((name) => spritesheet.animations[name]);
  if (!animationName) {
    return null;
  }

  return {
    name: animationName,
    animation: spritesheet.animations[animationName],
  };
};

function SpritesheetView({
  spritesheet,
  animationName,
  alt,
}: {
  spritesheet: StageSpritesheet;
  animationName: string;
  alt: string;
}) {
  const [frameIndex, setFrameIndex] = useState(0);
  const selected = useMemo(() => getAnimation(
    spritesheet,
    animationName,
    animationName.startsWith("walk_") ? "walk" : "idle",
  ), [animationName, spritesheet]);
  const animation = selected?.animation;
  const frames = animation?.frames || [0];
  const frame = frames[frameIndex % frames.length] || 0;
  const maxFrame = Math.max(
    frame,
    ...Object.values(spritesheet.animations).flatMap((item) => item.frames),
  );
  const maxRow = Math.max(
    animation?.row || 0,
    ...Object.values(spritesheet.animations).map((item) => item.row),
  );
  const columns = Math.max(1, maxFrame + 1);
  const rows = Math.max(1, maxRow + 1);

  useEffect(() => {
    setFrameIndex(0);
  }, [selected?.name]);

  useEffect(() => {
    const fps = spritesheet.fps || 6;
    const interval = window.setInterval(() => {
      setFrameIndex((current) => current + 1);
    }, 1000 / fps);

    return () => window.clearInterval(interval);
  }, [spritesheet.fps]);

  return (
    <Box
      role="img"
      aria-label={alt}
      width="100%"
      height="100%"
      backgroundImage={`url("${spritesheet.src}")`}
      backgroundRepeat="no-repeat"
      backgroundSize={`${columns * 100}% ${rows * 100}%`}
      backgroundPosition={`${columns > 1 ? (frame / (columns - 1)) * 100 : 0}% ${rows > 1 && animation ? (animation.row / (rows - 1)) * 100 : 0}%`}
      imageRendering="pixelated"
    />
  );
}

function useAvatarAnimation(node: StageNode) {
  const previousPositionRef = useRef({ x: node.x, y: node.y });
  const timeoutRef = useRef<number | null>(null);
  const [direction, setDirection] = useState<StageDirection>("down");
  const [isMoving, setIsMoving] = useState(false);

  useEffect(() => {
    const previous = previousPositionRef.current;
    const deltaX = node.x - previous.x;
    const deltaY = node.y - previous.y;
    previousPositionRef.current = { x: node.x, y: node.y };

    if (deltaX === 0 && deltaY === 0) {
      return undefined;
    }

    if (Math.abs(deltaX) >= Math.abs(deltaY)) {
      setDirection(deltaX >= 0 ? "right" : "left");
    } else {
      setDirection(deltaY >= 0 ? "down" : "up");
    }

    setIsMoving(true);
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = window.setTimeout(() => {
      setIsMoving(false);
      timeoutRef.current = null;
    }, MOVE_DURATION_MS);

    return undefined;
  }, [node.x, node.y]);

  useEffect(() => () => {
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
    }
  }, []);

  return `${isMoving ? "walk" : "idle"}_${direction}`;
}

function FallbackAvatar({ node }: { node: StageNode }) {
  const style = node.style || {};

  return (
    <>
      <Box
        width="46%"
        aspectRatio="1"
        borderRadius="50%"
        bg="#f8c9a4"
        border="2px solid rgba(71, 85, 105, 0.72)"
        boxShadow="0 8px 20px rgba(15, 23, 42, 0.25)"
      />
      <Box
        width="72%"
        height="52%"
        mt="-4px"
        borderRadius="18px 18px 8px 8px"
        bg={style.background || "#2563eb"}
        border="2px solid"
        borderColor={style.borderColor || "rgba(191, 219, 254, 0.9)"}
        boxShadow="0 14px 24px rgba(15, 23, 42, 0.28)"
      />
    </>
  );
}

function AvatarNodeContent({ node }: { node: StageNode }) {
  const style = node.style || {};
  const animationName = useAvatarAnimation(node);

  return (
    <>
      {node.spritesheet ? (
        <Box
          width="100%"
          height="100%"
          filter="drop-shadow(0 14px 18px rgba(15, 23, 42, 0.26))"
        >
          <SpritesheetView
            spritesheet={node.spritesheet}
            animationName={animationName}
            alt={node.alt || node.label || node.id}
          />
        </Box>
      ) : (
        <FallbackAvatar node={node} />
      )}
      {node.text && (
        <Text
          as="span"
          mt="4px"
          color={style.color || "#172554"}
          fontSize={`${style.fontSize ?? 12}px`}
          fontWeight={style.fontWeight || "700"}
          lineHeight="1"
          letterSpacing="0"
          textShadow="0 1px 0 rgba(255, 255, 255, 0.72)"
        >
          {node.text}
        </Text>
      )}
    </>
  );
}

function StageBoxContent({ node }: { node: StageNode }) {
  const style = node.style || {};
  const [hasError, setHasError] = useState(false);
  const showTexture = Boolean(node.texture?.src && !hasError);

  useEffect(() => {
    setHasError(false);
  }, [node.texture?.src]);

  return (
    <>
      {showTexture && node.texture && (
        <Image
          position="absolute"
          inset="0"
          width="100%"
          height="100%"
          src={node.texture.src}
          alt={node.texture.alt || node.alt || node.label || node.id}
          objectFit={node.texture.fit || "contain"}
          padding={node.texture.fit === "cover" ? "0" : "8px"}
          onError={() => setHasError(true)}
          zIndex={1}
        />
      )}
      {node.type === "panel" && (
        <Box
          position="absolute"
          inset="8px"
          border="1px solid"
          borderColor="whiteAlpha.500"
          borderRadius={`${Math.max(0, (style.borderRadius ?? 8) - 3)}px`}
          opacity={0.72}
          zIndex={showTexture ? 0 : 1}
        />
      )}
      {(node.text || node.label) && (
        <Text
          as="span"
          color={style.color || "#ffffff"}
          fontSize={`${style.fontSize ?? 14}px`}
          fontWeight={style.fontWeight || "700"}
          lineHeight="1.15"
          textTransform={node.type === "panel" ? "uppercase" : undefined}
          textAlign="center"
          letterSpacing="0"
          zIndex={2}
          overflowWrap="anywhere"
          opacity={showTexture ? 0 : 1}
        >
          {node.text || node.label}
        </Text>
      )}
    </>
  );
}

function StageNodeView({ node, canvasWidth, canvasHeight }: {
  node: StageNode;
  canvasWidth: number;
  canvasHeight: number;
}) {
  const style = node.style || {};
  const furniture = useFurnitureWindow(node);
  const interactionProps = node.uiWindow ? {
    ref: furniture.anchorRef,
    tabIndex: 0,
    onMouseEnter: () => furniture.enter(false),
    onMouseLeave: furniture.leave,
    onFocus: () => furniture.enter(true),
    onBlur: furniture.leave,
    onClick: furniture.toggle,
    cursor: "pointer",
  } : {};
  const popup = (
    <FurnitureWindow
      node={node}
      anchor={furniture.anchorRef.current}
      open={furniture.open}
      onEnter={() => furniture.enter(true)}
      onLeave={furniture.leave}
    />
  );
  const commonProps = {
    position: "absolute" as const,
    left: toPercent(node.x, canvasWidth),
    top: toPercent(node.y, canvasHeight),
    width: toPercent(node.width, canvasWidth),
    height: toPercent(node.height, canvasHeight),
    transform: node.type === "avatar" ? "translate(-50%, -78%)" : "translate(-50%, -50%)",
    zIndex: node.zIndex || 1,
    opacity: style.opacity ?? 1,
  };

  if (node.type === "image") {
    return (
      <Box
        {...commonProps}
        {...interactionProps}
        aria-label={node.label || node.id}
        borderRadius={`${style.borderRadius ?? 8}px`}
        border={style.borderColor ? "2px solid" : undefined}
        borderColor={style.borderColor}
      >
        <Image
          width="100%"
          height="100%"
          src={node.src}
          alt={node.alt || node.label || node.id}
          objectFit="cover"
        />
        {popup}
      </Box>
    );
  }

  if (node.type === "avatar") {
    return (
      <Box
        {...commonProps}
        {...interactionProps}
        aria-label={node.label || node.id}
        minWidth="46px"
        minHeight="68px"
        transition="left 900ms ease-in-out, top 900ms ease-in-out"
        display="flex"
        flexDirection="column"
        alignItems="center"
        justifyContent="flex-end"
      >
        <AvatarNodeContent node={node} />
        {popup}
      </Box>
    );
  }

  return (
    <Box
      {...commonProps}
      {...interactionProps}
      aria-label={node.label || node.id}
      minWidth={node.type === "text" ? undefined : "32px"}
      minHeight={node.type === "text" ? undefined : "24px"}
      border={node.type === "text" ? undefined : "2px solid"}
      borderColor={style.borderColor || "rgba(255, 255, 255, 0.7)"}
      borderRadius={`${style.borderRadius ?? (node.type === "shape" ? 999 : 8)}px`}
      bg={node.type === "text" ? "transparent" : (style.background || "rgba(15, 23, 42, 0.72)")}
      boxShadow={node.type === "text" ? undefined : "0 16px 34px rgba(15, 23, 42, 0.28)"}
      display="flex"
      alignItems="center"
      justifyContent="center"
      overflow="hidden"
      padding={node.type === "text" ? "0" : "8px"}
    >
      <StageBoxContent node={node} />
      {popup}
    </Box>
  );
}

export default function DynamicStage(): JSX.Element {
  const { stage } = useDynamicStage();

  return (
    <Box
      width="100%"
      height="100%"
      position="relative"
      overflow="hidden"
      bg="#e5d8c7"
    >
      <Box
        role="img"
        aria-label="dynamic stage"
        position="absolute"
        inset="0"
        bg={stage.canvas.background}
      >
        <Box
          position="absolute"
          left="0"
          right="0"
          top="48%"
          height="2px"
          bg={stage.canvas.theme?.floorLine || "rgba(88, 67, 48, 0.32)"}
        />
        <Box
          position="absolute"
          left="8%"
          right="8%"
          bottom="0"
          height="38%"
          bg={`repeating-linear-gradient(90deg, ${stage.canvas.theme?.floorStripe || "rgba(104, 75, 46, 0.16)"} 0 2px, transparent 2px 86px)`}
        />
      </Box>

      {stage.nodes
        .slice()
        .sort((a, b) => (a.zIndex || 0) - (b.zIndex || 0))
        .map((node) => (
          <StageNodeView
            key={node.id}
            node={node}
            canvasWidth={stage.canvas.width}
            canvasHeight={stage.canvas.height}
          />
        ))}
    </Box>
  );
}
