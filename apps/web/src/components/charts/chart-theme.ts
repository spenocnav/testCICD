/**
 * Paleta y tokens compartidos para los gráficos recharts del portal.
 * Usa los colores de marca (ver styles/globals.css) para evitar la estética
 * genérica por defecto de recharts.
 */

export const CHART_COLORS = {
  red: '#ee2e2f',
  blue: '#185979',
  lime: '#b2e100',
  yellow: '#ffb301',
  gray: '#354550',
  cream: '#eff0c8',
} as const;

/**
 * Variaciones de la paleta oficial para series sobre superficies claras.
 * Reducen la saturación de marca o aumentan el contraste sin introducir
 * familias de color ajenas a Navi.
 */
export const CHART_TONES = {
  redMuted: '#c95354',
  blueMuted: '#527f93',
  limeDark: '#718f00',
  yellowDark: '#c88700',
  grayMuted: '#738088',
  creamDark: '#9b945b',
  pinkLight: '#f3c8bd',
} as const;

// Secuencia estable para series múltiples / barras por categoría.
export const SERIES_PALETTE = [
  CHART_COLORS.blue,
  CHART_TONES.yellowDark,
  CHART_TONES.grayMuted,
  CHART_TONES.limeDark,
  CHART_TONES.redMuted,
  CHART_TONES.creamDark,
];

export const AXIS_COLOR = '#9aa6ad';
export const GRID_COLOR = '#e2e7e4';

export const EXTREME_COLORS = {
  min: CHART_COLORS.red,
  max: CHART_TONES.limeDark,
} as const;

/** Movimiento unificado para las series animadas de Recharts. */
export const CHART_ANIMATION_DURATION = 500;
export const CHART_ANIMATION_EASING = 'cubic-bezier(0.4, 0, 0.2, 1)';

export const tooltipStyle = {
  borderRadius: 8,
  border: '1px solid var(--border)',
  background: 'var(--card)',
  fontSize: 12,
  boxShadow: 'var(--shadow-soft)',
} as const;
