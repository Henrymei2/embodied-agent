import { SystemStyleObject } from '@chakra-ui/react';
import {
  accent,
  border,
  duration,
  ease,
  elevation,
  focusRing,
  glass,
  radius,
  surface,
  text,
  transition,
  typography,
} from '@/styles/tokens';

interface FooterStyles {
  container: (isCollapsed: boolean) => SystemStyleObject
  toggleButton: SystemStyleObject
  composerWrap: SystemStyleObject
  composer: SystemStyleObject
  statePosition: SystemStyleObject
  actionButton: SystemStyleObject
  input: SystemStyleObject
  sendButton: SystemStyleObject
}

interface AIIndicatorStyles {
  container: SystemStyleObject
  text: SystemStyleObject
}

export const footerStyles: {
  footer: FooterStyles
  aiIndicator: AIIndicatorStyles
} = {
  footer: {
    container: (isCollapsed) => ({
      bg: 'transparent',
      transform: isCollapsed ? 'translateY(calc(100% - 34px))' : 'translateY(0)',
      transition: transition.transform,
      height: '100%',
      position: 'relative',
      overflow: isCollapsed ? 'visible' : 'hidden',
      pt: '8px',
    }),
    toggleButton: {
      position: 'absolute',
      top: '0',
      right: { base: '8px', md: 'calc(50% - 450px)' },
      minWidth: '34px',
      width: '34px',
      height: '24px',
      borderRadius: `${radius.sm} ${radius.sm} 0 0`,
      color: text.tertiary,
      bg: surface.chrome,
      backdropFilter: 'blur(20px)',
      border: `1px solid ${border.default}`,
      borderBottom: 0,
      _hover: {
        color: text.primary,
        bg: surface.chromeSolid,
      },
      _focusVisible: focusRing,
      transition: transition.control,
      zIndex: 3,
    },
    composerWrap: {
      width: '100%',
      maxWidth: '920px',
      height: '100%',
      mx: 'auto',
      display: 'flex',
      alignItems: 'center',
    },
    composer: {
      position: 'relative',
      display: 'flex',
      alignItems: 'center',
      width: '100%',
      height: { base: '72px', md: '78px' },
      px: { base: 2.5, md: 3 },
      gap: { base: 2, md: 2.5 },
      ...glass,
      borderRadius: { base: radius['2xl'], md: radius['3xl'] },
      boxShadow: `${elevation[4]}, ${elevation.hairline}`,
      // Focus travels up from the textarea so the whole composer lifts as one
      // object, the way a native search field does.
      transition: `border-color ${duration.normal} ${ease.out}, box-shadow ${duration.normal} ${ease.out}`,
      _focusWithin: {
        borderColor: accent.ring,
        boxShadow: `${elevation[5]}, ${elevation.hairline}, 0 0 0 3px ${accent.soft}`,
      },
    },
    statePosition: {
      position: 'absolute',
      left: { base: '14px', md: '18px' },
      top: '-13px',
      zIndex: 3,
    },
    actionButton: {
      borderRadius: radius.lg,
      width: { base: '42px', md: '46px' },
      height: { base: '42px', md: '46px' },
      minW: { base: '42px', md: '46px' },
      flexShrink: 0,
      border: '1px solid',
      fontSize: '18px',
      transition: transition.control,
      _hover: {
        filter: 'brightness(1.14)',
        transform: 'translateY(-1px)',
      },
      _active: {
        transform: 'translateY(0) scale(0.97)',
        filter: 'brightness(0.98)',
      },
      _focusVisible: focusRing,
    },
    input: {
      ...typography.body,
      bg: surface.glass1,
      border: `1px solid ${border.subtle}`,
      height: { base: '50px', md: '54px' },
      minHeight: { base: '50px', md: '54px' },
      maxHeight: { base: '50px', md: '54px' },
      borderRadius: radius.xl,
      fontSize: { base: '14px', md: '15px' },
      pl: { base: 3.5, md: 4 },
      pr: '52px',
      color: text.primary,
      transition: transition.colors,
      _placeholder: {
        color: text.quaternary,
      },
      _hover: {
        bg: surface.glass2,
      },
      _focus: {
        borderColor: border.default,
        bg: surface.glass2,
        // The composer shell owns the focus ring; the field just brightens.
        boxShadow: 'none',
        outline: 'none',
      },
      resize: 'none',
      py: { base: '14px', md: '15px' },
      lineHeight: '1.35',
    },
    sendButton: {
      position: 'absolute',
      right: '7px',
      top: '50%',
      transform: 'translateY(-50%)',
      minWidth: '38px',
      width: '38px',
      height: '38px',
      borderRadius: radius.md,
      color: accent.onSolid,
      bg: accent.contrast,
      zIndex: 2,
      transition: transition.control,
      _hover: {
        bg: '#fff',
        transform: 'translateY(-50%) scale(1.05)',
      },
      _active: {
        transform: 'translateY(-50%) scale(0.96)',
      },
      _focusVisible: focusRing,
      _disabled: {
        color: text.quaternary,
        bg: surface.glass2,
        opacity: 1,
        transform: 'translateY(-50%)',
        cursor: 'not-allowed',
      },
    },
  },
  aiIndicator: {
    container: {
      bg: surface.chromeSolid,
      backdropFilter: 'blur(20px)',
      color: text.secondary,
      minWidth: '86px',
      height: '26px',
      px: '10px',
      border: `1px solid ${border.default}`,
      borderRadius: radius.full,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 2,
      boxShadow: elevation[2],
      overflow: 'hidden',
      _before: {
        content: '""',
        width: '6px',
        height: '6px',
        flexShrink: 0,
        borderRadius: radius.full,
        bg: accent[400],
        boxShadow: `0 0 10px ${accent.glow}`,
      },
    },
    text: {
      ...typography.labelSmall,
      fontSize: '10px',
      textTransform: 'capitalize',
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
    },
  },
};
