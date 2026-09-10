'use client';

import * as React from 'react';
import L from 'leaflet';
import { CircleMarker, MapContainer, Popup, TileLayer, Tooltip, useMap } from 'react-leaflet';

import { CHART_COLORS, SERIES_PALETTE } from '@/components/charts/chart-theme';

import 'leaflet/dist/leaflet.css';

export interface MapPoint {
  event_sk: string;
  placa: string | null;
  event_type: string | null;
  fecha_y_hora_del_evento: string | null;
  duracion_evento: number | null;
  observacion_corta: string | null;
  latitud: number;
  longitud: number;
}

interface EventMapCardProps {
  title: string;
  subtitle?: string;
  points: MapPoint[];
  isLoading?: boolean;
  height?: number;
}

// Centro por defecto (Colombia) hasta que haya puntos para encuadrar.
const DEFAULT_CENTER: [number, number] = [4.6, -74.1];
const DEFAULT_ZOOM = 5;

/** Encuadra el mapa a los puntos cada vez que cambian. */
function FitBounds({ points }: { points: MapPoint[] }) {
  const map = useMap();
  React.useEffect(() => {
    if (points.length === 0) return;
    const bounds = L.latLngBounds(points.map((p) => [p.latitud, p.longitud] as [number, number]));
    map.fitBounds(bounds, { padding: [24, 24], maxZoom: points.length === 1 ? 17 : 14 });
  }, [points, map]);
  return null;
}

/** Mapa de eventos georreferenciados; color por tipo de evento. */
export function EventMapCard({
  title,
  subtitle,
  points,
  isLoading,
  height = 460,
}: EventMapCardProps) {
  // Color estable por tipo de evento (mismo orden que la paleta de series).
  const colorByType = React.useMemo(() => {
    const types = Array.from(new Set(points.map((p) => p.event_type ?? '—')));
    const map: Record<string, string> = {};
    types.forEach((t, i) => {
      map[t] = SERIES_PALETTE[i % SERIES_PALETTE.length] ?? CHART_COLORS.blue;
    });
    return map;
  }, [points]);

  const types = Object.keys(colorByType);
  const isEmpty = !isLoading && points.length === 0;

  return (
    <div className="bg-card relative isolate z-0 rounded-lg border p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-heading text-sm font-bold tracking-tight">{title}</h3>
          {subtitle && <p className="text-muted-foreground text-xs">{subtitle}</p>}
        </div>
        {types.length > 0 && (
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {types.map((t) => (
              <span key={t} className="flex items-center gap-1 text-xs">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: colorByType[t] }}
                />
                {t}
              </span>
            ))}
          </div>
        )}
      </div>

      <div style={{ height }} className="relative z-0 overflow-hidden rounded-md">
        {isLoading ? (
          <div className="bg-muted h-full w-full animate-pulse" />
        ) : isEmpty ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            Sin eventos georreferenciados para los filtros seleccionados.
          </div>
        ) : (
          <MapContainer
            center={DEFAULT_CENTER}
            zoom={DEFAULT_ZOOM}
            scrollWheelZoom
            className="z-0"
            style={{ height: '100%', width: '100%' }}
          >
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <FitBounds points={points} />
            {points.map((p) => (
              <CircleMarker
                key={p.event_sk}
                center={[p.latitud, p.longitud]}
                radius={5}
                pathOptions={{
                  color: colorByType[p.event_type ?? '—'],
                  fillColor: colorByType[p.event_type ?? '—'],
                  fillOpacity: 0.6,
                  weight: 1,
                }}
              >
                <Tooltip direction="top">{p.placa ?? '—'}</Tooltip>
                <Popup>
                  <div className="space-y-0.5 text-xs">
                    <p className="font-semibold">{p.placa ?? '—'}</p>
                    <p>{p.event_type ?? '—'}</p>
                    {p.observacion_corta && (
                      <p>
                        <span className="font-medium">Velocidad:</span>{' '}
                        {p.observacion_corta.split(',')[0]}
                      </p>
                    )}
                    {p.fecha_y_hora_del_evento && (
                      <p className="text-muted-foreground">
                        {new Date(p.fecha_y_hora_del_evento).toLocaleString('es-CO')}
                      </p>
                    )}
                    {p.duracion_evento != null && <p>{p.duracion_evento.toFixed(1)} s</p>}
                  </div>
                </Popup>
              </CircleMarker>
            ))}
          </MapContainer>
        )}
      </div>
    </div>
  );
}
