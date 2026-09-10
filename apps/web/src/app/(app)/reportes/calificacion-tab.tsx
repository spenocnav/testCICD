'use client';

import * as React from 'react';
import {
  CarFront,
  ChevronRight,
  CircleCheck,
  CircleSlash,
  Gauge,
  HelpCircle,
  ShieldAlert,
  SlidersHorizontal,
  TriangleAlert,
} from 'lucide-react';

import { Can } from '@/components/auth/can';
import { CHART_COLORS, CHART_TONES } from '@/components/charts/chart-theme';
import { DonutChartCard } from '@/components/charts/donut-chart-card';
import { GaugeCard } from '@/components/charts/gauge-card';
import { LineChartCard } from '@/components/charts/line-chart-card';
import { useFleetFilter } from '@/components/fleet/fleet-provider';
import { CalificacionCalibracionDialog } from '@/components/reportes/calificacion-calibracion-dialog';
import {
  CalificacionCalibracionBloqueada,
  CalificacionConfiguracionAviso,
} from '@/components/reportes/calificacion-configuracion-aviso';
import { CalificacionDetalleDialog } from '@/components/reportes/calificacion-detalle-dialog';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  GroupComparisonCard,
  type GroupComparisonRow,
} from '@/components/vehicles/group-comparison-card';
import {
  promedioQgenVisible,
  type ReportesFilters,
  useCalificacion,
  useVehiculos,
} from '@/lib/reportes';
import type { CalificacionUmbrales, CalificacionVehiculo } from '@/lib/types';

import { CalificacionMetodologiaDialog } from './calificacion-metodologia-dialog';
import { fmt, fmtInt, KpiCard } from './shared';

const scoreFmt = (v: number) => fmt(v, 1);
const pctFmt = (v: number) => `${fmt(v, 1)} %`;

// Zonas del gauge del Q General. Se derivan de los umbrales que publica el API:
// tenerlos duplicados aquí ya provocó que el gauge pintara un 78.3 como "No
// cumple (<90)" mientras la tabla, con el estado del backend, decía "Cumple".
function scoreZones(umbrales?: CalificacionUmbrales) {
  const enRiesgo = umbrales?.en_riesgo ?? 0;
  const cumple = umbrales?.cumple ?? 100;
  return [
    { upto: enRiesgo, color: CHART_TONES.redMuted, label: `No cumple (<${fmt(enRiesgo, 0)})` },
    {
      upto: cumple,
      color: CHART_TONES.yellowDark,
      label: `En riesgo (${fmt(enRiesgo, 0)}–${fmt(cumple, 0)})`,
    },
    { upto: 100, color: CHART_TONES.limeDark, label: `Cumple (≥${fmt(cumple, 0)})` },
  ];
}

// Color del badge de estado en la tabla.
const ESTADO_VARIANT: Record<string, 'success' | 'warning' | 'destructive'> = {
  Cumple: 'success',
  'En riesgo': 'warning',
  'No cumple': 'destructive',
};

// Color del segmento del donut por estado. Mapear por etiqueta (no por índice):
// los segmentos en cero se filtran y el índice dejaría de alinear con el color.
const ESTADO_COLOR: Record<string, string> = {
  Cumple: CHART_TONES.limeDark,
  'En riesgo': CHART_TONES.yellowDark,
  'No cumple': CHART_TONES.redMuted,
};

function monthRangeForPeriod(period: number, filters: ReportesFilters): ReportesFilters {
  const year = Math.floor(period / 100);
  const month = period % 100;
  const monthText = String(month).padStart(2, '0');
  const monthStart = `${year}-${monthText}-01`;
  const lastDay = new Date(year, month, 0).getDate();
  const monthEnd = `${year}-${monthText}-${String(lastDay).padStart(2, '0')}`;

  return {
    ...filters,
    date_from: filters.date_from && filters.date_from > monthStart ? filters.date_from : monthStart,
    date_to: filters.date_to && filters.date_to < monthEnd ? filters.date_to : monthEnd,
  };
}

