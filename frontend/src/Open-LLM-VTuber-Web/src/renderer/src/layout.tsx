import {
  border,
  elevation,
  radius,
  surface,
  text,
  transition,
  typography,
} from './styles/tokens';

const isElectron = window.api !== undefined;

const getAppHeight = () => {
  if (typeof window !== 'undefined' && /Mobi|Android/i.test(navigator.userAgent)) {
    return `${window.innerHeight}px`;
  }
  return isElectron ? 'calc(100vh - 30px)' : '100vh';
};

const TITLE_BAR_HEIGHT = '30px';

export const layoutStyles = {
  appContainer: {
    width: '100vw',
    height: getAppHeight(),
    bg: surface.canvas,
    color: text.primary,
    overflow: 'hidden',
    position: 'relative',
    display: 'flex',
    flexDirection: 'row',
    mt: isElectron ? TITLE_BAR_HEIGHT : '0',
  },
  sidebar: {
    position: { base: 'absolute', md: 'relative' } as const,
    left: 0,
    top: 0,
    height: '100%',
    bg: 'transparent',
    overflow: 'visible',
    flexShrink: 0,
    // Width alone, so the sidebar's own transform animation stays independent.
    transition: `width var(--duration-slow) var(--ease-spring)`,
    zIndex: 30,
  },
  mainContent: {
    flex: 1,
    minWidth: 0,
    height: '100%',
    position: 'relative',
    display: 'flex',
    flexDirection: 'column',
    transition: transition.layout,
    width: '100%',
    overflow: 'hidden',
    isolation: 'isolate',
  },
  stageArea: {
    position: 'relative',
    width: '100%',
    flex: 1,
    minHeight: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    px: { base: 3, sm: 5, lg: 7 },
    pb: { base: 2, md: 3 },
    transition: transition.layout,
    overflow: 'hidden',
  },
  stageFrame: {
    position: 'relative',
    height: 'auto',
    width: 'min(100%, calc((100vh - 216px) * 16 / 9))',
    maxWidth: '100%',
    maxHeight: '100%',
    aspectRatio: '16 / 9',
    overflow: 'hidden',
    borderRadius: { base: radius.xl, md: radius['3xl'] },
    border: `1px solid ${border.default}`,
    bg: surface.overlay,
    boxShadow: `${elevation[5]}, ${elevation.hairline}`,
    css: {
      '@media (max-height: 680px)': {
        width: 'min(100%, calc((100vh - 176px) * 16 / 9))',
      },
    },
  },
  footer: {
    width: '100%',
    height: { base: '104px', md: '112px' },
    px: { base: 3, sm: 5, lg: 7 },
    transition: transition.layout,
    willChange: 'transform',
    position: 'relative',
    zIndex: 20,
  },
  collapsedFooter: {
    height: '34px',
    transition: transition.layout,
  },
  windowsTitleBar: {
    position: 'fixed',
    top: 0,
    left: 0,
    width: '100vw',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    height: TITLE_BAR_HEIGHT,
    backgroundColor: surface.raised,
    borderBottom: `1px solid ${border.subtle}`,
    paddingX: '12px',
    zIndex: 1000,
    css: { '-webkit-app-region': 'drag' },
  },
  macTitleBar: {
    position: 'fixed',
    top: 0,
    left: 0,
    width: '100vw',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: TITLE_BAR_HEIGHT,
    backgroundColor: surface.raised,
    borderBottom: `1px solid ${border.subtle}`,
    zIndex: 1000,
    css: {
      '-webkit-app-region': 'drag',
      '-webkit-user-select': 'none',
    },
  },
  titleBarTitle: {
    ...typography.labelSmall,
    color: text.secondary,
    textAlign: 'center',
  },
  titleBarButtons: {
    display: 'flex',
    gap: '1',
  },
  titleBarButton: {
    size: 'sm',
    variant: 'ghost',
    color: text.secondary,
    borderRadius: radius.xs,
    transition: transition.colors,
    css: { '-webkit-app-region': 'no-drag' },
    _hover: { backgroundColor: surface.glass2, color: text.primary },
  },
  closeButton: {
    size: 'sm',
    variant: 'ghost',
    color: text.secondary,
    borderRadius: radius.xs,
    transition: transition.colors,
    css: { '-webkit-app-region': 'no-drag' },
    _hover: { backgroundColor: '#e5484d', color: '#fff' },
  },
} as const;
