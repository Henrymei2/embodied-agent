/**
 * Typed accessors for the design tokens declared in `index.css`.
 *
 * Values are `var(--…)` references rather than literals so the stylesheet
 * stays the single source of truth — retheming means editing one file, not
 * hunting hard-coded rgba() through the component tree.
 *
 * This module is presentation only. It holds no state and imports nothing.
 */

export const font = {
  text: 'var(--font-text)',
  display: 'var(--font-display)',
  mono: 'var(--font-mono)',
} as const;

export const surface = {
  canvas: 'var(--surface-canvas)',
  sunken: 'var(--surface-sunken)',
  raised: 'var(--surface-raised)',
  overlay: 'var(--surface-overlay)',
  glass1: 'var(--surface-glass-1)',
  glass2: 'var(--surface-glass-2)',
  glass3: 'var(--surface-glass-3)',
  scrim: 'var(--surface-scrim)',
  chrome: 'var(--surface-chrome)',
  chromeSolid: 'var(--surface-chrome-solid)',
} as const;

export const text = {
  primary: 'var(--text-primary)',
  secondary: 'var(--text-secondary)',
  tertiary: 'var(--text-tertiary)',
  quaternary: 'var(--text-quaternary)',
  inverse: 'var(--text-inverse)',
} as const;

export const border = {
  subtle: 'var(--border-subtle)',
  default: 'var(--border-default)',
  strong: 'var(--border-strong)',
} as const;

export const accent = {
  50: 'var(--accent-50)',
  200: 'var(--accent-200)',
  300: 'var(--accent-300)',
  400: 'var(--accent-400)',
  500: 'var(--accent-500)',
  600: 'var(--accent-600)',
  contrast: 'var(--accent-contrast)',
  onSolid: 'var(--accent-on-solid)',
  soft: 'var(--accent-soft)',
  softHover: 'var(--accent-soft-hover)',
  ring: 'var(--accent-ring)',
  glow: 'var(--accent-glow)',
} as const;

export const status = {
  successFg: 'var(--success-fg)',
  successBg: 'var(--success-bg)',
  successBorder: 'var(--success-border)',
  warningFg: 'var(--warning-fg)',
  warningBg: 'var(--warning-bg)',
  warningBorder: 'var(--warning-border)',
  dangerFg: 'var(--danger-fg)',
  dangerBg: 'var(--danger-bg)',
  dangerBorder: 'var(--danger-border)',
} as const;

export const radius = {
  xs: 'var(--radius-xs)',
  sm: 'var(--radius-sm)',
  md: 'var(--radius-md)',
  lg: 'var(--radius-lg)',
  xl: 'var(--radius-xl)',
  '2xl': 'var(--radius-2xl)',
  '3xl': 'var(--radius-3xl)',
  full: 'var(--radius-full)',
} as const;

export const elevation = {
  1: 'var(--elevation-1)',
  2: 'var(--elevation-2)',
  3: 'var(--elevation-3)',
  4: 'var(--elevation-4)',
  5: 'var(--elevation-5)',
  hairline: 'var(--elevation-inset-hairline)',
} as const;

export const duration = {
  instant: 'var(--duration-instant)',
  fast: 'var(--duration-fast)',
  normal: 'var(--duration-normal)',
  slow: 'var(--duration-slow)',
  slower: 'var(--duration-slower)',
} as const;

export const ease = {
  out: 'var(--ease-out)',
  in: 'var(--ease-in)',
  inOut: 'var(--ease-in-out)',
  spring: 'var(--ease-spring)',
} as const;

/** Composed transitions. Prefer these over ad-hoc `all 0.3s ease`. */
export const transition = {
  /** Hover/press feedback on controls. */
  control: `background-color ${duration.fast} ${ease.out}, border-color ${duration.fast} ${ease.out}, color ${duration.fast} ${ease.out}, box-shadow ${duration.fast} ${ease.out}, transform ${duration.fast} ${ease.out}`,
  /** Colour-only changes. */
  colors: `background-color ${duration.fast} ${ease.out}, border-color ${duration.fast} ${ease.out}, color ${duration.fast} ${ease.out}`,
  /** Panels, drawers, and anything that changes the layout. */
  layout: `all ${duration.slow} ${ease.spring}`,
  /** Transforms only — cheapest thing to animate. */
  transform: `transform ${duration.slow} ${ease.spring}`,
  opacity: `opacity ${duration.normal} ${ease.out}`,
} as const;

/**
 * Type ramp. Larger text gets progressively tighter tracking, matching how
 * optical sizing works in SF and Segoe UI Variable.
 *
 * Weights stay on 400/500/600/700 so they resolve to real cuts on every
 * platform — `font-synthesis: none` means an unavailable weight like 650
 * silently snaps to a neighbour instead of rendering as designed.
 */
export const typography = {
  /** Section headers: SETTINGS, CONVERSATION. */
  overline: {
    fontSize: '11px',
    fontWeight: '600',
    letterSpacing: '0.07em',
    textTransform: 'uppercase',
    lineHeight: '1.2',
  },
  caption: {
    fontSize: '11px',
    fontWeight: '500',
    letterSpacing: '0',
    lineHeight: '1.4',
  },
  footnote: {
    fontSize: '12px',
    fontWeight: '400',
    letterSpacing: '-0.003em',
    lineHeight: '1.45',
  },
  body: {
    fontSize: '14px',
    fontWeight: '400',
    letterSpacing: '-0.008em',
    lineHeight: '1.55',
  },
  bodyStrong: {
    fontSize: '14px',
    fontWeight: '600',
    letterSpacing: '-0.008em',
    lineHeight: '1.5',
  },
  /** Control labels — buttons, tabs, chips. */
  label: {
    fontSize: '13px',
    fontWeight: '600',
    letterSpacing: '-0.006em',
    lineHeight: '1.2',
  },
  labelSmall: {
    fontSize: '11px',
    fontWeight: '600',
    letterSpacing: '0.01em',
    lineHeight: '1.2',
  },
  title: {
    fontFamily: font.display,
    fontSize: '15px',
    fontWeight: '600',
    letterSpacing: '-0.014em',
    lineHeight: '1.35',
  },
  titleLarge: {
    fontFamily: font.display,
    fontSize: '18px',
    fontWeight: '600',
    letterSpacing: '-0.019em',
    lineHeight: '1.3',
  },
  display: {
    fontFamily: font.display,
    fontSize: '22px',
    fontWeight: '700',
    letterSpacing: '-0.024em',
    lineHeight: '1.22',
  },
} as const;

/** Keyboard focus ring. Applied via `_focusVisible` so pointer use stays clean. */
export const focusRing = {
  outline: 'var(--focus-ring-width) solid var(--focus-ring-color)',
  outlineOffset: 'var(--focus-ring-offset)',
} as const;

/** Frosted chrome floating above the stage. */
export const glass = {
  bg: surface.chrome,
  backdropFilter: 'blur(28px) saturate(140%)',
  border: `1px solid ${border.default}`,
} as const;

export const tokens = {
  font,
  surface,
  text,
  border,
  accent,
  status,
  radius,
  elevation,
  duration,
  ease,
  transition,
  typography,
  focusRing,
  glass,
} as const;

export default tokens;
