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
} from '@/styles/tokens';

const isElectron = window.api !== undefined;

/** Fields share one resting/hover/focus treatment so the form reads as a set. */
const fieldSurface = {
  bg: surface.glass1,
  borderColor: border.subtle,
  borderRadius: radius.md,
  color: text.primary,
  transition: transition.colors,
  _hover: {
    bg: surface.glass2,
    borderColor: border.default,
  },
  _focusVisible: {
    borderColor: accent.ring,
    boxShadow: `0 0 0 3px ${accent.soft}`,
    outline: 'none',
  },
};

export const settingStyles = {
  settingUI: {
    container: {
      width: '100%',
      height: '100%',
      p: 4,
      gap: 4,
      position: 'relative',
      overflowY: 'auto',
      css: {
        '&::-webkit-scrollbar': {
          width: '8px',
        },
        '&::-webkit-scrollbar-track': {
          background: 'transparent',
        },
        '&::-webkit-scrollbar-thumb': {
          background: 'rgba(255, 255, 255, 0.14)',
          border: '2px solid transparent',
          backgroundClip: 'padding-box',
          borderRadius: '999px',
        },
      },
    },
    header: {
      width: '100%',
      display: 'flex',
      alignItems: 'center',
      gap: 1,
    },
    title: {
      ...typography.titleLarge,
      ml: 4,
    },
    tabs: {
      root: {
        width: '100%',
        variant: 'plain' as const,
        colorPalette: 'gray',
      },
      content: {},
      trigger: {
        ...typography.labelSmall,
        fontSize: '12px',
        flex: 1,
        minWidth: 'fit-content',
        height: '36px',
        px: 3,
        borderRadius: radius.sm,
        color: text.tertiary,
        transition: transition.control,
        _selected: {
          color: text.primary,
          bg: surface.glass3,
          boxShadow: elevation[1],
        },
        _hover: {
          color: text.secondary,
          bg: surface.glass2,
        },
        _focusVisible: focusRing,
      },
      list: {
        display: 'flex',
        justifyContent: 'flex-start',
        width: '100%',
        gap: 1,
        p: 1,
        bg: surface.glass1,
        border: `1px solid ${border.subtle}`,
        borderRadius: radius.lg,
        mb: 4,
        overflowX: 'auto',
      },
    },
    footer: {
      width: '100%',
      display: 'flex',
      justifyContent: 'flex-end',
      gap: 2,
      mt: 'auto',
      pt: 4,
      borderTop: '1px solid',
      borderColor: border.subtle,
    },
    drawerContent: {
      bg: 'rgba(11, 14, 21, 0.98)',
      backdropFilter: 'blur(32px) saturate(140%)',
      maxWidth: '500px',
      height: isElectron ? 'calc(100vh - 30px)' : '100vh',
      borderRight: `1px solid ${border.subtle}`,
      boxShadow: elevation[5],
    },
    drawerHeader: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      width: '100%',
      position: 'relative',
      minHeight: '68px',
      px: 6,
      py: 4,
      borderBottom: `1px solid ${border.subtle}`,
    },
    drawerTitle: {
      ...typography.title,
      color: text.primary,
    },
    closeButton: {
      position: 'absolute',
      right: 3,
      top: 3,
      color: text.secondary,
    },
  },
  general: {
    container: {
      align: 'stretch',
      gap: 6,
      py: 3,
    },
    field: {
      label: {
        color: text.secondary,
      },
    },
    select: {
      root: {
        colorPalette: 'gray',
        bg: surface.glass1,
      },
      trigger: {
        ...fieldSurface,
      },
    },
    input: {
      ...fieldSurface,
    },
    buttonGroup: {
      gap: 4,
      width: '100%',
    },
    button: {
      ...typography.label,
      width: '50%',
      variant: 'outline' as const,
      bg: surface.glass1,
      color: text.primary,
      borderColor: border.default,
      borderRadius: radius.md,
      transition: transition.control,
      _hover: {
        bg: surface.glass3,
        borderColor: border.strong,
      },
      _focusVisible: focusRing,
    },
    fieldLabel: {
      ...typography.body,
      color: text.secondary,
    },
  },
  common: {
    field: {
      orientation: 'horizontal' as const,
    },
    fieldLabel: {
      ...typography.body,
      color: text.secondary,
      whiteSpace: 'nowrap' as const,
    },
    switch: {
      size: 'md' as const,
      colorPalette: 'blue' as const,
      variant: 'solid' as const,
    },
    numberInput: {
      root: {
        pattern: '[0-9]*\\.?[0-9]*',
        inputMode: 'decimal' as const,
      },
      input: {
        ...fieldSurface,
        fontVariantNumeric: 'tabular-nums',
      },
    },
    container: {
      gap: 8,
      maxW: 'sm',
      css: { '--field-label-width': '120px' },
    },
    input: {
      ...fieldSurface,
    },
  },
};
