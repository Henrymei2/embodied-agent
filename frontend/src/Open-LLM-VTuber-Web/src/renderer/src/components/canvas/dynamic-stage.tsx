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
import { useChatHistory } from "@/context/chat-history-context";

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

interface UnreadMessage {
  message_id: string;
  thread_id: string;
  sender: string;
  subject: string;
  received_at: string;
  snippet: string;
}

interface UnreadPage {
  messages: UnreadMessage[];
  next_page_token?: string | null;
  has_unread: boolean;
}

const statusCache = new Map<string, { expires: number; value: GoogleStatus }>();

const GMAIL_MUTATION_TOOLS = new Set([
  "mark_email_read",
  "mark_email_unread",
  "archive_email",
  "send_email",
]);

function useMailboxUnreadState(node: StageNode, hovered: boolean) {
  const { baseUrl } = useWebSocket();
  const { messages } = useChatHistory();
  const [hasUnread, setHasUnread] = useState(false);
  const mutation = [...messages].reverse().find((message) => (
    message.type === "tool_call_status" &&
    message.status === "completed" &&
    Boolean(message.tool_name && GMAIL_MUTATION_TOOLS.has(message.tool_name))
  ));
  const mutationKey = mutation ? `${mutation.tool_id}:${mutation.timestamp}` : "";

  const refresh = useCallback(async () => {
    if (node.uiWindow?.provider !== "google_gmail_unread") return;
    try {
      const response = await fetch(`${baseUrl}/api/integrations/google/gmail/unread-status`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("unread status request failed");
      const value = await response.json() as { has_unread: boolean };
      setHasUnread(Boolean(value.has_unread));
    } catch (_error) {
      // Preserve the last successful state during transient/auth failures.
    }
  }, [baseUrl, node.uiWindow?.provider]);

  useEffect(() => {
    if (node.uiWindow?.provider !== "google_gmail_unread") return undefined;
    void refresh();
    const onFocus = () => void refresh();
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 60_000);
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      window.clearInterval(interval);
    };
  }, [node.uiWindow?.provider, refresh]);

  useEffect(() => {
    if (hovered || mutationKey) void refresh();
  }, [hovered, mutationKey, refresh]);

  return hasUnread;
}

interface TodoWindowItem {
  text: string;
  priority: "high" | "normal" | "low";
  due?: string | null;
}

interface TodoWindowData {
  available: boolean;
  message?: string;
  open_count: number;
  items: TodoWindowItem[];
  remaining: number;
}

interface TimerWindowItem {
  label: string;
  due_at?: string | null;
  remaining_seconds: number;
}

interface TimerWindowData {
  available: boolean;
  count: number;
  timers: TimerWindowItem[];
  remaining: number;
}

// Read-only routes backing the furniture hover windows.
const FURNITURE_FEED_ROUTES: Record<string, string> = {
  todo_list: "/api/furniture/todo",
  timer_status: "/api/furniture/timers",
};

const TODO_PRIORITY_COLORS: Record<string, string> = {
  high: "red.300",
  normal: "blue.200",
  low: "gray.400",
};

/** Poll a furniture feed while its window is open. */
function useFurnitureFeed<T>(
  baseUrl: string,
  provider: string | undefined,
  open: boolean,
  refreshMs: number,
) {
  const [data, setData] = useState<T | null>(null);
  const [failed, setFailed] = useState(false);
  const route = provider ? FURNITURE_FEED_ROUTES[provider] : undefined;

  useEffect(() => {
    if (!open || !route) return undefined;
    let cancelled = false;

    const load = async () => {
      try {
        const response = await fetch(`${baseUrl}${route}`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("furniture feed request failed");
        const value = await response.json() as T;
        if (!cancelled) {
          setData(value);
          setFailed(false);
        }
      } catch (_error) {
        if (!cancelled) setFailed(true);
      }
    };

    void load();
    const interval = window.setInterval(() => void load(), refreshMs);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [baseUrl, open, refreshMs, route]);

  return { data, failed };
}

/** Re-render once a second so a countdown stays live between fetches. */
function useSecondTick(active: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const interval = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(interval);
  }, [active]);
}

const timerRemainingSeconds = (timer: TimerWindowItem): number => {
  // due_at is the authoritative moment; remaining_seconds is only a fallback so
  // the countdown keeps moving between polls.
  if (timer.due_at) {
    const due = new Date(timer.due_at).getTime();
    if (Number.isFinite(due)) {
      return Math.max(0, Math.round((due - Date.now()) / 1000));
    }
  }
  return Math.max(0, timer.remaining_seconds);
};

