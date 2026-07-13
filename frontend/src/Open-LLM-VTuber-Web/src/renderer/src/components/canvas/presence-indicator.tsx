import { Box, Button, HStack, Text, VStack } from "@chakra-ui/react";
import { useState } from "react";
import { FiActivity, FiClock, FiEye, FiEyeOff } from "react-icons/fi";
import { useTranslation } from "react-i18next";
import { usePresence } from "@/context/presence-context";

function PresenceIndicator(): JSX.Element {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const { state, focusMode, latestReceipt, setFocusMode, sendFeedback } =
    usePresence();

  const budget = state.attention_budget ?? 0;
  const maxBudget = state.attention_budget_max ?? 0;
  const fillPercent =
    maxBudget > 0 ? Math.min(100, (budget / maxBudget) * 100) : 0;
  const receiptNeedsFeedback = latestReceipt && latestReceipt.feedback === null;

  return (
    <Box position="relative">
      <Button
        size="sm"
        bg={focusMode ? "purple.600" : "gray.800"}
        color="white"
        borderRadius="full"
        boxShadow="0 8px 24px rgba(0, 0, 0, 0.22)"
        onClick={() => setExpanded((value) => !value)}
        aria-label={t("presence.openPanel")}
      >
        {focusMode ? <FiEyeOff /> : <FiActivity />}
        {state.enabled
          ? t("presence.budgetCompact", {
              budget: budget.toFixed(1),
              max: maxBudget,
            })
          : t("presence.disabled")}
      </Button>

      {expanded && (
        <Box
          position="absolute"
          top="calc(100% + 8px)"
          right="0"
          width="320px"
          bg="gray.900"
          color="white"
          border="1px solid"
          borderColor="whiteAlpha.200"
          borderRadius="16px"
          boxShadow="0 18px 48px rgba(0, 0, 0, 0.35)"
          p="4"
        >
          <VStack align="stretch" gap="3">
            <HStack justify="space-between">
              <Box>
                <Text fontWeight="700">{t("presence.title")}</Text>
                <Text fontSize="xs" color="whiteAlpha.600">
                  {t("presence.subtitle")}
                </Text>
              </Box>
              <Button
                size="xs"
                variant={focusMode ? "solid" : "outline"}
                colorPalette="purple"
                onClick={() => setFocusMode(!focusMode)}
              >
                {focusMode ? <FiEyeOff /> : <FiEye />}
                {focusMode ? t("presence.focusOn") : t("presence.focusOff")}
              </Button>
            </HStack>

            {state.enabled ? (
              <>
                <Box>
                  <HStack justify="space-between" mb="1">
                    <Text fontSize="xs" color="whiteAlpha.700">
                      {t("presence.attentionBudget")}
                    </Text>
                    <Text fontSize="xs" fontWeight="700">
                      {budget.toFixed(2)} / {maxBudget}
                    </Text>
                  </HStack>
                  <Box height="6px" bg="whiteAlpha.200" borderRadius="full">
                    <Box
                      height="100%"
                      width={`${fillPercent}%`}
                      bg="purple.400"
                      borderRadius="full"
                      transition="width 0.2s ease"
                    />
                  </Box>
                  <Text mt="1" fontSize="10px" color="whiteAlpha.500">
                    {t("presence.refill", {
                      rate: state.attention_refill_per_hour ?? 0,
                    })}
                  </Text>
                </Box>

                {state.snoozed && (
                  <HStack
                    bg="orange.950"
                    color="orange.200"
                    borderRadius="10px"
                    px="3"
                    py="2"
                    fontSize="xs"
                  >
                    <FiClock />
                    <Text>{t("presence.snoozed")}</Text>
                  </HStack>
                )}

                {latestReceipt ? (
                  <Box bg="whiteAlpha.100" borderRadius="12px" p="3">
                    <HStack justify="space-between" mb="1">
                      <Text
                        fontSize="xs"
                        fontWeight="700"
                        textTransform="capitalize"
                      >
                        {t("presence.latestReceipt")}: {latestReceipt.direction}
                      </Text>
                      <Text fontSize="10px" color="purple.200">
                        -{latestReceipt.attention_cost}
                      </Text>
                    </HStack>
                    <Text fontSize="xs" color="whiteAlpha.700">
                      {latestReceipt.reason}
                    </Text>
                    <Text mt="1" fontSize="10px" color="whiteAlpha.500">
                      {t("presence.receiptContext", {
                        activity: latestReceipt.signals.activity,
                        score: Math.round(latestReceipt.score * 100),
                      })}
                    </Text>
                    <HStack mt="3" gap="2">
                      <Button
                        size="xs"
                        colorPalette="green"
                        disabled={!receiptNeedsFeedback}
                        onClick={() =>
                          sendFeedback(latestReceipt.receipt_id, "helpful")
                        }
                      >
                        {t("presence.helpful")}
                      </Button>
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={!receiptNeedsFeedback}
                        onClick={() =>
                          sendFeedback(latestReceipt.receipt_id, "dismissed")
                        }
                      >
                        {t("presence.notNow")}
                      </Button>
                      <Button
                        size="xs"
                        variant="outline"
                        colorPalette="orange"
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
                  <Text fontSize="xs" color="whiteAlpha.600">
                    {t("presence.noReceipts")}
                  </Text>
                )}
              </>
            ) : (
              <Text fontSize="sm" color="whiteAlpha.700">
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