export function CalificacionTab({
  filters,
  onSelectGroup,
}: {
  filters: ReportesFilters;
  /** Clic en una barra del comparativo → aplicar ese grupo al filtro global. */
  onSelectGroup?: (groupId: string) => void;
}) {
  const q = useCalificacion(filters);
  const data = q.data;
  const [selectedPeriod, setSelectedPeriod] = React.useState<number | null>(null);
  const [selectedState, setSelectedState] = React.useState<string | null>(null);
  const [metodologiaOpen, setMetodologiaOpen] = React.useState(false);
  const [calibracionOpen, setCalibracionOpen] = React.useState(false);
  const [detalleVehiculo, setDetalleVehiculo] = React.useState<CalificacionVehiculo | null>(null);

  // La calibración es POR flota, así que solo se puede editar con una flota en
  // el alcance. `selectedFleetIds` vacío significa «todas las visibles», que
  // con una sola flota accesible sigue siendo una sola flota.
  const { fleets, selectedFleetIds } = useFleetFilter();
  const scopedFleetIds = selectedFleetIds.length > 0 ? selectedFleetIds : fleets.map((f) => f.id);
  const configuracion = data?.configuracion;
  const calibracionFleetId =
    configuracion?.fleet_id ?? (scopedFleetIds.length === 1 ? (scopedFleetIds[0] ?? null) : null);
  const calibracionFleetName =
    fleets.find((fleet) => fleet.id === calibracionFleetId)?.name ?? null;

  const availablePeriods = data?.evolucion?.map((point) => point.periodo) ?? [];
  React.useEffect(() => {
    if (availablePeriods.length === 0) {
      setSelectedPeriod(null);
      return;
    }
    setSelectedPeriod((current) => {
      if (current != null && availablePeriods.includes(current)) return current;
      return null;
    });
  }, [data?.evolucion]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedFilters = React.useMemo(
    () => (selectedPeriod == null ? filters : monthRangeForPeriod(selectedPeriod, filters)),
    [filters, selectedPeriod],
  );
  const selectedQ = useCalificacion(selectedFilters);
  const selectedData = selectedQ.data ?? data;
  const selectedLabel = data?.evolucion?.find((point) => point.periodo === selectedPeriod)?.label;
  const allVehicles = selectedData?.vehiculos ?? [];

  const estado = selectedData?.estado;
  const donutData = estado
    ? [
        { label: 'Cumple', value: estado.cumple },
        { label: 'En riesgo', value: estado.en_riesgo },
        { label: 'No cumple', value: estado.no_cumple },
      ].filter((d) => d.value > 0)
    : [];

  React.useEffect(() => {
    if (selectedState && !donutData.some((item) => item.label === selectedState)) {
      setSelectedState(null);
    }
  }, [donutData, selectedState]);

  const selectedStateVehicleIds = selectedState
    ? allVehicles
        .filter((vehicle) => vehicle.estado === selectedState)
        .map((vehicle) => vehicle.vehicle_id)
    : [];
  const stateFilters: ReportesFilters =
    selectedState && selectedStateVehicleIds.length > 0
      ? { ...filters, vehicle_id: selectedStateVehicleIds }
      : filters;
  const stateQ = useCalificacion(stateFilters);
  const trendData = selectedState ? (stateQ.data ?? data) : data;

  // % de tiempo por banda vienen como fracción 0..1; el gráfico usa puntos %.
  const operativos = (trendData?.operativos ?? []).map((p) => ({
    label: p.label,
    periodo: p.periodo,
    pct_eficiente: p.pct_eficiente != null ? p.pct_eficiente * 100 : null,
    pct_ralenti: p.pct_ralenti != null ? p.pct_ralenti * 100 : null,
    eventos_rpm: p.eventos_rpm,
    eventos_rpm_sobre_gobernada: p.eventos_rpm_sobre_gobernada,
  }));

  const vehiculos = selectedState
    ? allVehicles.filter((vehicle) => vehicle.estado === selectedState)
    : allVehicles;
  // Al filtrar por estado el promedio se recalcula sobre las filas visibles,
  // con los MISMOS pesos que usó el backend: una media simple aquí haría que el
  // gauge cambiara de fórmula al hacer clic en el donut. La regla es una función
  // pura con pruebas (`promedioQgenVisible`).
  const selectedAverage =
    selectedState && vehiculos.length > 0
      ? promedioQgenVisible(vehiculos)
      : (selectedData?.promedio_general ?? null);

  const kpiStatus = React.useMemo(() => {
    const status = {
      evaluados: 0,
      cumple: 0,
      enRiesgo: 0,
      noCumple: 0,
    };

    for (const vehicle of vehiculos) {
      if (vehicle.qgen == null) continue;
      status.evaluados += 1;
      if (vehicle.estado === 'Cumple') status.cumple += 1;
      if (vehicle.estado === 'En riesgo') status.enRiesgo += 1;
      if (vehicle.estado === 'No cumple') status.noCumple += 1;
    }

    return status;
  }, [vehiculos]);

  const compliancePct =
    kpiStatus.evaluados > 0 ? (kpiStatus.cumple / kpiStatus.evaluados) * 100 : null;
  const attentionCount = kpiStatus.enRiesgo + kpiStatus.noCumple;
  // Un vehículo penalizado no es un vehículo con mala nota: su nota fue anulada.
  // Se cuenta aparte para poder avisarlo sobre la tabla.
  const penalizados = vehiculos.filter((vehicle) => vehicle.penalizado_por_sobrevelocidad);
  // Y uno sin exposición suficiente tampoco es un vehículo con mala nota: no
  // tiene nota. Se cuenta aparte de los penalizados porque son dos cosas
  // distintas —una falta contra falta de datos— y confundirlas fue justo lo
  // que hacía ilegible el gauge.
  const sinExposicion = vehiculos.filter(
    (vehicle) => vehicle.exposicion_suficiente === false && !vehicle.penalizado_por_sobrevelocidad,
  );

  const pickPeriod = React.useCallback((payload?: Record<string, unknown>) => {
    const period = payload?.periodo;
    if (typeof period === 'number') {
      setSelectedPeriod((current) => (current === period ? null : period));
    }
  }, []);

  // Comparativo por grupo interno del cliente: todo sale de datos ya en
  // memoria (vehiculos[] de la calificación + catálogo analytics para el
  // grupo de cada vehículo). Cero consultas nuevas.
  const { data: catalogoVehiculos } = useVehiculos();
  const groupRows: GroupComparisonRow[] = React.useMemo(() => {
    const groupByVehicle = new Map(
      (catalogoVehiculos ?? []).map((v) => [v.vehicle_id, v.vehicle_group_id]),
    );
    return vehiculos.map((v) => ({
      groupId: groupByVehicle.get(v.vehicle_id) ?? null,
      values: {
        placas: 1,
        evaluados: v.qgen != null ? 1 : 0,
        qgen_sum: v.qgen ?? 0,
        no_cumple: v.estado === 'No cumple' ? 1 : 0,
      },
    }));
  }, [vehiculos, catalogoVehiculos]);
  const umbrales = selectedData?.umbrales ?? data?.umbrales;
  const colorForQgen = React.useCallback(
    (value: number) => {
      if (umbrales && value >= umbrales.cumple) return 'bg-emerald-500';
      if (umbrales && value >= umbrales.en_riesgo) return 'bg-amber-400';
      return 'bg-red-500';
    },
    [umbrales],
  );

  return (
    <>
      {/* Con qué calibración se calculó el puntaje que se está viendo. */}
      <CalificacionConfiguracionAviso configuracion={configuracion} />

      {/* La nota combina pesos y escalas que no son obvios: se explican a un clic. */}
      <div className="mb-3 flex flex-wrap items-center justify-end gap-4">
        <button
          type="button"
          onClick={() => setMetodologiaOpen(true)}
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5 text-xs font-medium transition"
        >
          <HelpCircle className="h-4 w-4" aria-hidden />
          ¿Cómo se calcula la calificación?
        </button>

        {/* Calibrar cambia el puntaje de toda la flota: exige `reportes.edit`.
            Ocultarlo aquí es experiencia de usuario; el backend lo exige igual. */}
        <Can permission="reportes.edit">
          {calibracionFleetId ? (
            <button
              type="button"
              onClick={() => setCalibracionOpen(true)}
              className="text-accent-blue hover:text-accent-blue/80 inline-flex items-center gap-1.5 text-xs font-semibold transition"
            >
              <SlidersHorizontal className="h-4 w-4" aria-hidden />
              Calibrar parámetros de la flota
            </button>
          ) : (
            <CalificacionCalibracionBloqueada />
          )}
        </Can>
      </div>

      {calibracionFleetId && (
        <Can permission="reportes.edit">
          <CalificacionCalibracionDialog
            open={calibracionOpen}
            onOpenChange={setCalibracionOpen}
            fleetId={calibracionFleetId}
            fleetName={calibracionFleetName}
          />
        </Can>
      )}

      <CalificacionMetodologiaDialog
        open={metodologiaOpen}
        onOpenChange={setMetodologiaOpen}
        metodologia={selectedData?.metodologia ?? data?.metodologia}
        umbrales={selectedData?.umbrales ?? data?.umbrales}
      />

      {/* KPIs del periodo/estado seleccionado. */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard
          label="Q General"
          value={fmt(selectedAverage, 1)}
          hint="promedio ponderado"
          trend={(trendData?.evolucion ?? []).map((point) => point.qgen)}
          icon={<Gauge size={32} className="text-accent-blue" />}
        />
        <KpiCard
          label="Vehículos evaluados"
          value={fmtInt(kpiStatus.evaluados)}
          hint="con Q General disponible"
          icon={<CarFront size={32} className="text-accent-blue" />}
        />
        <KpiCard
          label="Cumplimiento"
          value={compliancePct == null ? '—' : `${fmt(compliancePct, 1)} %`}
          hint={`${fmtInt(kpiStatus.cumple)} vehículos cumplen`}
          icon={<CircleCheck size={32} className="text-emerald-600" />}
        />
        <KpiCard
          label="Requieren atención"
          value={fmtInt(attentionCount)}
          hint={`${fmtInt(kpiStatus.enRiesgo)} en riesgo · ${fmtInt(kpiStatus.noCumple)} no cumplen`}
          icon={<TriangleAlert size={32} className="text-accent-yellow" />}
        />
      </div>

      {/* Primera fila: indicador general (1/3) + evolución de calificaciones (2/3). */}
      <div className="mb-6 grid grid-cols-1 items-stretch gap-4 lg:grid-cols-3">
        <div className="h-full [&>div]:h-full">
          <GaugeCard
            title="Prom. Calificación General"
            subtitle={
              selectedState
                ? `${selectedState}${selectedLabel ? ` · ${selectedLabel}` : ''}`
                : selectedLabel
                  ? `Q General del periodo: ${selectedLabel}`
                  : 'Q General promedio de la flota en el periodo'
            }
            value={selectedAverage}
            unit="pts"
            zones={scoreZones(selectedData?.umbrales ?? data?.umbrales)}
            hint={
              selectedAverage != null
                ? `${fmtInt(kpiStatus.evaluados)} vehículos evaluados` +
                  (sinExposicion.length > 0
                    ? ` · ${fmtInt(sinExposicion.length)} sin exposición suficiente`
                    : '')
                : undefined
            }
          />
        </div>
        <div className="h-full lg:col-span-2 [&>div]:h-full">
          <LineChartCard
            title="Evolución de calificaciones"
            subtitle="Q.H. Seguros, Q.H. Operación y Q General por mes"
            data={trendData?.evolucion ?? []}
            xKey="label"
            isLoading={q.isLoading || (selectedState != null && stateQ.isLoading)}
            action={
              selectedLabel ? (
                <button
                  type="button"
                  onClick={() => setSelectedPeriod(null)}
                  className="bg-accent-blue/10 text-accent-blue hover:bg-accent-blue/20 rounded-pill px-3 py-1 text-xs font-semibold transition-colors"
                  aria-label={`Quitar selección de ${selectedLabel}`}
                >
                  Viendo: {selectedLabel} ×
                </button>
              ) : null
            }
            selectedBucket={selectedLabel ?? null}
            yDomain={[0, 100]}
            referenceY={{ value: 80, color: CHART_TONES.redMuted }}
            onBucketClick={(_label, payload) => pickPeriod(payload)}
            series={[
              { key: 'qhs', name: 'Q.H. Seguros', color: CHART_COLORS.blue, format: scoreFmt },
              {
                key: 'qho',
                name: 'Q.H. Operación',
                color: CHART_TONES.yellowDark,
                format: scoreFmt,
              },
              {
                key: 'qgen',
                name: 'Q General',
                color: CHART_TONES.limeDark,
                format: scoreFmt,
              },
            ]}
            height={320}
          />
        </div>
      </div>

      {/* Segunda fila: estado y metas porcentuales de operación. */}
      <div className="mb-6 grid grid-cols-1 items-stretch gap-4 lg:grid-cols-3">
        <div className="h-full [&>div]:h-full">
          <DonutChartCard
            title="Estado General"
            subtitle="Clic en un estado para filtrar las demás visualizaciones"
            data={donutData}
            colors={donutData.map((d) => ESTADO_COLOR[d.label] ?? CHART_COLORS.gray)}
            isLoading={selectedQ.isLoading}
            format={(v) => `${v} veh.`}
            selectedLabel={selectedState}
            onSegmentClick={(state) =>
              setSelectedState((current) => (current === state ? null : state))
            }
            action={
              selectedState ? (
                <button
                  type="button"
                  onClick={() => setSelectedState(null)}
                  className="bg-accent-blue/10 text-accent-blue hover:bg-accent-blue/20 rounded-pill px-3 py-1 text-xs font-semibold transition-colors"
                  aria-label={`Quitar filtro de estado ${selectedState}`}
                >
                  Estado: {selectedState} ×
                </button>
              ) : null
            }
            height={320}
          />
        </div>
        <div className="h-full [&>div]:h-full">
          <LineChartCard
            title="Tiempo en rango eficiente"
            subtitle="Meta: 70% o más"
            data={operativos}
            xKey="label"
            isLoading={q.isLoading || (selectedState != null && stateQ.isLoading)}
            selectedBucket={selectedLabel ?? null}
            yDomain={[0, 100]}
            referenceY={{ value: 70, color: CHART_TONES.limeDark }}
            onBucketClick={(_label, payload) => pickPeriod(payload)}
            series={[
              {
                key: 'pct_eficiente',
                name: '% Rango eficiente',
                color: CHART_TONES.limeDark,
                format: pctFmt,
              },
            ]}
            height={320}
          />
        </div>
        <div className="h-full [&>div]:h-full">
          <LineChartCard
            title="Ralentí"
            subtitle="Objetivo: 10% o menos"
            data={operativos}
            xKey="label"
            isLoading={q.isLoading || (selectedState != null && stateQ.isLoading)}
            selectedBucket={selectedLabel ?? null}
            yDomain={[0, 100]}
            referenceY={{ value: 10, color: CHART_TONES.redMuted }}
            onBucketClick={(_label, payload) => pickPeriod(payload)}
            series={[
              {
                key: 'pct_ralenti',
                name: '% Ralentí',
                color: CHART_TONES.yellowDark,
                format: pctFmt,
              },
            ]}
            height={320}
          />
        </div>
      </div>

      {/* Los RPM son eventos puntuales: se muestran como conteos, no porcentajes. */}
      <div className="mb-6">
        <LineChartCard
          title="Excesos de RPM"
          subtitle="Conteo mensual; penalizan más los excesos que superan la velocidad gobernada del motor de cada vehículo. Un motor sin ese dato capturado no recibe la agravación."
          data={operativos}
          xKey="label"
          isLoading={q.isLoading || (selectedState != null && stateQ.isLoading)}
          selectedBucket={selectedLabel ?? null}
          onBucketClick={(_label, payload) => pickPeriod(payload)}
          series={[
            {
              key: 'eventos_rpm',
              name: 'Total eventos RPM',
              color: CHART_TONES.yellowDark,
              format: (value) => fmt(value, 0),
            },
            {
              key: 'eventos_rpm_sobre_gobernada',
              name: 'Eventos > gobernada',
              color: CHART_TONES.redMuted,
              format: (value) => fmt(value, 0),
            },
          ]}
          height={300}
        />
      </div>

      {/* Comparativo por grupo interno del cliente (solo con una flota en
          alcance y con grupos; el componente se oculta solo). */}
      <div className="mb-6">
        <GroupComparisonCard
          title="Q General por grupo"
          subtitle="Promedio ponderado por vehículos evaluados; clic en una barra filtra la pestaña."
          rows={groupRows}
          derive={(t) => ((t.evaluados ?? 0) > 0 ? (t.qgen_sum ?? 0) / (t.evaluados ?? 1) : null)}
          format={(value) => fmt(value, 1)}
          detail={(t) =>
            `${fmtInt(t.placas ?? 0)} placas · ${fmtInt(t.evaluados ?? 0)} evaluadas` +
            ((t.no_cumple ?? 0) > 0 ? ` · ${fmtInt(t.no_cumple ?? 0)} en rojo` : '')
          }
          reference={
            selectedAverage != null ? { label: 'Promedio de la flota', value: selectedAverage } : null
          }
          colorFor={colorForQgen}
          onSelectGroup={onSelectGroup}
          isLoading={selectedQ.isLoading}
        />
      </div>

      {/* Tabla por vehículo */}
      <h2 className="font-heading mb-3 text-sm font-bold tracking-tight">
        Calificación por vehículo
      </h2>
      <p className="text-muted-foreground mb-3 text-xs">
        {selectedState
          ? `${selectedState}${selectedLabel ? ` en ${selectedLabel}` : ''}, peores primero.`
          : selectedLabel
            ? `Puntajes de ${selectedLabel}, peores primero.`
            : 'Puntajes del periodo completo, peores primero.'}{' '}
        Clic en una fila para ver el desglose del vehículo.
      </p>
      {(q.isError || selectedQ.isError || stateQ.isError) && (
        <p className="text-destructive text-sm">Error al cargar datos. Intenta de nuevo.</p>
      )}

      {/* La penalización es una anulación de la nota, no un puntaje bajo: se
          anuncia antes de la tabla para que no haya que buscarla fila por fila. */}
      {penalizados.length > 0 && (
        <div className="border-destructive/40 bg-destructive/5 mb-3 flex items-start gap-3 rounded-lg border p-3">
          <ShieldAlert className="text-destructive mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <p className="text-xs leading-snug">
            <span className="text-destructive font-semibold">
              {fmtInt(penalizados.length)}{' '}
              {penalizados.length === 1 ? 'vehículo quedó' : 'vehículos quedaron'} en 0 por
              penalización
            </span>{' '}
            <span className="text-muted-foreground">
              — superaron la sobrevelocidad máxima de su motor. Un solo exceso anula el Q General y
              fija el estado en «No cumple».
            </span>
          </p>
        </div>
      )}
      {/* Una línea discreta, no una caja de color: no es una alerta, es una
          aclaración de por qué esas filas no tienen nota. */}
      {sinExposicion.length > 0 && (
        <p className="text-muted-foreground mb-3 text-xs leading-snug">
          {fmtInt(sinExposicion.length)}{' '}
          {sinExposicion.length === 1 ? 'vehículo operó' : 'vehículos operaron'} por debajo de la
          exposición mínima del periodo, así que no reciben puntaje ni entran en el promedio. Su
          nota sería una tasa sin recorrido que la sostenga. Se muestra abajo el puntaje que
          habrían tenido, como referencia.
        </p>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Placa</TableHead>
            <TableHead className="text-right">Eventos RPM</TableHead>
            <TableHead
              className="text-right"
              title="Excesos por encima de la velocidad gobernada del motor de ese vehículo. Un motor sin el dato capturado no recibe la agravación: sus eventos cuentan solo en la columna anterior."
            >
              RPM &gt; gobernada
            </TableHead>
            <TableHead
              className="text-right"
              title="Excesos por encima de la sobrevelocidad máxima del motor. Uno solo anula el Q General del vehículo. Un motor sin ese límite capturado no puede disparar la penalización."
            >
              RPM &gt; sobrevelocidad
            </TableHead>
            <TableHead className="text-right">Q.H. Seguros</TableHead>
            <TableHead className="text-right">Q.H. Operación</TableHead>
            <TableHead className="text-right">Q General</TableHead>
            <TableHead>Estado</TableHead>
            <TableHead className="w-8">
              <span className="sr-only">Detalle</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {vehiculos.length === 0 && !selectedQ.isLoading && (
            <TableRow>
              <TableCell colSpan={9} className="text-muted-foreground text-center">
                Sin datos para los filtros seleccionados.
              </TableCell>
            </TableRow>
          )}
          {vehiculos.map((v) => (
            <TableRow
              key={v.vehicle_id}
              tabIndex={0}
              aria-haspopup="dialog"
              title={`Ver desglose de ${v.placa ?? v.vehicle_id}`}
              onClick={() => setDetalleVehiculo(v)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  setDetalleVehiculo(v);
                }
              }}
              className={
                v.penalizado_por_sobrevelocidad
                  ? 'bg-destructive/5 hover:bg-destructive/10 cursor-pointer'
                  : v.exposicion_suficiente === false
                    ? 'hover:bg-muted/60 cursor-pointer opacity-70'
                    : 'hover:bg-muted/60 cursor-pointer'
              }
            >
              <TableCell>
                <div className="flex items-center gap-1.5 font-medium">
                  {v.penalizado_por_sobrevelocidad && (
                    <ShieldAlert
                      className="text-destructive h-4 w-4 shrink-0"
                      aria-label="Penalizado por sobrevelocidad"
                    />
                  )}
                  {!v.penalizado_por_sobrevelocidad && v.exposicion_suficiente === false && (
                    <CircleSlash
                      className="text-muted-foreground h-4 w-4 shrink-0"
                      aria-label="Sin exposición suficiente para calificar"
                    />
                  )}
                  {v.placa ?? v.vehicle_id}
                </div>
                <Badge variant={v.vocacional ? 'info' : 'outline'}>
                  {v.vocacional ? 'Vocacional · por horas' : 'Comercial · por km'}
                </Badge>
              </TableCell>
              <TableCell className="text-right">{fmt(v.eventos_rpm, 0)}</TableCell>
              <TableCell className="text-destructive text-right font-medium">
                {fmt(v.eventos_rpm_sobre_gobernada, 0)}
              </TableCell>
              {/* Solo se tiñe cuando hay excesos: un 0 aquí es la buena noticia. */}
              <TableCell
                className={
                  v.eventos_rpm_sobre_sobrevelocidad > 0
                    ? 'text-destructive text-right font-bold'
                    : 'text-muted-foreground text-right'
                }
              >
                {fmt(v.eventos_rpm_sobre_sobrevelocidad, 0)}
              </TableCell>
              <TableCell className="text-right">{v.qhs != null ? fmt(v.qhs, 1) : '—'}</TableCell>
              <TableCell className="text-right">{v.qho != null ? fmt(v.qho, 1) : '—'}</TableCell>
              {/* El 0 de una penalización no puede leerse como dato faltante ni
                  como un vehículo que apenas operó: se etiqueta como sanción y
                  se muestra el puntaje que habría tenido. */}
              <TableCell className="text-right">
                {v.penalizado_por_sobrevelocidad ? (
                  <div className="flex flex-col items-end leading-tight">
                    <span className="text-destructive text-base font-bold">
                      {v.qgen != null ? fmt(v.qgen, 1) : '—'}
                    </span>
                    <span className="text-destructive text-[11px] font-semibold uppercase tracking-wide">
                      Penalizado
                    </span>
                    {v.qgen_base != null && (
                      <span className="text-muted-foreground text-[11px]">
                        habría sido {fmt(v.qgen_base, 1)}
                      </span>
                    )}
                  </div>
                ) : v.exposicion_suficiente === false ? (
                  // Ni 0 ni un guion pelado: el 0 lo leería como sanción y el
                  // guion como dato perdido. Se dice lo que pasó y se conserva
                  // a la vista el puntaje que habría tenido.
                  <div className="flex flex-col items-end leading-tight">
                    <span className="text-muted-foreground text-base font-semibold">—</span>
                    <span className="text-muted-foreground text-[11px] font-medium uppercase tracking-wide">
                      Sin exposición
                    </span>
                    {v.qgen_base != null && (
                      <span className="text-muted-foreground text-[11px]">
                        habría sido {fmt(v.qgen_base, 1)}
                      </span>
                    )}
                  </div>
                ) : (
                  <span className="font-semibold">{v.qgen != null ? fmt(v.qgen, 1) : '—'}</span>
                )}
              </TableCell>
              <TableCell>
                {v.estado ? (
                  <Badge variant={ESTADO_VARIANT[v.estado] ?? 'outline'}>{v.estado}</Badge>
                ) : (
                  '—'
                )}
              </TableCell>
              <TableCell className="text-muted-foreground w-8">
                <ChevronRight className="h-4 w-4" aria-hidden />
                <span className="sr-only">Ver desglose</span>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <CalificacionDetalleDialog
        vehiculo={detalleVehiculo}
        open={detalleVehiculo != null}
        onOpenChange={(open) => {
          if (!open) setDetalleVehiculo(null);
        }}
        periodoLabel={selectedLabel ?? null}
      />
    </>
  );
}
