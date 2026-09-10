import { describe, expect, it } from 'vitest';

import {
  HEAT_GRADIENT_CSS,
  HEAT_MIN_OPACITY,
  HEAT_RAMP,
  heatIntensity,
  heatRampColor,
  heatStampOpacity,
} from './ralenti-heat';

describe('heatRampColor', () => {
  it('va de verde en el borde a rojo en el núcleo', () => {
    expect(heatRampColor(0)).toBe('rgb(22, 163, 74)');
    expect(heatRampColor(1)).toBe('rgb(220, 38, 38)');
  });

  it('pasa por amarillo y naranja en el medio, en ese orden', () => {
    const yellow = HEAT_RAMP.find((s) => s.at === 0.5)!;
    const orange = HEAT_RAMP.find((s) => s.at === 0.72)!;
    expect(heatRampColor(0.5)).toBe(`rgb(${yellow.rgb.join(', ')})`);
    expect(heatRampColor(0.72)).toBe(`rgb(${orange.rgb.join(', ')})`);
  });

  it('acota fuera de 0..1 y tolera NaN', () => {
    expect(heatRampColor(-3)).toBe(heatRampColor(0));
    expect(heatRampColor(7)).toBe(heatRampColor(1));
    expect(heatRampColor(Number.NaN)).toBe(heatRampColor(0));
  });

  it('la leyenda CSS lleva las mismas paradas en el mismo orden', () => {
    expect(HEAT_GRADIENT_CSS.startsWith('linear-gradient(to right, rgb(22, 163, 74) 0%')).toBe(
      true,
    );
    expect(HEAT_GRADIENT_CSS.endsWith('rgb(220, 38, 38) 100%)')).toBe(true);
  });
});

describe('heatIntensity', () => {
  it('es la raíz del peso relativo al máximo', () => {
    expect(heatIntensity(100, 100)).toBe(1);
    expect(heatIntensity(25, 100)).toBe(0.5);
    expect(heatIntensity(1, 100)).toBe(0.1);
  });

  it('comprime la cola: una celda diez veces menor no queda diez veces más tenue', () => {
    expect(heatIntensity(10, 100)).toBeGreaterThan(0.3);
  });

  it('sin máximo ni peso no hay intensidad', () => {
    expect(heatIntensity(5, 0)).toBe(0);
    expect(heatIntensity(0, 100)).toBe(0);
    expect(heatIntensity(-2, 100)).toBe(0);
  });
});

describe('heatStampOpacity', () => {
  it('ninguna celda con episodios baja del piso visible', () => {
    expect(heatStampOpacity(1, 1_000_000)).toBe(HEAT_MIN_OPACITY);
    expect(heatStampOpacity(0, 100)).toBe(HEAT_MIN_OPACITY);
  });

  it('la celda máxima estampa opaca', () => {
    expect(heatStampOpacity(40, 40)).toBe(1);
  });
});
