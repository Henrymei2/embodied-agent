import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Image, Text } from "@chakra-ui/react";
import {
  StageDirection,
  StageNode,
  StageSpriteAnimation,
  StageSpritesheet,
  useDynamicStage,
} from "@/context/dynamic-stage-context";

const toPercent = (value: number, total: number) => `${(value / total) * 100}%`;
const MOVE_DURATION_MS = 900;

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
      <Image
        {...commonProps}
        src={node.src}
        alt={node.alt || node.label || node.id}
        objectFit="cover"
        borderRadius={`${style.borderRadius ?? 8}px`}
        border={style.borderColor ? "2px solid" : undefined}
        borderColor={style.borderColor}
      />
    );
  }

  if (node.type === "avatar") {
    return (
      <Box
        {...commonProps}
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
      </Box>
    );
  }

  return (
    <Box
      {...commonProps}
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