const formatRemaining = (seconds: number): string => {
  if (seconds <= 0) return "due now";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m ${seconds % 60}s`;
};

const formatDue = (due?: string | null): string | null => {
  if (!due) return null;
  const parsed = new Date(due);
  if (!Number.isFinite(parsed.getTime())) return null;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

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
  const [unreadMessages, setUnreadMessages] = useState<UnreadMessage[]>([]);
  const [unreadNextToken, setUnreadNextToken] = useState<string | null>(null);
  const [unreadLoading, setUnreadLoading] = useState(false);
  const [unreadError, setUnreadError] = useState<string | null>(null);
  const [unreadNeedsAuth, setUnreadNeedsAuth] = useState(false);
  const unreadRequest = useRef(0);
  const config = node.uiWindow;
  const provider = config?.provider;
  const todoFeed = useFurnitureFeed<TodoWindowData>(baseUrl, provider === "todo_list" ? provider : undefined, open, 15_000);
  const timerFeed = useFurnitureFeed<TimerWindowData>(baseUrl, provider === "timer_status" ? provider : undefined, open, 20_000);
  useSecondTick(open && provider === "timer_status");

  const loadUnread = useCallback(async (pageToken?: string) => {
    if (provider !== "google_gmail_unread") return;
    const nextPage = Boolean(pageToken);
    const requestId = ++unreadRequest.current;
    setUnreadLoading(true);
    if (!nextPage) {
      setUnreadMessages([]);
      setUnreadNextToken(null);
    }
    setUnreadError(null);
    setUnreadNeedsAuth(false);
    try {
      const params = new URLSearchParams({ page_size: "20" });
      if (pageToken) params.set("page_token", pageToken);
      const response = await fetch(
        `${baseUrl}/api/integrations/google/gmail/unread?${params.toString()}`,
        { headers: { Accept: "application/json" } },
      );
      const value = await response.json() as UnreadPage & { error?: string };
      if (!response.ok) {
        if (response.status === 401) setUnreadNeedsAuth(true);
        throw new Error(value.error || "Unread Gmail messages are unavailable.");
      }
      if (requestId !== unreadRequest.current) return;
      setUnreadMessages((current) => nextPage ? [...current, ...value.messages] : value.messages);
      setUnreadNextToken(value.next_page_token || null);
    } catch (error) {
      if (requestId === unreadRequest.current) {
        setUnreadError(error instanceof Error ? error.message : "Unread Gmail messages are unavailable.");
      }
    } finally {
      if (requestId === unreadRequest.current) setUnreadLoading(false);
    }
  }, [baseUrl, provider]);

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
    if (open && provider === "google_gmail_unread") void loadUnread();
    if (!open) unreadRequest.current += 1;
  }, [loadUnread, open, provider]);

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
      {provider === "google_gmail_unread" && (
        <Box mt="12px" aria-live="polite">
          {unreadLoading && unreadMessages.length === 0 ? (
            <Spinner size="sm" aria-label="Loading unread Gmail messages" />
          ) : unreadError ? (
            <>
              <Text fontSize="sm" color="orange.200">{unreadError}</Text>
              {unreadNeedsAuth && (
                <Button mt="12px" size="sm" onClick={authorize}>Authenticate with Google</Button>
              )}
            </>
          ) : unreadMessages.length === 0 ? (
            <Text fontSize="sm" color="gray.400">No unread messages.</Text>
          ) : (
            <>
              {unreadMessages.map((message) => (
                <Box key={message.message_id} py="8px" borderBottom="1px solid" borderColor="whiteAlpha.200">
                  <Text fontSize="xs" color="blue.200" fontWeight="700" overflowWrap="anywhere">
                    {message.sender || "Unknown sender"}
                  </Text>
                  <Text fontSize="sm" color="gray.100" fontWeight="600" overflowWrap="anywhere">
                    {message.subject || "(no subject)"}
                  </Text>
                  {message.received_at && <Text fontSize="xs" color="gray.500">{message.received_at}</Text>}
                  {message.snippet && (
                    <Text fontSize="xs" color="gray.400" mt="3px" overflowWrap="anywhere">
                      {message.snippet}
                    </Text>
                  )}
                </Box>
              ))}
              {unreadNextToken && (
                <Button mt="10px" size="xs" onClick={() => void loadUnread(unreadNextToken)} disabled={unreadLoading}>
                  {unreadLoading ? "Loading…" : "Load more"}
                </Button>
              )}
            </>
          )}
        </Box>
      )}
      {provider === "todo_list" && (
        <Box mt="12px" aria-live="polite">
          {todoFeed.failed || todoFeed.data?.available === false ? (
            <Text fontSize="sm" color="orange.200">
              {todoFeed.data?.message || "The todo list is unavailable."}
            </Text>
          ) : !todoFeed.data ? (
            <Spinner size="sm" aria-label="Loading todo list" />
          ) : todoFeed.data.items.length === 0 ? (
            <Text fontSize="sm" color="gray.400">Nothing on the list.</Text>
          ) : (
            <>
              {todoFeed.data.items.map((item, index) => {
                const due = formatDue(item.due);
                return (
                  <Box key={`${item.text}-${index}`} display="flex" gap="8px" alignItems="baseline" mb="6px">
                    <Box
                      width="7px"
                      height="7px"
                      mt="5px"
                      flexShrink={0}
                      borderRadius="50%"
                      bg={TODO_PRIORITY_COLORS[item.priority] || TODO_PRIORITY_COLORS.normal}
                    />
                    <Text fontSize="sm" color="gray.100" overflowWrap="anywhere">
                      {item.text}
                      {due && <Text as="span" color="gray.400"> · {due}</Text>}
                    </Text>
                  </Box>
                );
              })}
              {todoFeed.data.remaining > 0 && (
                <Text fontSize="xs" color="gray.400" mt="8px">
                  +{todoFeed.data.remaining} more open
                </Text>
              )}
            </>
          )}
        </Box>
      )}
      {provider === "timer_status" && (
        <Box mt="12px" aria-live="polite">
          {timerFeed.failed ? (
            <Text fontSize="sm" color="orange.200">Timers are unavailable.</Text>
          ) : !timerFeed.data ? (
            <Spinner size="sm" aria-label="Loading timers" />
          ) : timerFeed.data.timers.length === 0 ? (
            <Text fontSize="sm" color="gray.400">No timers running.</Text>
          ) : (
            <>
              {timerFeed.data.timers.map((timer, index) => (
                <Box key={`${timer.due_at}-${index}`} display="flex" justifyContent="space-between" gap="12px" mb="6px">
                  <Text fontSize="sm" color="gray.100" overflowWrap="anywhere">
                    {timer.label || "timer"}
                  </Text>
                  <Text fontSize="sm" color="purple.200" flexShrink={0} fontVariantNumeric="tabular-nums">
                    {formatRemaining(timerRemainingSeconds(timer))}
                  </Text>
                </Box>
              ))}
              {timerFeed.data.remaining > 0 && (
                <Text fontSize="xs" color="gray.400" mt="8px">
                  +{timerFeed.data.remaining} more
                </Text>
              )}
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

function StageBoxContent({
  node,
  hasError,
  onTextureError,
}: {
  node: StageNode;
  hasError: boolean;
  onTextureError: () => void;
}) {
  const style = node.style || {};
  const showTexture = Boolean(node.texture?.src && !hasError);

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
          padding="0"
          imageRendering="pixelated"
          onError={onTextureError}
          zIndex={1}
        />
      )}
      {node.type === "panel" && !showTexture && (
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
  const hasUnread = useMailboxUnreadState(node, furniture.open);
  const displayNode = hasUnread && node.stateTextures?.unread
    ? { ...node, texture: node.stateTextures.unread }
    : node;
  const [textureError, setTextureError] = useState(false);
  const hasTexture = Boolean(displayNode.texture?.src && !textureError);
  useEffect(() => {
    setTextureError(false);
  }, [displayNode.texture?.src]);
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
          imageRendering="pixelated"
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
      border={node.type === "text" || hasTexture ? undefined : "2px solid"}
      borderColor={hasTexture ? undefined : (style.borderColor || "rgba(255, 255, 255, 0.7)")}
      borderRadius={hasTexture ? undefined : `${style.borderRadius ?? (node.type === "shape" ? 999 : 8)}px`}
      bg={node.type === "text" || hasTexture ? "transparent" : (style.background || "rgba(15, 23, 42, 0.72)")}
      boxShadow={node.type === "text" || hasTexture ? undefined : "0 16px 34px rgba(15, 23, 42, 0.28)"}
      display="flex"
      alignItems="center"
      justifyContent="center"
      overflow="visible"
      padding={node.type === "text" || hasTexture ? "0" : "8px"}
    >
      <StageBoxContent
        node={displayNode}
        hasError={textureError}
        onTextureError={() => setTextureError(true)}
      />
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
        {stage.canvas.texture?.src ? (
          <Image
            width="100%"
            height="100%"
            src={stage.canvas.texture.src}
            alt={stage.canvas.texture.alt || "cozy pixel-art bedroom"}
            objectFit={stage.canvas.texture.fit || "cover"}
            imageRendering="pixelated"
          />
        ) : (
          <>
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
          </>
        )}
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
