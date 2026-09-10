'use client';

import * as React from 'react';
import Link from 'next/link';
import { Bell, CarFront, ChevronRight, ClipboardList, FileBarChart, Gauge } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { BarChartCard } from '@/components/charts/bar-chart-card';
import { ChartCard } from '@/components/charts/chart-card';
import { CHART_COLORS, CHART_TONES } from '@/components/charts/chart-theme';
import { useFleetFilter } from '@/components/fleet/fleet-provider';
import { PageTitle } from '@/components/layout/page-title';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useHasPermission, useMe } from '@/lib/auth';
import { useDisponibilidad, useOrdenes } from '@/lib/mantenimiento';
import { useNovedades } from '@/lib/novedades';
import { useCombustibleSummary, useVehicleRanking, useVehiculos } from '@/lib/reportes';
import { useAccessibleVehiclesPaginated } from '@/lib/vehicles';

interface QuickLink {
  href: string;
  label: string;
  description: string;
  permission: string;
  icon: LucideIcon;
}

const QUICK_LINKS: QuickLink[] = [
  {
    href: '/reportes',
    label: 'Reportes',
    description: 'Indicadores de operación, combustible y rendimiento.',
    permission: 'reportes.view',
    icon: FileBarChart,
  },
  {
    href: '/vehiculos',
    label: 'Vehículos',
    description: 'Consulta los vehículos de tus flotas.',
    permission: 'reportes.view',
    icon: CarFront,
  },
  {
    href: '/mantenimiento/informe',
    label: 'Informe de mantenimiento',
    description: 'Disponibilidad, confiabilidad y próximas intervenciones.',
    permission: 'mantenimiento.view',
    icon: ClipboardList,
  },
  {
    href: '/novedades',
    label: 'Novedades',
    description: 'Seguimiento de novedades y evidencias.',
    permission: 'novedades.view',
    icon: Bell,
  },
];

const BOGOTA_TIME_ZONE = 'America/Bogota';

const GREETING_VARIANTS = {
  morning: ['Buenos días', 'Buenos días', 'Hola', 'Qué gusto verte'],
  afternoon: ['Buenas tardes', 'Buenas tardes', 'Hola', 'Qué gusto verte'],
  night: ['Buenas noches', 'Buenas noches', 'Hola', 'Bienvenido de nuevo'],
} as const;

type GreetingPeriod = keyof typeof GREETING_VARIANTS;

function getGreeting(now: Date, userKey: string): string {
  const hour = Number(
    new Intl.DateTimeFormat('en-US', {
      timeZone: BOGOTA_TIME_ZONE,
      hour: 'numeric',
      hour12: false,
    }).format(now),
  );
  const period: GreetingPeriod = hour < 12 ? 'morning' : hour < 19 ? 'afternoon' : 'night';
  const localDate = new Intl.DateTimeFormat('en-CA', {
    timeZone: BOGOTA_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now);
  const seed = `${userKey}:${localDate}:${period}`;
  let hash = 0;

  for (const character of seed) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  }

  const variants = GREETING_VARIANTS[period];
  return variants[hash % variants.length] ?? 'Hola';
}

function getCurrentMonthRange(now = new Date()): { dateFrom: string; dateTo: string } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: BOGOTA_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const year = values.year ?? String(now.getUTCFullYear());
  const month = values.month ?? String(now.getUTCMonth() + 1).padStart(2, '0');
  const day = values.day ?? String(now.getUTCDate()).padStart(2, '0');

  return { dateFrom: `${year}-${month}-01`, dateTo: `${year}-${month}-${day}` };
}

function getMonthLabel(now = new Date()): string {
  return new Intl.DateTimeFormat('es-CO', {
    timeZone: BOGOTA_TIME_ZONE,
    month: 'long',
    year: 'numeric',
  }).format(now);
}

function formatNumber(value: number | null | undefined, maximumFractionDigits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('es-CO', { maximumFractionDigits }).format(value);
}

