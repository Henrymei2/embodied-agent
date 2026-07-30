import { Box, Button, HStack, Text, VStack } from "@chakra-ui/react";
import { useState } from "react";
import { FiActivity, FiClock, FiEye, FiEyeOff } from "react-icons/fi";
import { useTranslation } from "react-i18next";
import { usePresence } from "@/context/presence-context";
import {
  accent,
  border,
  elevation,
  focusRing,
  radius,
  status,
  surface,
  text,
  transition,
  typography,
} from "@/styles/tokens";

function PresenceIndicator(): JSX.Element {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const { state, focusMode, latestReceipt, setFocusMode, sendFeedback } =
    usePresence();

  const budget = state.attention_budget ?? 0;
  const maxBudget = state.attention_budget_max ?? 0;
  const fillPercent =
    maxBudget > 0 ? Math.min(100, (budget / maxBudget) * 100) : 0;
  const receiptNeedsFeedback =
    latestReceipt?.status === "succeeded" && latestReceipt.feedback === null;

  return (
    <Box position="relative">
      <Button
        {...typography.labelSmall}
        height="34px"
        px={{ base: "10px", md: "12px" }}
        gap="1.5"
        bg={focusMode ? accent.soft : surface.glass1}
        color={focusMode ? accent[200] : text.secondary}
        border="1px solid"
        borderColor={focusMode ? accent.ring : border.subtle}
        borderRadius={radius.md}
        backdropFilter="blur(14px)"
        boxShadow="none"
        transition={transition.control}
        // The budget ticks down live — tabular figures keep the chip from
        // twitching as the digits change width.
        fontVariantNumeric="tabular-nums"
        _hover={{
          bg: focusMode ? accent.softHover : surface.glass3,
          color: focusMode ? accent[200] : text.primary,
        }}
        _active={{ transform: "scale(0.97)" }}
        _focusVisible={focusRing}
        onClick={() => setExpanded((value) => !value)}
        aria-label={t("presence.openPanel")}
        aria-expanded={expanded}
      >
        {focusMode ? <FiEyeOff /> : <FiActivity />}
        <Text display={{ base: "none", md: "block" }}>
          {state.enabled
            ? t("presence.budgetCompact", {
                budget: budget.toFixed(1),
                max: maxBudget,
              })
            : t("presence.disabled")}
        </Text>
      </Button>

      {expanded && (
        <Box
          position="absolute"
          top="calc(100% + 10px)"
          right="0"
          width={{ base: "min(340px, calc(100vw - 24px))", md: "340px" }}
          bg={surface.chromeSolid}
          backdropFilter="blur(32px) saturate(140%)"
          color={text.primary}
          border="1px solid"
          borderColor={border.default}
          borderRadius={radius["2xl"]}
          boxShadow={`${elevation[5]}, ${elevation.hairline}`}
          transformOrigin="top right"
          animation="surfaceIn var(--duration-fast) var(--ease-out)"
          p="5"
          zIndex={40}
        >
          <VStack align="stretch" gap="4">
            <HStack justify="space-between" align="flex-start" gap="3">
              <Box minWidth={0}>
                <Text {...typography.title}>{t("presence.title")}</Text>
                <Text
                  {...typography.caption}
                  mt="1"
                  color={text.tertiary}
                >
                  {t("presence.subtitle")}
                </Text>
              </Box>
              <Button
                size="xs"
                flexShrink={0}
                borderRadius={radius.sm}
                variant={focusMode ? "solid" : "outline"}
                colorPalette="purple"
                transition={transition.control}
                _focusVisible={focusRing}
                onClick={() => setFocusMode(!focusMode)}
              >
                {focusMode ? <FiEyeOff /> : <FiEye />}
                {focusMode ? t("presence.focusOn") : t("presence.focusOff")}
              </Button>
            </HStack>

            {state.enabled ? (
              <>
                <Box>
                  <HStack justify="space-between" mb="1.5">
                    <Text {...typography.caption} color={text.tertiary}>
                      {t("presence.attentionBudget")}
                    </Text>
                    <Text
                      {...typography.labelSmall}
                      fontVariantNumeric="tabular-nums"
                    >
                      {budget.toFixed(2)} / {maxBudget}
                    </Text>
                  </HStack>
                  <Box
                    role="progressbar"
                    aria-label={t("presence.attentionBudget")}
                    aria-valuenow={Number(budget.toFixed(2))}
                    aria-valuemin={0}
                    aria-valuemax={maxBudget}
                    height="6px"
                    bg={surface.glass3}
                    borderRadius={radius.full}
                    overflow="hidden"
                  >
                    <Box
                      height="100%"
                      width={`${fillPercent}%`}
                      bg="linear-gradient(90deg, #7786ff, #a864e8)"
                      borderRadius={radius.full}
                      transition="width var(--duration-normal) var(--ease-out)"
                    />
                  </Box>
                  <Text {...typography.caption} mt="1.5" color={text.quaternary}>
                    {t("presence.refill", {
                      rate: state.attention_refill_per_hour ?? 0,
                    })}
                  </Text>
                </Box>

                {state.snoozed && (
                  <HStack
                    {...typography.caption}
                    bg={status.warningBg}
                    color={status.warningFg}
                    border={`1px solid ${status.warningBorder}`}
                    borderRadius={radius.sm}
                    px="3"
                    py="2"
                  >
                    <FiClock />
                    <Text>{t("presence.snoozed")}</Text>
                  </HStack>
                )}

                {latestReceipt ? (
                  <Box
                    bg={surface.glass1}
                    border={`1px solid ${border.subtle}`}
                    borderRadius={radius.lg}
                    p="3"
                  >
                    <HStack justify="space-between" mb="1" gap="2">
                      <Text
                        {...typography.labelSmall}
                        textTransform="capitalize"
                        truncate
                      >
                        {t("presence.latestReceipt")}: {latestReceipt.direction}
                      </Text>
                      <Text
                        {...typography.caption}
                        flexShrink={0}
                        color={accent[200]}
                        fontVariantNumeric="tabular-nums"
                      >
                        -{latestReceipt.attention_cost}
                      </Text>
                    </HStack>
                    <Text {...typography.footnote} color={text.secondary}>
                      {latestReceipt.reason}
                    </Text>
                    <Text {...typography.caption} mt="1" color={text.quaternary}>
                      {t("presence.receiptContext", {
                        activity: latestReceipt.signals.activity,
                        score: Math.round(latestReceipt.score * 100),
                        status: latestReceipt.status,
                      })}
                    </Text>
                    {latestReceipt.error && (
                      <Text
                        {...typography.caption}
                        mt="1"
                        color={status.dangerFg}
                      >
                        {latestReceipt.error}
                      </Text>
                    )}
                    <HStack mt="3" gap="2">
                      <Button
                        size="xs"
                        borderRadius={radius.sm}
                        colorPalette="green"
                        transition={transition.control}
                        _focusVisible={focusRing}
                        disabled={!receiptNeedsFeedback}
                        onClick={() =>
                          sendFeedback(latestReceipt.receipt_id, "helpful")
                        }
                      >
                        {t("presence.helpful")}
                      </Button>
                      <Button
                        size="xs"
                        borderRadius={radius.sm}
                        variant="outline"
                        transition={transition.control}
                        _focusVisible={focusRing}
                        disabled={!receiptNeedsFeedback}
                        onClick={() =>
                          sendFeedback(latestReceipt.receipt_id, "dismissed")
                        }
                      >
                        {t("presence.notNow")}
                      </Button>
                      <Button
                        size="xs"
                        borderRadius={radius.sm}
                        variant="outline"
                        colorPalette="orange"
                        transition={transition.control}
                        _focusVisible={focusRing}
                        disabled={!receiptNeedsFeedback}
                        onClick={() =>
                          sendFeedback(latestReceipt.receipt_id, "snoozed", 60)
                        }
                      >
                        {t("presence.snooze")}
                      </Button>
                    </HStack>
                  </Box>
                ) : (
                  <Text {...typography.footnote} color={text.tertiary}>
                    {t("presence.noReceipts")}
                  </Text>
                )}
              </>
            ) : (
              <Text {...typography.body} color={text.secondary}>
                {t("presence.disabledDescription")}
              </Text>
            )}
          </VStack>
        </Box>
      )}
    </Box>
  );
}

export default PresenceIndicator;
