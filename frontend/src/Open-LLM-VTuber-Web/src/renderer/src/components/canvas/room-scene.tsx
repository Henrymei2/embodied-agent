import { Box, Text } from "@chakra-ui/react";
import type { SceneObject } from "@/context/room-scene-context";
import { useRoomScene } from "@/context/room-scene-context";

const objectSize: Record<SceneObject["label"], { width: number; height: number }> = {
  computer: { width: 150, height: 104 },
  calendar: { width: 118, height: 92 },
  bed: { width: 188, height: 118 },
};

const objectColors: Record<SceneObject["label"], { bg: string; border: string }> = {
  computer: { bg: "rgba(79, 70, 229, 0.88)", border: "rgba(199, 210, 254, 0.9)" },
  calendar: { bg: "rgba(13, 148, 136, 0.9)", border: "rgba(153, 246, 228, 0.9)" },
  bed: { bg: "rgba(225, 29, 72, 0.86)", border: "rgba(254, 205, 211, 0.9)" },
};

function RoomObject({ object }: { object: SceneObject }) {
  const { roomSize } = useRoomScene();
  const size = objectSize[object.label];
  const colors = objectColors[object.label];

  return (
    <Box
      aria-label={object.label}
      position="absolute"
      left={`${(object.x / roomSize.x) * 100}%`}
      top={`${(object.y / roomSize.y) * 100}%`}
      width={`${(size.width / roomSize.x) * 100}%`}
      height={`${(size.height / roomSize.y) * 100}%`}
      minWidth="72px"
      minHeight="52px"
      transform="translate(-50%, -50%)"
      border="2px solid"
      borderColor={colors.border}
      borderRadius="8px"
      bg={colors.bg}
      boxShadow="0 16px 34px rgba(15, 23, 42, 0.28)"
      display="flex"
      alignItems="center"
      justifyContent="center"
      overflow="hidden"
    >
      <Box
        position="absolute"
        inset="8px"
        border="1px solid"
        borderColor="whiteAlpha.500"
        borderRadius="5px"
        opacity={0.72}
      />
      <Text
        as="span"
        color="white"
        fontSize={{ base: "xs", md: "sm" }}
        fontWeight="700"
        lineHeight="1"
        textTransform="uppercase"
        textAlign="center"
        letterSpacing="0"
        zIndex={1}
      >
        {object.label}
      </Text>
    </Box>
  );
}

export default function RoomScene(): JSX.Element {
  const { avatarPosition, objects, roomSize } = useRoomScene();

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
        aria-label="2d room"
        position="absolute"
        inset="0"
        bg="linear-gradient(180deg, #d7ecf3 0%, #d7ecf3 48%, #c6a983 48%, #c6a983 100%)"
      >
        <Box
          position="absolute"
          left="0"
          right="0"
          top="48%"
          height="2px"
          bg="rgba(88, 67, 48, 0.32)"
        />
        <Box
          position="absolute"
          left="8%"
          right="8%"
          bottom="0"
          height="38%"
          bg="repeating-linear-gradient(90deg, rgba(104, 75, 46, 0.16) 0 2px, transparent 2px 86px)"
        />
        <Box
          position="absolute"
          left="42%"
          top="10%"
          width="16%"
          height="22%"
          border="8px solid rgba(255, 255, 255, 0.78)"
          borderRadius="8px"
          bg="linear-gradient(135deg, #8bc7df, #f8fbff)"
          boxShadow="0 12px 28px rgba(15, 23, 42, 0.14)"
        />
      </Box>

      {objects.map((object) => (
        <RoomObject key={object.label} object={object} />
      ))}

      <Box
        aria-label="avatar"
        position="absolute"
        left={`${(avatarPosition.x / roomSize.x) * 100}%`}
        top={`${(avatarPosition.y / roomSize.y) * 100}%`}
        width={{ base: "54px", md: "70px" }}
        height={{ base: "74px", md: "92px" }}
        transform="translate(-50%, -78%)"
        transition="left 900ms ease-in-out, top 900ms ease-in-out"
        display="flex"
        flexDirection="column"
        alignItems="center"
        justifyContent="flex-end"
        zIndex={3}
      >
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
          bg="#2563eb"
          border="2px solid rgba(191, 219, 254, 0.9)"
          boxShadow="0 14px 24px rgba(15, 23, 42, 0.28)"
        />
        <Text
          as="span"
          mt="4px"
          color="#172554"
          fontSize={{ base: "10px", md: "xs" }}
          fontWeight="700"
          lineHeight="1"
          letterSpacing="0"
          textShadow="0 1px 0 rgba(255, 255, 255, 0.72)"
        >
          avatar
        </Text>
      </Box>
    </Box>
  );
}
