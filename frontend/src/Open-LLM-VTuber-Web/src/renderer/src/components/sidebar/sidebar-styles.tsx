import { css } from '@emotion/react';
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
} from '@/styles/tokens';

const isElectron = window.api !== undefined;

const commonStyles = {
  scrollbar: {
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
    '&::-webkit-scrollbar-thumb:hover': {
      background: 'rgba(255, 255, 255, 0.26)',
      backgroundClip: 'padding-box',
    },
  },
  panel: {
    border: '1px solid',
    borderColor: border.subtle,
    borderRadius: radius.xl,
    bg: surface.glass1,
  },
  title: {
    ...typography.overline,
    color: text.tertiary,
    mb: 3,
  },
};

export const sidebarStyles = {
  sidebar: {
    container: (isCollapsed: boolean) => ({
      position: 'absolute' as const,
      left: 0,
      top: 0,
      height: '100%',
      width: { base: 'min(88vw, 372px)', md: '372px' },
      bg: 'rgba(10, 13, 20, 0.94)',
      backdropFilter: 'blur(32px) saturate(140%)',
      borderRight: `1px solid ${border.subtle}`,
      boxShadow: isCollapsed ? 'none' : elevation[4],
      transform: isCollapsed
        ? 'translateX(calc(-100% + 56px))'
        : 'translateX(0)',
      transition: transition.layout,
      display: 'flex',
      flexDirection: 'column' as const,
      overflow: isCollapsed ? 'visible' : 'hidden',
      p: isCollapsed ? 0 : { base: '12px', md: '14px' },
    }),
    toggleButton: {
      position: 'absolute',
      right: '10px',
      top: '50%',
      transform: 'translateY(-50%)',
      minWidth: '32px',
      width: '32px',
      height: '48px',
      borderRadius: radius.md,
      color: text.tertiary,
      bg: surface.glass1,
      border: `1px solid ${border.subtle}`,
      _hover: {
        color: text.primary,
        bg: surface.glass3,
        borderColor: border.default,
      },
      _active: {
        bg: surface.glass2,
      },
      _focusVisible: focusRing,
      transition: transition.control,
      zIndex: 5,
    },
    content: {
      flex: 1,
      width: '100%',
      display: 'flex',
      flexDirection: 'column' as const,
      gap: 0,
      overflow: 'hidden',
      pr: '42px',
    },
    header: {
      width: '100%',
      display: 'flex',
      alignItems: 'center',
      px: 1,
      pt: 4,
      pb: 3,
    },
    brandRow: {
      minHeight: '52px',
      alignItems: 'center',
      gap: 3,
      px: 1,
      pt: 1,
    },
    brandMark: {
      width: '38px',
      height: '38px',
      flexShrink: 0,
      alignItems: 'center',
      justifyContent: 'center',
      borderRadius: radius.lg,
      color: 'white',
      bg: 'linear-gradient(145deg, #7987ff 0%, #a855f7 100%)',
      boxShadow: `0 8px 24px rgba(111, 94, 255, 0.28), ${elevation.hairline}`,
    },
    collapsedBrand: {
      position: 'absolute',
      top: '18px',
      right: '10px',
      width: '36px',
      height: '36px',
      alignItems: 'center',
      justifyContent: 'center',
      borderRadius: radius.md,
      color: 'white',
      bg: 'linear-gradient(145deg, #7987ff 0%, #a855f7 100%)',
      boxShadow: `0 8px 22px rgba(111, 94, 255, 0.26), ${elevation.hairline}`,
    },
    brandName: {
      fontFamily: 'var(--font-display)',
      fontSize: '14px',
      fontWeight: '700',
      letterSpacing: '0.1em',
      color: text.primary,
      lineHeight: 1.1,
    },
    brandTagline: {
      ...typography.caption,
      mt: '4px',
      color: text.tertiary,
      lineHeight: 1,
    },
    newConversationButton: {
      ...typography.label,
      height: '38px',
      flex: 1,
      borderRadius: radius.md,
      bg: accent.contrast,
      color: accent.onSolid,
      transition: transition.control,
      _hover: {
        bg: 'white',
        transform: 'translateY(-1px)',
        boxShadow: elevation[2],
      },
      _active: {
        transform: 'translateY(0) scale(0.98)',
      },
      _focusVisible: focusRing,
    },
    utilityButton: {
      minWidth: '38px',
      width: '38px',
      height: '38px',
      borderRadius: radius.md,
      variant: 'ghost' as const,
      color: text.secondary,
      bg: surface.glass1,
      border: `1px solid ${border.subtle}`,
      transition: transition.control,
      _hover: {
        color: text.primary,
        bg: surface.glass3,
        borderColor: border.default,
      },
      _active: {
        transform: 'scale(0.95)',
      },
      _focusVisible: focusRing,
    },
    modeMenu: {
      bg: surface.overlay,
      backdropFilter: 'blur(24px) saturate(140%)',
      border: `1px solid ${border.default}`,
      borderRadius: radius.lg,
      boxShadow: elevation[4],
      color: text.primary,
      p: 1,
      animation: `surfaceIn var(--duration-fast) var(--ease-out)`,
    },
  },

  chatHistoryPanel: {
    container: {
      flex: 1,
      overflow: 'hidden',
      px: 1,
      display: 'flex',
      flexDirection: 'column',
      minHeight: 0,
    },
    title: commonStyles.title,
    messageList: {
      ...commonStyles.panel,
      p: 2,
      width: '100%',
      flex: 1,
      overflowY: 'auto',
      css: {
        ...commonStyles.scrollbar,
        scrollPaddingBottom: '1rem',
      },
      display: 'flex',
      flexDirection: 'column',
      gap: 2,
    },
  },

  systemLogPanel: {
    container: {
      width: '100%',
      overflow: 'hidden',
      px: 4,
      minH: '200px',
      marginTop: 'auto',
    },
    title: commonStyles.title,
    logList: {
      ...commonStyles.panel,
      p: 4,
      height: '200px',
      overflowY: 'auto',
      fontFamily: 'var(--font-mono)',
      fontSize: '12px',
      css: commonStyles.scrollbar,
    },
    entry: {
      p: 2,
      borderRadius: radius.xs,
      transition: transition.colors,
      _hover: {
        bg: surface.glass1,
      },
    },
  },

  chatBubble: {
    container: {
      display: 'flex',
      position: 'relative',
      transition: transition.colors,
      _hover: {
        bg: surface.glass1,
      },
      py: 1,
      px: 2,
      borderRadius: radius.sm,
    },
    message: {
      maxW: '90%',
      bg: 'transparent',
      p: 2,
    },
    text: {
      ...typography.footnote,
      color: text.primary,
    },
    dot: {
      position: 'absolute',
      w: '2',
      h: '2',
      borderRadius: radius.full,
      bg: 'white',
      top: '2',
    },
  },

  historyDrawer: {
    listContainer: {
      flex: 1,
      overflowY: 'auto',
      px: 4,
      py: 2,
      css: commonStyles.scrollbar,
    },
    historyItem: {
      mb: 3,
      p: 3,
      borderRadius: radius.lg,
      bg: surface.glass1,
      border: `1px solid ${border.subtle}`,
      cursor: 'pointer',
      transition: transition.control,
      _hover: {
        bg: surface.glass2,
        borderColor: border.default,
      },
      _focusVisible: focusRing,
    },
    historyItemSelected: {
      bg: accent.soft,
      borderColor: accent.ring,
    },
    historyHeader: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      mb: 2,
    },
    timestamp: {
      ...typography.caption,
      color: text.tertiary,
      fontVariantNumeric: 'tabular-nums',
    },
    deleteButton: {
      variant: 'ghost' as const,
      colorScheme: 'red' as const,
      size: 'sm' as const,
      color: status.dangerFg,
      borderRadius: radius.sm,
      opacity: 0.8,
      transition: transition.control,
      _hover: {
        opacity: 1,
        bg: status.dangerBg,
      },
      _focusVisible: focusRing,
    },
    messagePreview: {
      ...typography.footnote,
      color: text.secondary,
      noOfLines: 2,
      overflow: 'hidden',
      textOverflow: 'ellipsis',
    },
    drawer: {
      content: {
        background: surface.raised,
        maxWidth: '480px',
        marginTop: isElectron ? '30px' : '0',
        height: isElectron ? 'calc(100vh - 30px)' : '100vh',
        borderRight: `1px solid ${border.subtle}`,
        boxShadow: elevation[5],
      },
      title: {
        ...typography.title,
        color: text.primary,
      },
      closeButton: {
        color: text.secondary,
        borderRadius: radius.sm,
        _hover: { bg: surface.glass2, color: text.primary },
        _focusVisible: focusRing,
      },
      actionButton: {
        ...typography.label,
        color: text.primary,
        borderColor: border.default,
        borderRadius: radius.md,
        variant: 'outline' as const,
        transition: transition.control,
        _hover: { bg: surface.glass2, borderColor: border.strong },
        _focusVisible: focusRing,
      },
    },
  },

  schedulerDrawer: {
    listContainer: {
      display: 'flex',
      flexDirection: 'column' as const,
      gap: 3,
      height: '100%',
      overflowY: 'auto',
      px: 4,
      py: 2,
      css: commonStyles.scrollbar,
    },
    eventItem: {
      p: 3,
      borderRadius: radius.lg,
      bg: surface.glass1,
      border: '1px solid',
      borderColor: border.subtle,
      transition: transition.colors,
      _hover: {
        borderColor: border.default,
      },
    },
    eventHeader: {
      display: 'flex',
      alignItems: 'flex-start',
      justifyContent: 'space-between',
      gap: 3,
    },
    eventTitle: {
      ...typography.bodyStrong,
      color: text.primary,
      overflowWrap: 'anywhere' as const,
      mb: 1,
    },
    kindBadge: {
      size: 'sm' as const,
      colorPalette: 'blue',
      variant: 'subtle' as const,
    },
    prompt: {
      ...typography.footnote,
      color: text.secondary,
      mt: 3,
      whiteSpace: 'pre-wrap' as const,
      overflowWrap: 'anywhere' as const,
    },
    metadata: {
      ...typography.caption,
      color: text.tertiary,
      mt: 3,
      display: 'flex',
      flexDirection: 'column' as const,
      gap: 1,
      fontVariantNumeric: 'tabular-nums',
    },
    deleteButton: {
      variant: 'ghost' as const,
      size: 'sm' as const,
      color: status.dangerFg,
      borderRadius: radius.sm,
      flexShrink: 0,
      transition: transition.control,
      _hover: {
        bg: status.dangerBg,
      },
      _focusVisible: focusRing,
    },
    state: {
      flex: 1,
      minHeight: '240px',
      color: text.secondary,
      display: 'flex',
      flexDirection: 'column' as const,
      alignItems: 'center',
      justifyContent: 'center',
      gap: 3,
      textAlign: 'center' as const,
    },
  },

  cameraPanel: {
    container: {
      width: '100%',
      overflow: 'hidden',
      px: 2,
      minH: '184px',
    },
    header: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      mb: 2,
    },
    title: commonStyles.title,
    videoContainer: {
      ...commonStyles.panel,
      width: '100%',
      height: '170px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      overflow: 'hidden',
      transition: transition.colors,
    },
    video: {
      width: '100%',
      height: '100%',
      objectFit: 'cover' as const,
      transform: 'scaleX(-1)',
      borderRadius: '8px',
      display: 'block',
    } as const,
  },

  screenPanel: {
    container: {
      width: '100%',
      overflow: 'hidden',
      px: 2,
      minH: '184px',
    },
    header: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      mb: 2,
    },
    title: commonStyles.title,
    screenContainer: {
      ...commonStyles.panel,
      width: '100%',
      height: '170px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      overflow: 'hidden',
      transition: transition.colors,
    },
    video: {
      width: '100%',
      height: '100%',
      objectFit: 'cover' as const,
      borderRadius: '8px',
      display: 'block',
    } as const,
  },

  // Add Browser Panel Styles
  browserPanel: {
    container: {
      width: '100%',
      overflow: 'hidden',
      px: 2,
      minH: '184px',
    },
    header: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      mb: 2,
    },
    title: commonStyles.title,
    browserContainer: {
      ...commonStyles.panel,
      width: '100%',
      height: '170px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      overflow: 'hidden',
      transition: transition.control,
      cursor: 'pointer',
      _hover: {
        bg: surface.glass2,
        borderColor: border.default,
      },
    },
    iframe: {
      width: '100%',
      height: '100%',
      border: 'none',
      borderRadius: '8px',
    } as const,
  },

  bottomTab: {
    container: {
      width: '100%',
      mt: 2,
      p: 1,
      position: 'relative' as const,
      zIndex: 0,
      bg: surface.glass1,
      border: `1px solid ${border.subtle}`,
      borderRadius: radius.xl,
    },
    tabs: {
      width: '100%',
      bg: surface.glass1,
      borderRadius: radius.sm,
      p: '1',
    },
    list: {
      borderBottom: 'none',
      gap: '1',
      width: '100%',
      p: 1,
      bg: surface.glass1,
      borderRadius: radius.lg,
    },
    trigger: {
      ...typography.labelSmall,
      flex: 1,
      justifyContent: 'center',
      color: text.tertiary,
      display: 'flex',
      alignItems: 'center',
      gap: 1.5,
      px: 2,
      height: '34px',
      borderRadius: radius.sm,
      transition: transition.control,
      _hover: {
        color: text.secondary,
        bg: surface.glass2,
      },
      _selected: {
        color: text.primary,
        bg: surface.glass3,
        boxShadow: elevation[1],
      },
      _focusVisible: focusRing,
    },
  },

  groupDrawer: {
    section: {
      mb: 6,
    },
    sectionTitle: {
      ...typography.titleLarge,
      color: text.primary,
      mb: 3,
    },
    inviteBox: {
      display: 'flex',
      gap: 2,
    },
    input: {
      bg: surface.glass2,
      border: `1px solid ${border.subtle}`,
      borderRadius: radius.md,
      color: text.primary,
      transition: transition.colors,
      _placeholder: {
        color: text.quaternary,
      },
      _focusVisible: {
        borderColor: accent.ring,
        boxShadow: `0 0 0 3px ${accent.soft}`,
        outline: 'none',
      },
    },
    memberList: {
      display: 'flex',
      flexDirection: 'column',
      gap: 2,
    },
    memberItem: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      p: 2,
      borderRadius: radius.md,
      bg: surface.glass2,
      border: `1px solid ${border.subtle}`,
    },
    memberText: {
      ...typography.body,
      color: text.primary,
    },
    removeButton: {
      size: 'sm',
      color: status.dangerFg,
      bg: 'transparent',
      borderRadius: radius.sm,
      transition: transition.control,
      _hover: {
        bg: status.dangerBg,
      },
      _focusVisible: focusRing,
    },
    button: {
      ...typography.label,
      color: text.primary,
      bg: surface.glass2,
      borderRadius: radius.md,
      transition: transition.control,
      _hover: {
        bg: surface.glass3,
      },
      _focusVisible: focusRing,
    },
    clipboardButton: {
      color: text.secondary,
      bg: 'transparent',
      borderRadius: radius.sm,
      transition: transition.control,
      _hover: {
        bg: surface.glass2,
        color: text.primary,
      },
      _focusVisible: focusRing,
      size: 'sm',
    },
  },

  // Add styles for the Tool Call Indicator
  toolCallIndicator: {
    container: {
      ml: '44px',
      mr: '12px',
      my: '2',
      px: 3,
      py: 2,
      gap: 1,
      maxWidth: 'calc(100% - 56px)',
      minHeight: '42px',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'stretch',
      justifyContent: 'center',
      bg: surface.glass1,
      border: '1px solid',
      borderColor: border.subtle,
      borderRadius: radius.md,
      overflow: 'hidden',
    },
    header: {
      display: 'flex',
      alignItems: 'center',
      gap: 2,
      minWidth: 0,
    },
    icon: {
      color: accent[300],
      boxSize: '14px',
      flexShrink: 0,
    },
    title: {
      ...typography.labelSmall,
      color: text.secondary,
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap',
      minWidth: 0,
    },
    detail: {
      fontFamily: 'var(--font-mono)',
      fontSize: '11px',
      lineHeight: '1.5',
      color: text.tertiary,
      whiteSpace: 'pre-wrap',
      overflowWrap: 'anywhere',
    },
    spinner: {
      size: 'xs',
      color: accent[300],
      ml: 'auto',
    },
    completedIcon: {
      color: status.successFg,
      boxSize: '14px',
      ml: 'auto',
      flexShrink: 0,
    },
    errorIcon: {
      color: status.dangerFg,
      boxSize: '14px',
      ml: 'auto',
      flexShrink: 0,
    },
  },
};

