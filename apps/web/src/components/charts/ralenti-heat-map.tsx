'use client';

import * as React from 'react';
import L from 'leaflet';
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from 'react-leaflet';

import { HEAT_GRADIENT_CSS, HEAT_RAMP, heatStampOpacity } from '@/lib/ralenti-heat';
import { cn } from '@/lib/utils';
import type { RalentiHeatCell } from '@/lib/types';

import 'leaflet/dist/leaflet.css';

/** Con qué magnitud se pesa cada celda del mapa de calor. */
export type RalentiHeatWeight = 'minutos' | 'eventos';

interface RalentiHeatMapProps {
  title: string;
  subtitle?: string;
  cells: RalentiHeatCell[];
  weight: RalentiHeatWeight;
  onWeightChange: (weight: RalentiHeatWeight) => void;
  isLoading?: boolean;
  isError?: boolean;
  height?: number;
}

// Centro por defecto (Colombia) hasta que haya celdas para encuadrar.
const DEFAULT_CENTER: [number, number] = [4.6, -74.1];
const DEFAULT_ZOOM = 5;

/**
 * Radio y difuminado de la mancha de cada celda, en píxeles de pantalla y
 * FIJOS con el zoom, como en cualquier mapa de calor: la grilla del backend
 * es de ~110 m, así que a zoom 13 las celdas vecinas caen a ~6 px y se funden
 * en una sola masa; al acercarse se separan y cada una muestra su propio peso.
 */
const HEAT_RADIUS_PX = 18;
const HEAT_BLUR_PX = 14;

/**
 * Refuerzo de la opacidad acumulada antes de colorear. El difuminado deja
 * bordes muy tenues que sobre un mapa base claro no se distinguen; sin
 * refuerzo la corona verde/amarilla casi no se ve.
 */
const HEAT_ALPHA_BOOST = 1.3;

/** Radio del blanco invisible que da el tooltip de cada celda. */
const HOVER_MIN_RADIUS_PX = 6;
const HOVER_MAX_RADIUS_PX = 16;

const WEIGHT_META: Record<RalentiHeatWeight, { label: string; legend: string }> = {
  minutos: { label: 'Tiempo', legend: 'tiempo en ralentí' },
  eventos: { label: 'Eventos', legend: 'eventos de ralentí' },
};

function fmtInt(v: number): string {
  return Math.round(v).toLocaleString('es-CO');
}

