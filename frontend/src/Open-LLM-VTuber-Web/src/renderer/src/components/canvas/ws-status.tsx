import { Box, Text } from '@chakra-ui/react';
import React, { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { canvasStyles } from './canvas-styles';
import { useWSStatus } from '@/hooks/canvas/use-ws-status';
import { surface, text as textColor } from '@/styles/tokens';

// Type definitions
interface StatusContentProps {
  textKey: string
}

/**
 * How each connection state reads on screen. The hook stays the authority on
 * *which* state we are in; this map is only how that state is drawn, so the
 * indicator can use the app's own palette instead of Chakra's stock 500s.
 */
const DOT_APPEARANCE: Record<string, { bg: string; glow: string; pulse: boolean }> = {
  'green.500': { bg: '#4ade80', glow: 'rgba(74, 222, 128, 0.55)', pulse: false },
  'yellow.500': { bg: '#fbbf24', glow: 'rgba(251, 191, 36, 0.55)', pulse: true },
  'red.500': { bg: '#f87171', glow: 'rgba(248, 113, 113, 0.55)', pulse: false },
};

const FALLBACK_DOT = DOT_APPEARANCE['red.500'];

// Reusable components
const StatusContent: React.FC<StatusContentProps> = ({ textKey }) => {
  const { t } = useTranslation();
  return t(textKey);
};
const MemoizedStatusContent = memo(StatusContent);

// Main component
const WebSocketStatus = memo((): JSX.Element => {
  const { t } = useTranslation();
  const {
    color, textKey, handleClick, isDisconnected,
  } = useWSStatus();
  const dot = DOT_APPEARANCE[color] || FALLBACK_DOT;

  return (
    <Box
      {...canvasStyles.wsStatus.container}
      onClick={handleClick}
      cursor={isDisconnected ? 'pointer' : 'default'}
      _hover={{
        background: isDisconnected ? surface.glass3 : surface.glass1,
        color: isDisconnected ? textColor.primary : textColor.secondary,
      }}
      role={isDisconnected ? 'button' : 'status'}
      tabIndex={isDisconnected ? 0 : undefined}
      aria-label={t(textKey)}
      onKeyDown={(event) => {
        if (isDisconnected && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          handleClick();
        }
      }}
    >
      <Box
        aria-hidden="true"
        width="7px"
        height="7px"
        flexShrink={0}
        borderRadius="full"
        bg={dot.bg}
        boxShadow={`0 0 10px ${dot.glow}`}
        animation={dot.pulse ? 'pulse 1.8s ease-in-out infinite' : undefined}
      />
      <Text display={{ base: 'none', sm: 'block' }} truncate>
        <MemoizedStatusContent textKey={textKey} />
      </Text>
    </Box>
  );
});

WebSocketStatus.displayName = 'WebSocketStatus';

export default WebSocketStatus;
