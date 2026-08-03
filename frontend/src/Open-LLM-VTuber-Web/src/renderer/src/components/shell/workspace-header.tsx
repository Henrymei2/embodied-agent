import { Box, Flex, HStack, Text } from "@chakra-ui/react";
import { ReactNode } from "react";
import { FiHome } from "react-icons/fi";
import { useTranslation } from "react-i18next";
import {
  accent,
  border,
  radius,
  surface,
  text,
  typography,
} from "@/styles/tokens";

interface WorkspaceHeaderProps {
  sidebarCollapsed: boolean;
  roomEnabled: boolean;
  connectionStatus: ReactNode;
  presenceControl: ReactNode;
  spatialControl?: ReactNode;
}

function WorkspaceHeader({
  sidebarCollapsed,
  roomEnabled,
  connectionStatus,
  presenceControl,
  spatialControl,
}: WorkspaceHeaderProps): JSX.Element {
  const { t } = useTranslation();

  return (
    <Flex
      as="header"
      height={{ base: "64px", md: "72px" }}
      flexShrink={0}
      alignItems="center"
      justifyContent="space-between"
      pl={{
        base: sidebarCollapsed ? "68px" : "16px",
        md: "28px",
      }}
      pr={{ base: 3, sm: 5, lg: 7 }}
      transition="padding var(--duration-slow) var(--ease-spring)"
      position="relative"
      zIndex={20}
    >
      <HStack gap="3" minWidth={0}>
        <Flex
          display={{ base: "none", sm: "flex" }}
          width="34px"
          height="34px"
          flexShrink={0}
          alignItems="center"
          justifyContent="center"
          borderRadius={radius.md}
          bg={surface.glass2}
          border={`1px solid ${border.subtle}`}
          color={accent[300]}
        >
          <FiHome size={16} />
        </Flex>
        <Box minWidth={0}>
          <Text {...typography.title} color={text.primary} truncate>
            {t("workspace.title")}
          </Text>
          <Text
            {...typography.caption}
            display={{ base: "none", sm: "block" }}
            mt="1px"
            color={text.tertiary}
            truncate
          >
            {t(roomEnabled ? "workspace.subtitle" : "workspace.subtitleWithoutRoom")}
          </Text>
        </Box>
      </HStack>

      <HStack gap={{ base: 2, md: 3 }} flexShrink={0}>
        {spatialControl}
        {connectionStatus}
        {presenceControl}
      </HStack>
    </Flex>
  );
}

export default WorkspaceHeader;