function fmtMin(v: number): string {
  return v.toLocaleString('es-CO', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** Encuadra el mapa a las celdas cada vez que cambian. */
function FitBounds({ cells }: { cells: RalentiHeatCell[] }) {
  const map = useMap();
  React.useEffect(() => {
    if (cells.length === 0) return;
    const bounds = L.latLngBounds(cells.map((c) => [c.latitud, c.longitud] as [number, number]));
    map.fitBounds(bounds, { padding: [24, 24], maxZoom: cells.length === 1 ? 15 : 13 });
  }, [cells, map]);
  return null;
}

interface HeatPoint {
  lat: number;
  lng: number;
  /** Opacidad con la que se estampa la mancha (ver `heatStampOpacity`). */
  opacity: number;
}

/**
 * Mancha negra con borde difuminado que se estampa por cada celda. El color
 * NO va aquí: se acumula sólo la opacidad y se colorea al final, que es lo
 * que hace que las manchas se fundan en vez de taparse.
 */
function buildStamp(dpr: number): HTMLCanvasElement | null {
  const reach = Math.ceil((HEAT_RADIUS_PX + HEAT_BLUR_PX) * dpr);
  const stamp = document.createElement('canvas');
  stamp.width = reach * 2;
  stamp.height = reach * 2;
  const ctx = stamp.getContext('2d');
  if (!ctx) return null;
  // Se dibuja fuera del lienzo y se deja que sólo la sombra caiga dentro: es
  // la forma barata de obtener un disco con caída suave sin calcular píxeles.
  ctx.shadowOffsetX = reach * 2;
  ctx.shadowOffsetY = reach * 2;
  ctx.shadowBlur = HEAT_BLUR_PX * dpr;
  ctx.shadowColor = 'black';
  ctx.fillStyle = 'black';
  ctx.beginPath();
  ctx.arc(-reach, -reach, HEAT_RADIUS_PX * dpr, 0, Math.PI * 2);
  ctx.closePath();
  ctx.fill();
  return stamp;
}

/** Tabla opacidad (0..255) → RGB de la rampa, 4 bytes por entrada. */
function buildRampLookup(): Uint8ClampedArray | null {
  const canvas = document.createElement('canvas');
  canvas.width = 1;
  canvas.height = 256;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  const gradient = ctx.createLinearGradient(0, 0, 0, 256);
  for (const stop of HEAT_RAMP) {
    gradient.addColorStop(stop.at, `rgb(${stop.rgb[0]}, ${stop.rgb[1]}, ${stop.rgb[2]})`);
  }
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 1, 256);
  return ctx.getImageData(0, 0, 1, 256).data;
}

function paintHeat(
  map: L.Map,
  canvas: HTMLCanvasElement,
  points: readonly HeatPoint[],
  ramp: Uint8ClampedArray,
): void {
  const size = map.getSize();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(size.x * dpr));
  const height = Math.max(1, Math.round(size.y * dpr));
  canvas.width = width;
  canvas.height = height;
  canvas.style.width = `${size.x}px`;
  canvas.style.height = `${size.y}px`;
  // El lienzo cubre exactamente la vista: se ancla a la esquina superior
  // izquierda del contenedor expresada en coordenadas de la capa.
  L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));

  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.clearRect(0, 0, width, height);
  if (points.length === 0) return;

  const stamp = buildStamp(dpr);
  if (!stamp) return;
  const reach = stamp.width / 2;

  let painted = false;
  for (const point of points) {
    const px = map.latLngToContainerPoint([point.lat, point.lng]);
    const x = px.x * dpr;
    const y = px.y * dpr;
    if (x < -reach || y < -reach || x > width + reach || y > height + reach) continue;
    ctx.globalAlpha = point.opacity;
    ctx.drawImage(stamp, x - reach, y - reach);
    painted = true;
  }
  if (!painted) return;

  // Colorear: la opacidad acumulada de cada píxel elige el color de la rampa.
  const image = ctx.getImageData(0, 0, width, height);
  const data = image.data;
  for (let i = 0; i < data.length; i += 4) {
    const alpha = data[i + 3]!;
    if (alpha === 0) continue;
    const j = alpha * 4;
    data[i] = ramp[j]!;
    data[i + 1] = ramp[j + 1]!;
    data[i + 2] = ramp[j + 2]!;
    data[i + 3] = Math.min(255, Math.round(alpha * HEAT_ALPHA_BOOST));
  }
  ctx.putImageData(image, 0, 0);
}

/**
 * Capa de densidad en canvas sobre el `overlayPane`. Se repinta al terminar
 * cada movimiento o zoom; durante la animación de zoom Leaflet la oculta
 * (`leaflet-zoom-hide`), que es más barato y más honesto que estirar el
 * bitmap. No captura eventos: los tooltips los dan los blancos invisibles.
 */
function HeatCanvasLayer({ points }: { points: readonly HeatPoint[] }) {
  const map = useMap();
  React.useEffect(() => {
    const pane = map.getPane('overlayPane');
    if (!pane) return;
    const ramp = buildRampLookup();
    if (!ramp) return;
    const canvas = L.DomUtil.create('canvas', 'leaflet-zoom-hide', pane);
    canvas.style.pointerEvents = 'none';
    const draw = () => paintHeat(map, canvas, points, ramp);
    draw();
    map.on('moveend viewreset resize', draw);
    return () => {
      map.off('moveend viewreset resize', draw);
      canvas.remove();
    };
  }, [map, points]);
  return null;
}

/**
 * Mapa de calor de ralentí sobre la grilla que agrega el backend. Es un mapa
 * de DENSIDAD: cada celda estampa una mancha con opacidad proporcional a su
 * peso, las manchas vecinas se suman y el color sale del total acumulado
 * (verde → amarillo → naranja → rojo). Se construye con canvas y primitivas
 * de react-leaflet: no hay dependencia de `leaflet.heat`.
 */
