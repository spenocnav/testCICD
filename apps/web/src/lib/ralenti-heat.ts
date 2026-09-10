/**
 * Rampa e intensidad del mapa de calor de ralentí. Va aparte del componente
 * para poder probarse sin DOM: el componente sólo pinta.
 *
 * El mapa NO colorea cada celda por su propio peso. Cada celda aporta una
 * mancha con opacidad proporcional a su intensidad y las manchas se SUMAN en
 * el canvas; el color sale de la opacidad acumulada. Así una zona con muchas
 * celdas medianas se lee igual de roja que una celda enorme, y una celda
 * aislada nunca pasa de verde/amarillo aunque sea la mayor del alcance.
 */

export interface HeatStop {
  /** Posición en la rampa, 0..1, sobre la opacidad acumulada. */
  at: number;
  rgb: readonly [number, number, number];
}

/** Verde en el borde de la mancha → amarillo → naranja → rojo en el núcleo. */
export const HEAT_RAMP: readonly HeatStop[] = [
  { at: 0.0, rgb: [22, 163, 74] },
  { at: 0.3, rgb: [132, 204, 22] },
  { at: 0.5, rgb: [250, 204, 21] },
  { at: 0.72, rgb: [249, 115, 22] },
  { at: 1.0, rgb: [220, 38, 38] },
];

/**
 * Opacidad mínima de una mancha. Sin ella, la celda más liviana (intensidad
 * casi 0) desaparecería del mapa y con ella el episodio que representa.
 */
export const HEAT_MIN_OPACITY = 0.12;

function mix(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

/** Color de la rampa para `t` en 0..1 (fuera de rango se acota). */
export function heatRampColor(t: number): string {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0));
  let from = HEAT_RAMP[0]!;
  let to = HEAT_RAMP[HEAT_RAMP.length - 1]!;
  for (let i = 0; i < HEAT_RAMP.length - 1; i += 1) {
    const a = HEAT_RAMP[i]!;
    const b = HEAT_RAMP[i + 1]!;
    if (clamped >= a.at && clamped <= b.at) {
      from = a;
      to = b;
      break;
    }
  }
  const span = to.at - from.at;
  const local = span > 0 ? (clamped - from.at) / span : 0;
  return `rgb(${mix(from.rgb[0], to.rgb[0], local)}, ${mix(from.rgb[1], to.rgb[1], local)}, ${mix(
    from.rgb[2],
    to.rgb[2],
    local,
  )})`;
}

/** La misma rampa como degradado CSS, para la leyenda. */
export const HEAT_GRADIENT_CSS = `linear-gradient(to right, ${HEAT_RAMP.map(
  (stop) => `rgb(${stop.rgb[0]}, ${stop.rgb[1]}, ${stop.rgb[2]}) ${Math.round(stop.at * 100)}%`,
).join(', ')})`;

/**
 * Intensidad 0..1 de una celda relativa al máximo visible, en raíz cuadrada.
 * Lineal, una planta con 1.000 minutos apagaría todo lo demás: el resto de
 * la flota quedaría en el mínimo y el mapa mostraría un solo punto. La raíz
 * comprime esa cola sin cambiar el orden. Con `max <= 0` no hay nada que
 * comparar y todo vale 0.
 */
export function heatIntensity(value: number, max: number): number {
  if (!(max > 0) || !(value > 0)) return 0;
  return Math.min(1, Math.sqrt(value / max));
}

/**
 * Opacidad con la que se estampa una mancha: la intensidad con piso. Es lo
 * que se acumula en el canvas y, por tanto, lo que decide el color final.
 */
export function heatStampOpacity(value: number, max: number): number {
  return Math.max(HEAT_MIN_OPACITY, heatIntensity(value, max));
}
