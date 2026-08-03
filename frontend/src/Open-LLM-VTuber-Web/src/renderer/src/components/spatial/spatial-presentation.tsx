import { Box, Button, HStack, Text } from "@chakra-ui/react";
import { FiMaximize2, FiX } from "react-icons/fi";
import { useTranslation } from "react-i18next";
import DynamicStage from "@/components/canvas/dynamic-stage";
import Subtitle from "@/components/canvas/subtitle";
import WebSocketStatus from "@/components/canvas/ws-status";
import type { SpatialCapability } from "@/hooks/canvas/use-spatial-presentation";
import {
  accent,
  border,
  elevation,
  focusRing,
  radius,
  surface,
  text,
  transition,
  typography,
} from "@/styles/tokens";

interface SpatialModeControlProps {
  capability: SpatialCapability;
  onEnter: () => void;
}

export function SpatialModeControl({
  capability,
  onEnter,
}: SpatialModeControlProps): JSX.Element {
  const { t } = useTranslation();

  return (
    <Button
      {...typography.labelSmall}
      height="34px"
      px={{ base: "10px", md: "12px" }}
      gap="1.5"
      bg={surface.glass1}
      color={text.secondary}
      border={`1px solid ${border.subtle}`}
      borderRadius={radius.md}
      backdropFilter="blur(14px)"
      transition={transition.control}
      _hover={{ bg: surface.glass3, color: text.primary }}
      _active={{ transform: "scale(0.97)" }}
      _focusVisible={focusRing}
      onClick={() => void onEnter()}
      aria-label={t("spatial.enter")}
      title={t(capability === "webxr-ready" ? "spatial.webxrReady" : "spatial.previewReady")}
    >
      <FiMaximize2 />
      <Text display={{ base: "none", lg: "block" }}>{t("spatial.control")}</Text>
    </Button>
  );
}

interface SpatialPresentationProps {
  capability: SpatialCapability;
  onExit: () => void;
}

export default function SpatialPresentation({
  capability,
  onExit,
}: SpatialPresentationProps): JSX.Element {
  const { t } = useTranslation();
  const capabilityLabel = capability === "checking"
    ? t("spatial.checking")
    : capability === "webxr-ready"
      ? t("spatial.webxrReady")
      : t("spatial.previewReady");

  return (
    <Box
      className="spatial-presentation"
      data-testid="spatial-presentation"
      position="fixed"
      inset="0"
      display="flex"
      alignItems="center"
      justifyContent="center"
      overflow="hidden"
      bg="#05070b"
      zIndex={100}
    >
      <Box
        className="spatial-stage"
        position="relative"
        width="min(100vw, calc(100dvh * 16 / 9))"
        maxWidth="100vw"
        maxHeight="100dvh"
        aspectRatio="16 / 9"
        overflow="hidden"
        bg="#0a0c12"
        boxShadow={`${elevation[5]}, 0 0 120px rgba(90, 100, 210, 0.16)`}
      >
        <DynamicStage />
        <Box
          aria-hidden="true"
          position="absolute"
          inset="0"
          pointerEvents="none"
          bg="linear-gradient(180deg, rgba(4,6,12,0.18), transparent 22%, transparent 68%, rgba(4,6,12,0.34))"
          boxShadow="inset 0 0 0 1px rgba(255,255,255,0.08), inset 0 0 72px rgba(0,0,0,0.18)"
          zIndex={7}
        />

        <HStack
          position="absolute"
          top="max(18px, env(safe-area-inset-top))"
          left="max(18px, env(safe-area-inset-left))"
          gap="2"
          zIndex={20}
        >
          <HStack
            role="status"
            aria-label={capabilityLabel}
            gap="2"
            height="34px"
            px="12px"
            bg={surface.chrome}
            border={`1px solid ${border.default}`}
            borderRadius={radius.full}
            backdropFilter="blur(22px) saturate(140%)"
            boxShadow={`${elevation[2]}, ${elevation.hairline}`}
          >
            <Box
              aria-hidden="true"
              width="7px"
              height="7px"
              borderRadius="full"
              bg={capability === "webxr-ready" ? "#86efac" : accent[300]}
              boxShadow={capability === "webxr-ready"
                ? "0 0 12px rgba(134,239,172,0.65)"
                : `0 0 12px ${accent.glow}`}
              animation={capability === "checking" ? "pulse 1.8s ease-in-out infinite" : undefined}
            />
            <Text {...typography.labelSmall} color={text.primary}>{capabilityLabel}</Text>
          </HStack>
          <WebSocketStatus />
        </HStack>

        <Button
          position="absolute"
          top="max(18px, env(safe-area-inset-top))"
          right="max(18px, env(safe-area-inset-right))"
          minWidth="34px"
          width="34px"
          height="34px"
          p="0"
          color={text.secondary}
          bg={surface.chrome}
          border={`1px solid ${border.default}`}
          borderRadius={radius.full}
          backdropFilter="blur(22px)"
          boxShadow={elevation[2]}
          transition={transition.control}
          _hover={{ color: text.primary, bg: surface.chromeSolid }}
          _active={{ transform: "scale(0.94)" }}
          _focusVisible={focusRing}
          onClick={() => void onExit()}
          aria-label={t("spatial.exit")}
          title={t("spatial.exit")}
          zIndex={20}
        >
          <FiX />
        </Button>

        <Box
          position="absolute"
          left="50%"
          bottom="max(24px, calc(env(safe-area-inset-bottom) + 18px))"
          width="min(720px, 72%)"
          transform="translateX(-50%)"
          pointerEvents="none"
          zIndex={20}
        >
          <Subtitle variant="spatial" />
        </Box>
      </Box>
    </Box>
  );
}
