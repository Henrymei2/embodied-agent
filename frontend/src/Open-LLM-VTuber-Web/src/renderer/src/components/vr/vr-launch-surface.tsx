import { Box, Button, HStack, Spinner, Text, VStack } from "@chakra-ui/react";
import { FiBox, FiMonitor } from "react-icons/fi";
import { useTranslation } from "react-i18next";
import type { RendererMode } from "@/context/renderer-mode-context";
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

interface VrLaunchSurfaceProps {
  mode: Extract<RendererMode, "detecting" | "choosing" | "vr-starting">;
  error: string | null;
  onEnter: () => void;
  onContinue: () => void;
}

export default function VrLaunchSurface({
  mode,
  error,
  onEnter,
  onContinue,
}: VrLaunchSurfaceProps): JSX.Element {
  const { t } = useTranslation();
  const busy = mode === "detecting" || mode === "vr-starting";

  return (
    <Box
      position="fixed"
      inset="0"
      overflow="hidden"
      display="grid"
      placeItems="center"
      px="6"
      color={text.primary}
      bg="radial-gradient(circle at 50% 18%, #242b56 0%, #101529 38%, #06080f 78%)"
    >
      <Box
        aria-hidden="true"
        position="absolute"
        width="440px"
        height="440px"
        borderRadius="full"
        bg="rgba(129, 140, 248, 0.2)"
        filter="blur(100px)"
        transform="translate(-42%, -40%)"
      />
      <Box
        aria-hidden="true"
        position="absolute"
        width="300px"
        height="300px"
        right="-80px"
        bottom="-80px"
        borderRadius="full"
        bg="rgba(56, 189, 248, 0.13)"
        filter="blur(90px)"
      />

      <VStack
        position="relative"
        width="min(560px, 100%)"
        gap="5"
        p={{ base: "7", md: "10" }}
        textAlign="center"
        bg={surface.chrome}
        border={`1px solid ${border.default}`}
        borderRadius="28px"
        backdropFilter="blur(30px) saturate(145%)"
        boxShadow={`${elevation[5]}, ${elevation.hairline}`}
      >
        <HStack
          role="status"
          gap="2"
          px="3"
          height="30px"
          borderRadius={radius.full}
          bg={surface.glass2}
          border={`1px solid ${border.subtle}`}
        >
          {busy ? <Spinner size="xs" color={accent[300]} /> : (
            <Box
              width="7px"
              height="7px"
              borderRadius="full"
              bg="#86efac"
              boxShadow="0 0 14px rgba(134,239,172,0.72)"
            />
          )}
          <Text {...typography.labelSmall} color={text.secondary}>
            {t(mode === "detecting" ? "spatial.checking" : mode === "vr-starting" ? "spatial.starting" : "spatial.headsetDetected")}
          </Text>
        </HStack>

        <Box
          aria-hidden="true"
          display="grid"
          placeItems="center"
          width="82px"
          height="82px"
          borderRadius="24px"
          color="#eef2ff"
          bg="linear-gradient(145deg, rgba(165,180,252,0.3), rgba(99,102,241,0.11))"
          border="1px solid rgba(199,210,254,0.34)"
          boxShadow="inset 0 1px rgba(255,255,255,0.18), 0 18px 52px rgba(79,70,229,0.3)"
        >
          <FiBox size={35} />
        </Box>

        <VStack gap="2">
          <Text fontSize={{ base: "30px", md: "38px" }} lineHeight="1.08" fontWeight="650" letterSpacing="-0.035em">
            {t("spatial.launchTitle")}
          </Text>
          <Text maxWidth="430px" fontSize={{ base: "14px", md: "16px" }} lineHeight="1.65" color={text.secondary}>
            {t("spatial.launchDescription")}
          </Text>
        </VStack>

        {error && (
          <Box
            role="alert"
            width="100%"
            px="4"
            py="3"
            borderRadius={radius.lg}
            bg="rgba(127,29,29,0.28)"
            border="1px solid rgba(252,165,165,0.28)"
          >
            <Text fontSize="13px" color="#fecaca">{error}</Text>
          </Box>
        )}

        <HStack width="100%" justify="center" gap="3" flexWrap="wrap">
          <Button
            height="44px"
            px="5"
            gap="2"
            color="#080a12"
            bg="#f5f7ff"
            borderRadius={radius.full}
            boxShadow="0 10px 32px rgba(199,210,254,0.22)"
            transition={transition.control}
            disabled={busy}
            _hover={{ bg: "white", transform: "translateY(-1px)" }}
            _active={{ transform: "scale(0.98)" }}
            _focusVisible={focusRing}
            onClick={onEnter}
          >
            {mode === "vr-starting" ? <Spinner size="sm" /> : <FiBox />}
            {t(mode === "vr-starting" ? "spatial.starting" : "spatial.enterVr")}
          </Button>
          <Button
            height="44px"
            px="5"
            gap="2"
            color={text.secondary}
            bg={surface.glass1}
            border={`1px solid ${border.subtle}`}
            borderRadius={radius.full}
            transition={transition.control}
            disabled={mode === "vr-starting"}
            _hover={{ color: text.primary, bg: surface.glass3 }}
            _active={{ transform: "scale(0.98)" }}
            _focusVisible={focusRing}
            onClick={onContinue}
          >
            <FiMonitor />
            {t("spatial.continueScreen")}
          </Button>
        </HStack>

        <Text {...typography.labelSmall} color={text.tertiary}>
          {t("spatial.controlsHint")}
        </Text>
      </VStack>
    </Box>
  );
}