function formatPercent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${formatNumber(value)}%`;
}

function formatOtStatus(status: string | null): string {
  if (status === 'opened') return 'Abierta';
  if (status === 'onTechnicalCompletion') return 'Cierre técnico';
  return status || 'Sin estado';
}

function KpiCard({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  detail: string;
}) {
  return (
    <article className="rounded-lg border bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
          {label}
        </p>
        <span className="bg-accent-blue/10 text-accent-blue rounded-md p-2">
          <Icon className="h-4 w-4" aria-hidden />
        </span>
      </div>
      <p className="mt-3 text-3xl font-extrabold">{value}</p>
      <p className="text-muted-foreground mt-1 text-xs">{detail}</p>
    </article>
  );
}

function performanceValue(
  summary: { km_gal: number | null; km_m3: number | null; n_registros: number } | undefined,
  fuelKind: 'liquid' | 'gas',
): string | null {
  if (!summary || summary.n_registros === 0) return null;
  const value = fuelKind === 'gas' ? summary.km_m3 : summary.km_gal;
  if (value == null || !Number.isFinite(value)) return null;
  return `${formatNumber(value, 2)} ${fuelKind === 'gas' ? 'km/m³' : 'km/gal'}`;
}

function rankingRows(
  rows:
    | Array<{
        vehicle_id: string | null;
        vehicle_label: string | null;
        placa: string | null;
        value: number | null;
      }>
    | undefined,
) {
  return (rows ?? [])
    .filter((row) => row.value != null && Number.isFinite(row.value) && row.value > 0)
    .slice(0, 5)
    .map((row) => ({
      id: row.vehicle_id ?? row.placa ?? row.vehicle_label ?? 'vehiculo',
      plate: row.placa ?? row.vehicle_label ?? 'Sin placa',
      value: row.value as number,
    }));
}

function formatOpenDuration(hours: number): string {
  if (hours < 24) return `${formatNumber(hours, 0)} h`;
  const days = Math.floor(hours / 24);
  const remainingHours = Math.round(hours % 24);
  return remainingHours > 0 ? `${days} d ${remainingHours} h` : `${days} d`;
}

export default function InicioPage() {
  const me = useMe();
  const hasPermission = useHasPermission();
  const fleetFilter = useFleetFilter();
  const period = React.useMemo(() => getCurrentMonthRange(), []);
  const monthLabel = React.useMemo(() => getMonthLabel(), []);

  const canViewReports = hasPermission('reportes.view');
  const canViewMantenimiento = hasPermission('mantenimiento.view');
  const canViewNovedades = hasPermission('novedades.view');
  const canViewVehicles = canViewReports || canViewNovedades;

  const vehicles = useAccessibleVehiclesPaginated({ limit: 1, offset: 0 }, canViewVehicles);
  const analyticsVehicles = useVehiculos(canViewReports);
  const novedades = useNovedades(
    { date_from: period.dateFrom, date_to: period.dateTo, limit: 1, offset: 0 },
    canViewNovedades,
  );
  const disponibilidad = useDisponibilidad(
    { date_from: period.dateFrom, date_to: period.dateTo },
    5,
    canViewMantenimiento,
  );
  const ordenes = useOrdenes(
    { date_from: period.dateFrom, date_to: period.dateTo },
    canViewMantenimiento,
  );

  const activeAnalyticsVehicles = analyticsVehicles.data?.filter((vehicle) => vehicle.is_active);
  const hasLiquidVehicles =
    activeAnalyticsVehicles?.some((vehicle) => vehicle.fuel_kind === 'liquid') ?? false;
  const hasGasVehicles =
    activeAnalyticsVehicles?.some((vehicle) => vehicle.fuel_kind === 'gas') ?? false;
  const performanceFilters = { date_from: period.dateFrom, date_to: period.dateTo };
  const liquidPerformance = useCombustibleSummary(
    { ...performanceFilters, fuel_kind: 'liquid' },
    canViewReports && hasLiquidVehicles,
  );
  const gasPerformance = useCombustibleSummary(
    { ...performanceFilters, fuel_kind: 'gas' },
    canViewReports && hasGasVehicles,
  );
  const liquidRanking = useVehicleRanking(
    { ...performanceFilters, fuel_kind: 'liquid' },
    'km_gal',
    20,
    'asc',
    canViewReports && hasLiquidVehicles,
  );
  const gasRanking = useVehicleRanking(
    { ...performanceFilters, fuel_kind: 'gas' },
    'km_m3',
    20,
    'asc',
    canViewReports && hasGasVehicles,
  );
  const singleFuelKind =
    hasLiquidVehicles === hasGasVehicles ? null : hasLiquidVehicles ? 'liquid' : 'gas';
  const distanceRanking = useVehicleRanking(
    { ...performanceFilters, fuel_kind: singleFuelKind ?? 'liquid' },
    'kms_ecm',
    20,
    'desc',
    canViewReports && singleFuelKind !== null,
  );

  const user = me.data?.user;
  const firstName = user?.full_name.trim().split(/\s+/)[0] || 'bienvenido';
  const userId = user?.id;
  const [greeting, setGreeting] = React.useState('Hola');

  React.useEffect(() => {
    if (!userId) return;

    const updateGreeting = () => setGreeting(getGreeting(new Date(), userId));
    updateGreeting();
    const interval = window.setInterval(updateGreeting, 60_000);
    return () => window.clearInterval(interval);
  }, [userId]);

  if (me.isLoading) {
    return (
      <section className="space-y-6" aria-label="Cargando resumen operativo">
        <Skeleton className="h-24 w-full" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-32" />
          ))}
        </div>
      </section>
    );
  }

  const selectedFleets =
    fleetFilter.selectedFleetIds.length > 0 ? fleetFilter.currentFleets : fleetFilter.fleets;
  const quickLinks = QUICK_LINKS.filter((link) => hasPermission(link.permission));
  const scopeLabel =
    selectedFleets.length === 1
      ? (selectedFleets[0]?.name ?? 'tu flota')
      : selectedFleets.length > 1
        ? `${selectedFleets.length} flotas`
        : 'tu alcance';
  const availabilitySummary = disponibilidad.data?.summary;
  const availabilityValue =
    availabilitySummary && availabilitySummary.placas > 0
      ? formatPercent(availabilitySummary.availabilityPctMec)
      : '—';
  const liquidValue = performanceValue(liquidPerformance.data, 'liquid');
  const gasValue = performanceValue(gasPerformance.data, 'gas');
  const performanceLoading =
    canViewReports &&
    (analyticsVehicles.isLoading ||
      (hasLiquidVehicles && liquidPerformance.isLoading) ||
      (hasGasVehicles && gasPerformance.isLoading));
  const performanceKinds = [hasLiquidVehicles && 'liquid', hasGasVehicles && 'gas'].filter(Boolean);
  const performanceValueLabel = performanceLoading
    ? '…'
    : performanceKinds.length === 0
      ? '—'
      : performanceKinds.length > 1
        ? 'Mixto'
        : (liquidValue ?? gasValue ?? '—');
  const performanceDetail =
    performanceKinds.length > 1
      ? `Líquido: ${liquidValue ?? 'sin datos'} · Gas: ${gasValue ?? 'sin datos'}`
      : performanceKinds.length === 1
        ? 'Promedio del mes actual'
        : 'Sin vehículos con datos de combustible';
  const availabilityRows = (disponibilidad.data?.topPlacas ?? [])
    .slice()
    .sort((a, b) => a.availabilityPct - b.availabilityPct)
    .slice(0, 5)
    .map((row) => ({
      id: row.plate,
      plate: row.plate,
      value: row.availabilityPct,
    }));
  const orderRows = ordenes.data ? ordenes.data.currentlyOpenOrders.slice(0, 5) : [];
  const liquidRankingRows = rankingRows(liquidRanking.data);
  const gasRankingRows = rankingRows(gasRanking.data);
  const distanceRankingRows = rankingRows(distanceRanking.data);

  return (
    <section className="space-y-7">
      <header className="from-brand-gray to-brand-gray/90 relative overflow-hidden rounded-xl bg-gradient-to-r p-6 text-white shadow-md">
        <div className="relative z-10 max-w-2xl">
          <div className="mb-3 flex flex-wrap gap-2">
            <Badge className="border-white/20 bg-white/10 text-white">Resumen operativo</Badge>
            <Badge className="border-white/20 bg-white/10 capitalize text-white">
              {monthLabel}
            </Badge>
          </div>
          <PageTitle
            title={`${greeting}, ${firstName}`}
            description={`${scopeLabel}. Indicadores del mes actual.`}
            titleClassName="text-white"
            descriptionClassName="mt-1 text-white/75"
          />
        </div>
        <Gauge className="absolute -bottom-8 -right-4 h-40 w-40 text-white/5" aria-hidden />
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {canViewVehicles && (
          <KpiCard
            icon={CarFront}
            label="Vehículos activos"
            value={vehicles.isError ? '—' : (vehicles.data?.active_total ?? '…')}
            detail="En el alcance seleccionado"
          />
        )}
        {canViewMantenimiento && (
          <KpiCard
            icon={ClipboardList}
            label="Disponibilidad"
            value={disponibilidad.isError ? '—' : availabilitySummary ? availabilityValue : '…'}
            detail="Disponibilidad mecánica del mes"
          />
        )}
        {canViewReports && (
          <KpiCard
            icon={Gauge}
            label="Rendimiento"
            value={performanceValueLabel}
            detail={performanceDetail}
          />
        )}
        {canViewNovedades && (
          <KpiCard
            icon={Bell}
            label="Novedades del mes"
            value={novedades.isError ? '—' : (novedades.data?.total ?? '…')}
            detail={`${monthLabel} · alcance seleccionado`}
          />
        )}
      </div>

      {canViewMantenimiento && (
        <section className="space-y-3">
          <div>
            <h2 className="font-heading text-lg font-extrabold">Atención del mes</h2>
            <p className="text-muted-foreground text-xs">
              Prioridades de disponibilidad y mantenimiento en el alcance seleccionado.
            </p>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            <BarChartCard
              title="Menor disponibilidad por placa"
              subtitle="Las cinco placas con peor disponibilidad mecánica"
              data={availabilityRows}
              categoryKey="plate"
              valueKey="value"
              valueName="Disponibilidad"
              format={formatPercent}
              color={CHART_TONES.redMuted}
              isLoading={disponibilidad.isLoading}
              categoryAxisWidth={88}
              height={Math.max(220, availabilityRows.length * 38 + 52)}
              action={
                <Link
                  href="/mantenimiento/informe"
                  className="text-accent-blue inline-flex items-center gap-1 text-xs font-semibold"
                >
                  Ver informe <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                </Link>
              }
            />
            <ChartCard
              title="OTs abiertas (estado actual)"
              subtitle="Número de OT y tiempo transcurrido"
              isLoading={ordenes.isLoading}
              isEmpty={!ordenes.isLoading && orderRows.length === 0}
              height={Math.max(220, orderRows.length * 64 + 48)}
              action={
                <Link
                  href="/mantenimiento/informe"
                  className="text-accent-blue inline-flex items-center gap-1 text-xs font-semibold"
                >
                  Ver detalle <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                </Link>
              }
            >
              <div className="space-y-2.5">
                {orderRows.map((order) => {
                  const maxHours = Math.max(...orderRows.map((row) => row.openHours), 1);
                  return (
                    <div key={order.number} className="flex items-center gap-3">
                      <div className="w-28 shrink-0">
                        <p className="text-sm font-bold">OT #{order.number}</p>
                        <p className="text-muted-foreground truncate text-xs">
                          {order.plate} · {order.fleet}
                        </p>
                        <p className="text-accent-yellow truncate text-[11px] font-semibold">
                          {formatOtStatus(order.status)}
                        </p>
                      </div>
                      <div className="bg-muted h-2.5 flex-1 overflow-hidden rounded-full">
                        <div
                          className="bg-accent-yellow h-full rounded-full"
                          style={{ width: `${Math.max(4, (order.openHours / maxHours) * 100)}%` }}
                        />
                      </div>
                      <span className="w-20 shrink-0 text-right text-sm font-semibold tabular-nums">
                        {formatOpenDuration(order.openHours)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </ChartCard>
          </div>
        </section>
      )}

      {canViewReports && (hasLiquidVehicles || hasGasVehicles) && (
        <section className="space-y-3">
          <div>
            <h2 className="font-heading text-lg font-extrabold">Rendimiento por vehículo</h2>
            <p className="text-muted-foreground text-xs">
              Vehículos con menor rendimiento registrado durante el mes actual.
            </p>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            {hasLiquidVehicles && (singleFuelKind === null || singleFuelKind === 'liquid') && (
              <BarChartCard
                title="Combustible líquido"
                subtitle="Menor rendimiento · km/gal"
                data={liquidRankingRows}
                categoryKey="plate"
                valueKey="value"
                valueName="km/gal"
                format={(value) => formatNumber(value, 2)}
                color={CHART_COLORS.gray}
                isLoading={liquidRanking.isLoading}
                categoryAxisWidth={88}
                height={Math.max(220, liquidRankingRows.length * 38 + 52)}
              />
            )}
            {hasGasVehicles && (singleFuelKind === null || singleFuelKind === 'gas') && (
              <BarChartCard
                title="Gas"
                subtitle="Menor rendimiento · km/m³"
                data={gasRankingRows}
                categoryKey="plate"
                valueKey="value"
                valueName="km/m³"
                format={(value) => formatNumber(value, 2)}
                color={CHART_COLORS.gray}
                isLoading={gasRanking.isLoading}
                categoryAxisWidth={88}
                height={Math.max(220, gasRankingRows.length * 38 + 52)}
              />
            )}
            {singleFuelKind && (
              <BarChartCard
                title="Distancia recorrida por vehículo"
                subtitle="Mayor recorrido · km del mes"
                data={distanceRankingRows}
                categoryKey="plate"
                valueKey="value"
                valueName="Kilómetros"
                format={(value) => `${formatNumber(value, 0)} km`}
                color={CHART_COLORS.gray}
                isLoading={distanceRanking.isLoading}
                categoryAxisWidth={88}
                height={Math.max(220, distanceRankingRows.length * 38 + 52)}
              />
            )}
          </div>
        </section>
      )}

      {quickLinks.length > 0 && (
        <div>
          <div className="mb-3">
            <h2 className="font-heading text-lg font-extrabold">Accesos rápidos</h2>
            <p className="text-muted-foreground text-xs">Sólo se muestran módulos autorizados.</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {quickLinks.map((link) => {
              const Icon = link.icon;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className="hover:border-accent-blue/40 group rounded-lg border bg-white p-5 transition-colors"
                >
                  <div className="flex items-start gap-3">
                    <span className="bg-muted text-brand-gray group-hover:bg-accent-blue/10 group-hover:text-accent-blue rounded-md p-2">
                      <Icon className="h-5 w-5" aria-hidden />
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-bold group-hover:underline">{link.label}</p>
                      <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
                        {link.description}
                      </p>
                    </div>
                    <ChevronRight
                      className="text-muted-foreground ml-auto mt-1 h-4 w-4 shrink-0"
                      aria-hidden
                    />
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