export const chatPanelStyles = css`
  .cs-main-container,
  .cs-chat-container,
  .cs-message-list {
    min-height: 0 !important;
  }

  .cs-message-list {
    background: transparent !important;
    padding: 8px 6px 14px !important;
  }

  .cs-message {
    margin: 14px 0 !important;
  }

  .cs-message__content {
    background: var(--surface-glass-2) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: 6px 17px 17px 17px !important;
    padding: 10px 12px !important;
    color: var(--text-primary) !important;
    font-family: var(--font-text) !important;
    font-size: 0.875rem !important;
    font-weight: 400 !important;
    letter-spacing: -0.008em !important;
    line-height: 1.55 !important;
    margin-top: 6px !important;
    box-shadow: var(--elevation-1) !important;
  }

  .cs-message__text {
    padding: 0 !important;
  }

  .cs-message--outgoing .cs-message__content {
    background: linear-gradient(145deg, #6f7cf6 0%, #7d5ce5 100%) !important;
    border-color: rgba(255, 255, 255, 0.12) !important;
    border-radius: 17px 6px 17px 17px !important;
    color: #fff !important;
  }

  .cs-chat-container {
    background: transparent !important;
    border: 0 !important;
    border-radius: var(--radius-xl) !important;
    padding: 0 !important;
  }

  .cs-main-container {
    border: none !important;
    background: var(--surface-glass-1) !important;
    border-radius: var(--radius-xl) !important;
    width: 100% !important;
    margin-left: 0 !important;
    overflow: hidden !important;
  }

  .cs-message__sender {
    position: absolute !important;
    top: 0 !important;
    left: 36px !important;
    font-family: var(--font-text) !important;
    font-size: 0.6875rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em !important;
    color: var(--text-tertiary) !important;
  }

  .cs-message__content-wrapper {
    max-width: 84% !important;
    margin: 0 7px !important;
  }

  .cs-avatar {
    background: linear-gradient(145deg, #7585ff, #a45ee7) !important;
    color: #fff !important;
    width: 30px !important;
    height: 30px !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    border-radius: var(--radius-sm) !important;
    box-shadow: var(--elevation-2) !important;
  }

  .cs-message--outgoing .cs-avatar {
    background: var(--surface-glass-3) !important;
  }

  .cs-message__header {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
  }

  .cs-message-list__scroll-wrapper::-webkit-scrollbar {
    width: 8px;
  }

  .cs-message-list__scroll-wrapper::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.14);
    border: 2px solid transparent;
    background-clip: padding-box;
    border-radius: 999px;
  }

  .cs-message-list__scroll-wrapper::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.26);
    background-clip: padding-box;
  }
`;
