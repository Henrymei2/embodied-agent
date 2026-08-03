import { Box, Text } from '@chakra-ui/react';
import { memo } from 'react';
import { canvasStyles } from './canvas-styles';
import { useSubtitleDisplay } from '@/hooks/canvas/use-subtitle-display';
import { useSubtitle } from '@/context/subtitle-context';

// Type definitions
interface SubtitleTextProps {
  text: string
  variant: "default" | "spatial"
}

// Reusable components
const SubtitleText = memo(({ text, variant }: SubtitleTextProps) => (
  <Text {...(variant === "spatial" ? canvasStyles.subtitle.spatialText : canvasStyles.subtitle.text)}>
    {text}
  </Text>
));

SubtitleText.displayName = 'SubtitleText';

// Main component
interface SubtitleProps {
  variant?: "default" | "spatial"
}

const Subtitle = memo(({ variant = "default" }: SubtitleProps): JSX.Element | null => {
  const { subtitleText, isLoaded } = useSubtitleDisplay();
  const { showSubtitle } = useSubtitle();

  if (!isLoaded || !subtitleText || !showSubtitle) return null;

  return (
    <Box
      {...(variant === "spatial" ? canvasStyles.subtitle.spatialContainer : canvasStyles.subtitle.container)}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <SubtitleText text={subtitleText} variant={variant} />
    </Box>
  );
});

Subtitle.displayName = 'Subtitle';

export default Subtitle;