export function RalentiHeatMap({
  title,
  subtitle,
  cells,
  weight,
  onWeightChange,
  isLoading,
  isError,
  height = 460,
}: RalentiHeatMapProps) {
  const { points, hovers } = React.useMemo(() => {
    const valueOf = (c: RalentiHeatCell) => (weight === 'minutos' ? c.minutos : c.eventos);
    const max = cells.reduce((acc, c) => Math.max(acc, valueOf(c)), 0);
    const sqrtMax = Math.sqrt(max) || 1;
    const heat: HeatPoint[] = [];
    const targets: { cell: RalentiHeatCell; radius: number; value: number }[] = [];
    for (const cell of cells) {
      const value = valueOf(cell);
      heat.push({ lat: cell.latitud, lng: cell.longitud, opacity: heatStampOpacity(value, max) });
      targets.push({
        cell,
        value,
        radius:
          HOVER_MIN_RADIUS_PX +
          (HOVER_MAX_RADIUS_PX - HOVER_MIN_RADIUS_PX) * (Math.sqrt(value) / sqrtMax),
      });
    }
    // Los blancos grandes van debajo para que los pequeños sigan recibiendo
    // el puntero cuando se superponen.
    targets.sort((a, b) => b.value - a.value);
    return { points: heat, hovers: targets };
  }, [cells, weight]);

  const isEmpty = !isLoading && !isError && cells.length === 0;

  return (
    <div className="bg-card relative isolate z-0 rounded-lg border p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-heading text-sm font-bold tracking-tight">{title}</h3>
          {subtitle && <p className="text-muted-foreground text-xs">{subtitle}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div
            role="group"
            aria-label="Ponderar el mapa por"
            className="bg-muted inline-flex h-8 items-center rounded-md p-0.5"
          >
            <span className="text-muted-foreground px-2 text-xs">Ponderar por:</span>
            {(Object.keys(WEIGHT_META) as RalentiHeatWeight[]).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => onWeightChange(option)}
                aria-pressed={weight === option}
                className={cn(
                  'rounded px-2.5 py-1 text-xs font-medium transition',
                  weight === option ? 'bg-card shadow-sm' : 'text-muted-foreground',
                )}
              >
                {WEIGHT_META[option].label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1.5 text-xs" aria-hidden>
            <span className="text-muted-foreground">menos</span>
            <span
              className="inline-block h-2.5 w-24 rounded-full"
              style={{ background: HEAT_GRADIENT_CSS }}
            />
            <span className="text-muted-foreground">más {WEIGHT_META[weight].legend}</span>
          </div>
        </div>
      </div>

      <div style={{ height }} className="relative z-0 overflow-hidden rounded-md">
        {isLoading ? (
          <div className="bg-muted h-full w-full animate-pulse" />
        ) : isError ? (
          <div className="text-destructive flex h-full items-center justify-center text-sm">
            No se pudo cargar el mapa de calor.
          </div>
        ) : isEmpty ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            Sin episodios georreferenciados para los filtros seleccionados.
          </div>
        ) : (
          <MapContainer
            center={DEFAULT_CENTER}
            zoom={DEFAULT_ZOOM}
            scrollWheelZoom
            className="z-0"
            style={{ height: '100%', width: '100%' }}
          >
            {/* El mapa base se desatura (ver `.ralenti-heat-tiles` en globals.css)
                para que el calor sea lo único con color. */}
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              className="ralenti-heat-tiles"
            />
            <FitBounds cells={cells} />
            <HeatCanvasLayer points={points} />
            {hovers.map(({ cell, radius }) => (
              <CircleMarker
                key={`${cell.latitud},${cell.longitud}`}
                center={[cell.latitud, cell.longitud]}
                radius={radius}
                pathOptions={{ stroke: false, fillOpacity: 0 }}
              >
                <Tooltip direction="top">
                  {fmtInt(cell.eventos)} {cell.eventos === 1 ? 'evento' : 'eventos'} ·{' '}
                  {fmtMin(cell.minutos)} min
                </Tooltip>
              </CircleMarker>
            ))}
          </MapContainer>
        )}
      </div>
    </div>
  );
}
