'use client';

import * as React from 'react';
import Link from 'next/link';

import {
  ArrowLeft,
  ClipboardCheck,
  Clock,
  History,
  Repeat,
  Send,
  Search,
  UserCheck,
  Wrench,
  X,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { PageTitle } from '@/components/layout/page-title';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { UserPagination } from '@/components/users/user-pagination';
import { useFleetFilter } from '@/components/fleet/fleet-provider';
import { useCanEdit } from '@/lib/auth';
import {
  type NavifaultManagedFaultCase,
  type NavifaultManagementState,
  useNavifaultManagedFaultCaseActions,
  useNavifaultManagedFaultCases,
  useNavifaultManagementSummary,
} from '@/lib/navifault-management';
import { cn } from '@/lib/utils';

import { fmtInt, KpiCard } from '../../reportes/shared';

const PAGE_SIZE = 25;

type CaseSegment = 'managed' | 'repeated' | 'escalada' | 'pendiente_registro' | 'all';

/**
 * Los segmentos NO ofrecen `pending` a propósito, y no es una omisión.
 *
 * Un pendiente es una firma SIN caso de gestión, así que el backend no tiene
 * nada acotado por donde entrar y debe recorrer el histórico completo del hecho
 * agrupando por firma: medido, 20-23 s y 5.992 filas contra `statement_timeout`
 * de 30 s. Además duplicaría la pantalla de Navifault, que ya lista lo que está
 * sin atender. Esta bandeja existe para lo que la ventana de 24 horas esconde:
 * lo que alguien ya gestionó y lo que volvió después.
 */
const SEGMENTS: Array<{ value: CaseSegment; label: string; states: NavifaultManagementState[] }> = [
  { value: 'managed', label: 'Gestionadas', states: ['managed'] },
  { value: 'repeated', label: 'Repetidas', states: ['repeated'] },
  { value: 'escalada', label: 'Escaladas', states: ['escalada'] },
  { value: 'pendiente_registro', label: 'Sin desenlace', states: ['pendiente_registro'] },
  {
    value: 'all',
    label: 'Todas',
    // `pending` sigue fuera: cuesta 23 s y duplicaría la pantalla de Navifault,
    // que ya lista lo que está sin atender.
    states: ['managed', 'repeated', 'escalada', 'pendiente_registro'],
  },
];

function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return '—';
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return isoString;
  return new Intl.DateTimeFormat('es-CO', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(d);
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const totalMinutes = Math.max(0, Math.round(seconds / 60));
  if (totalMinutes < 1) return '< 1 min';
  if (totalMinutes < 60) return `${totalMinutes} min`;
  const days = Math.floor(totalMinutes / (60 * 24));
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return hours > 0 ? `${days} d ${hours} h` : `${days} d`;
  return minutes > 0 ? `${hours} h ${minutes} min` : `${hours} h`;
}

function stateBadge(status: NavifaultManagementState): React.ReactNode {
  if (status === 'repeated') return <Badge variant="destructive">Repetida</Badge>;
  if (status === 'managed') return <Badge variant="success">Gestionada</Badge>;
  // Los dos estados del escalamiento NO son "resuelta": la falla sigue sonando.
  // `escalada` espera al taller; `pendiente_registro` nos espera a nosotros.
  if (status === 'escalada') return <Badge variant="info">Escalada</Badge>;
  if (status === 'pendiente_registro') return <Badge variant="warning">Falta desenlace</Badge>;
  return <Badge variant="warning">Sin gestionar</Badge>;
}

function attentionBadge(attention: string | null, stopRed: boolean | null): React.ReactNode {
  const value = (attention || '').toLowerCase();
  if (value.includes('urgente') || value.includes('nivel 1') || stopRed) {
    return <Badge variant="destructive">Urgente</Badge>;
  }
  if (value.includes('prioritaria') || value.includes('nivel 2')) {
    return <Badge variant="warning">Prioritaria</Badge>;
  }
  if (value.includes('pronta') || value.includes('nivel 3')) {
    return <Badge variant="info">Pronta</Badge>;
  }
  return <Badge variant="outline">{attention || 'Estándar'}</Badge>;
}

function SegmentPicker({
  value,
  onChange,
}: {
  value: CaseSegment;
  onChange: (value: CaseSegment) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Estado de gestión"
      className="bg-muted inline-flex w-full rounded-md p-1 sm:w-auto"
    >
      {SEGMENTS.map((segment) => {
        const active = segment.value === value;
        return (
          <button
            key={segment.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(segment.value)}
            className={cn(
              'focus-visible:ring-ring min-h-11 flex-1 rounded-md px-3 py-2 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 sm:min-h-9 sm:flex-none sm:px-4 sm:text-sm',
              active
                ? 'text-foreground shadow-soft bg-white'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {segment.label}
          </button>
        );
      })}
    </div>
  );
}

export function NavifaultManagementClient() {
  const { currentFleets, scopeGlobal } = useFleetFilter();
  const canEdit = useCanEdit();
  const canManage = canEdit('navifault');

  const [segment, setSegment] = React.useState<CaseSegment>('managed');
  const [offset, setOffset] = React.useState(0);
  const [searchQuery, setSearchQuery] = React.useState('');
  const [selectedCase, setSelectedCase] = React.useState<NavifaultManagedFaultCase | null>(null);
  const [detailOpen, setDetailOpen] = React.useState(false);

  const scopeKey = React.useMemo(
    () =>
      scopeGlobal
        ? 'global'
        : currentFleets
            .map((fleet) => fleet.id)
            .sort()
            .join(','),
    [currentFleets, scopeGlobal],
  );

  const states = React.useMemo<NavifaultManagementState[]>(
    () => SEGMENTS.find((option) => option.value === segment)?.states ?? ['managed'],
    [segment],
  );

  React.useEffect(() => {
    setOffset(0);
  }, [segment, scopeKey, searchQuery]);

  const casesQuery = useNavifaultManagedFaultCases(
    states,
    scopeKey,
    PAGE_SIZE,
    offset,
    );
  const summaryQuery = useNavifaultManagementSummary(scopeKey, canManage);

  // Búsqueda local sobre la página visible, igual que en Navifault: el endpoint
  // no acepta texto y filtrar en cliente sobre 25 filas es honesto mientras el
  // contador siga diciendo el total del servidor.
  const displayedItems = React.useMemo(() => {
    const items = casesQuery.data?.items ?? [];
    const q = searchQuery.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) =>
      [
        item.plate,
        item.diagnostic,
        item.failure_mode_name,
        item.controller,
        item.last_managed_by,
        item.diagnostic_code != null ? String(item.diagnostic_code) : null,
      ]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(q)),
    );
  }, [casesQuery.data?.items, searchQuery]);

  const openDetail = (item: NavifaultManagedFaultCase) => {
    setSelectedCase(item);
    setDetailOpen(true);
  };

  const total = casesQuery.data?.total ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <Button asChild variant="ghost" size="sm" className="mt-1 gap-1.5 text-xs">
            <Link href="/navifault">
              <ArrowLeft className="h-3.5 w-3.5" />
              Navifault
            </Link>
          </Button>
          <PageTitle
            icon={ClipboardCheck}
            title="Bandeja de gestión"
            description="Fallas ya atendidas y reincidencias, sobre todo el histórico y sin la ventana de 24 horas"
          />
        </div>
        {!scopeGlobal && currentFleets.length > 0 && (
          <Badge variant="outline" className="shrink-0 text-xs">
            {currentFleets.length === 1
              ? `Flota: ${currentFleets[0]?.name || 'Activa'}`
              : `${currentFleets.length} flotas seleccionadas`}
          </Badge>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {summaryQuery.isLoading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))
        ) : (
          <>
            <KpiCard
              label="Fallas Gestionadas"
              value={fmtInt(summaryQuery.data?.managed_faults ?? 0)}
              hint="Firmas únicas cerradas"
            />
            <KpiCard
              label="Fallas Escaladas"
              value={fmtInt(summaryQuery.data?.escalated_faults ?? 0)}
              hint="Con novedad en el taller"
            />
            <KpiCard
              label="Fallas Repetidas"
              value={fmtInt(summaryQuery.data?.repeated_faults ?? 0)}
              hint="Reaparecieron tras la gestión"
            />
            <KpiCard
              label="Tiempo Prom. Gestión"
              value={formatDuration(summaryQuery.data?.average_management_seconds)}
              hint={
                summaryQuery.data?.management_time_sample_size
                  ? `${fmtInt(summaryQuery.data.management_time_sample_size)} ciclos cerrados · últimos ${summaryQuery.data.management_time_window_days} días`
                  : 'Aún no hay ciclos cerrados'
              }
            />
          </>
        )}
      </div>

      <div className="bg-card flex flex-wrap items-center gap-3 rounded-lg border p-3 shadow-sm">
        <SegmentPicker value={segment} onChange={setSegment} />
        <div className="relative w-full sm:w-64">
          <Search className="text-muted-foreground pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />
          <Input
            placeholder="Buscar en esta página: placa, código, gestor…"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            className="h-9 pl-8 pr-7 text-[11px] placeholder:text-[11px]"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="text-muted-foreground hover:text-foreground absolute right-2 top-1/2 -translate-y-1/2"
              aria-label="Limpiar búsqueda"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      <div className="bg-card rounded-lg border shadow-sm">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div className="flex items-center gap-2">
            <History className="text-brand-red h-4 w-4" />
            <h2 className="text-foreground text-sm font-bold">Historial de gestión</h2>
          </div>
          <span className="text-muted-foreground text-xs">
            {fmtInt(total)} caso{total === 1 ? '' : 's'} en total
          </span>
        </div>

        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[120px]">Estado</TableHead>
                <TableHead className="w-[110px]">Atención</TableHead>
                <TableHead className="w-[100px]">Vehículo</TableHead>
                <TableHead className="w-[90px]">Código</TableHead>
                <TableHead className="min-w-[220px]">Diagnóstico</TableHead>
                <TableHead className="w-[140px]">Última ocurrencia</TableHead>
                <TableHead className="w-[80px] text-center">Ocurr.</TableHead>
                <TableHead className="w-[170px]">Gestionada</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {casesQuery.isLoading ? (
                Array.from({ length: 6 }).map((_, i) => (
                  <TableRow key={i}>
                    {Array.from({ length: 8 }).map((__, j) => (
                      <TableCell key={j}>
                        <Skeleton className="h-5 w-full" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              ) : casesQuery.isError ? (
                <TableRow>
                  <TableCell
                    colSpan={8}
                    className="text-muted-foreground py-12 text-center"
                  >
                    <p className="text-sm font-semibold">No fue posible cargar la bandeja</p>
                    <p className="mt-1 text-xs">Vuelve a intentarlo en un momento.</p>
                  </TableCell>
                </TableRow>
              ) : displayedItems.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={8}
                    className="text-muted-foreground py-12 text-center"
                  >
                    <div className="flex flex-col items-center justify-center gap-2">
                      <Wrench className="text-muted-foreground/50 h-8 w-8" />
                      <p className="text-sm font-semibold">
                        {searchQuery
                          ? 'Ningún caso de esta página coincide con la búsqueda'
                          : segment === 'repeated'
                            ? 'Ninguna falla gestionada ha reaparecido'
                            : 'Todavía no hay fallas gestionadas'}
                      </p>
                      {!searchQuery && segment !== 'repeated' && (
                        <p className="max-w-md text-xs leading-relaxed">
                          Una falla entra aquí cuando alguien la marca como gestionada o registra
                          una reparación desde la ficha técnica en Navifault.
                        </p>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                displayedItems.map((item) => (
                  <TableRow
                    key={item.case_id ?? item.sample_fault_row_id}
                    onClick={() => openDetail(item)}
                    title="Haz clic para ver la bitácora y las reparaciones"
                    className={cn(
                      'hover:bg-muted/60 cursor-pointer transition-colors',
                      item.status === 'managed' && 'text-muted-foreground',
                    )}
                  >
                    <TableCell>{stateBadge(item.status)}</TableCell>
                    <TableCell>{attentionBadge(item.attention_type, item.stop_red)}</TableCell>
                    <TableCell className="text-foreground font-bold">
                      {item.plate || '—'}
                    </TableCell>
                    <TableCell>
                      <span className="text-foreground font-mono text-xs font-bold">
                        {item.diagnostic_code ?? '—'}
                      </span>
                      {item.failure_mode != null && (
                        <p className="text-muted-foreground mt-0.5 font-mono text-[11px]">
                          FMI {item.failure_mode}
                        </p>
                      )}
                    </TableCell>
                    <TableCell>
                      <p className="text-foreground line-clamp-2 text-xs font-medium">
                        {item.diagnostic || 'Diagnóstico no especificado'}
                      </p>
                      {item.failure_mode_name && (
                        <p
                          className="text-muted-foreground mt-0.5 line-clamp-1 text-[11px]"
                          title={item.failure_mode_name}
                        >
                          {item.failure_mode_name}
                        </p>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground whitespace-nowrap text-xs tabular-nums">
                      <div className="flex items-center gap-1.5">
                        <Clock className="h-3 w-3 shrink-0" />
                        {formatDateTime(item.last_seen_at)}
                      </div>
                    </TableCell>
                    <TableCell className="text-center text-xs tabular-nums">
                      <span className="bg-muted text-foreground inline-flex min-w-[28px] items-center justify-center rounded-md px-1.5 py-0.5 font-bold">
                        {fmtInt(item.reported_occurrences)}
                      </span>
                      {item.status === 'repeated' && item.repeated_occurrences > 0 && (
                        <p className="text-destructive mt-0.5 flex items-center justify-center gap-1 text-[11px] font-semibold">
                          <Repeat className="h-3 w-3" />+{fmtInt(item.repeated_occurrences)}
                        </p>
                      )}
                      {/* Escalada que SIGUE disparándose. Sin este número se ve
                          igual que una que se calló al escalarla, y es justo la
                          que hay que perseguir con el taller. */}
                      {(item.occurrences_since_escalation ?? 0) > 0 && (
                        <p
                          className="mt-0.5 flex items-center justify-center gap-1 text-[11px] font-semibold text-amber-700"
                          title="Ocurrencias posteriores al escalamiento"
                        >
                          <Send className="h-3 w-3" />+
                          {fmtInt(item.occurrences_since_escalation ?? 0)}
                        </p>
                      )}
                    </TableCell>
                    <TableCell className="text-xs">
                      <p className="text-foreground flex items-center gap-1.5 font-medium">
                        <UserCheck className="text-muted-foreground h-3 w-3 shrink-0" />
                        <span className="truncate" title={item.last_managed_by || undefined}>
                          {item.last_managed_by || 'Sin registrar'}
                        </span>
                      </p>
                      <p className="text-muted-foreground mt-0.5 tabular-nums">
                        {formatDateTime(item.last_managed_at)}
                      </p>
                      {/* El enlace se publica en TODOS los estados: la falla y la
                          novedad tienen ciclos independientes, así que una fila
                          puede estar gestionada con la orden del taller abierta. */}
                      {item.escalated_novedad_id && (
                        <Link
                          href={`/novedades/${item.escalated_novedad_id}`}
                          onClick={(event) => event.stopPropagation()}
                          className="text-primary mt-1 inline-flex items-center gap-1 font-medium hover:underline"
                        >
                          <Send className="h-3 w-3 shrink-0" />
                          Novedad #{item.escalated_issue_number ?? '—'}
                          {item.escalated_deleted_at
                            ? ' · borrada en CloudFleet'
                            : item.escalated_external_is_done === true
                              ? ` · OT ${item.escalated_work_order_number ?? '—'}`
                              : ' · abierta'}
                        </Link>
                      )}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        {total > 0 && (
          <div className="border-t px-4 py-3">
            <UserPagination
              total={total}
              limit={PAGE_SIZE}
              offset={offset}
              onOffsetChange={setOffset}
              showPageSelect
            />
          </div>
        )}
      </div>

      <CaseDetailDialog
        managedCase={selectedCase}
        open={detailOpen}
        onOpenChange={setDetailOpen}
      />

    </div>
  );
}

function CaseDetailDialog({
  managedCase,
  open,
  onOpenChange,
}: {
  managedCase: NavifaultManagedFaultCase | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // El caso se conserva al cerrar: Radix mantiene el diálogo montado durante la
  // animación de salida y anularlo aquí deja los hijos sin datos a media
  // transición. La misma disciplina del visor de evidencias de novedades.
  const actionsQuery = useNavifaultManagedFaultCaseActions(managedCase?.case_id, open);

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] max-w-2xl flex-col overflow-hidden">
        <DialogHeader className="shrink-0">
          <DialogTitle>Caso de gestión</DialogTitle>
          <DialogDescription>
            {managedCase
              ? `${managedCase.plate || 'Vehículo'} · código ${managedCase.diagnostic_code ?? '—'} · FMI ${managedCase.failure_mode ?? '—'}`
              : 'Bitácora y reparaciones registradas para esta firma de falla.'}
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
          {managedCase && (
            <>
              {/* El taller cerró su orden y sólo falta declarar qué se hizo. Es
                  el único punto del módulo donde el trabajo pendiente cabe en un
                  clic, así que se ofrece aquí en vez de mandar a buscar la falla
                  en la lista de 24 h, donde puede que ya no esté. */}
              {managedCase.status === 'pendiente_registro' && (
                <section className="rounded-lg border border-amber-300 bg-amber-50 p-3">
                  <p className="text-sm font-semibold text-amber-900">
                    El taller cerró la novedad #{managedCase.escalated_issue_number ?? '—'}
                    {managedCase.escalated_work_order_number
                      ? ` con la OT ${managedCase.escalated_work_order_number}`
                      : ''}
                  </p>
                  {/* El trabajo que el taller ató a la novedad. Una orden cubre
                      varios y sólo éste es de esta falla; sin él no se puede
                      saber cuál fue. */}
                  {managedCase.escalated_labor_name && (
                    <p className="mt-1 text-sm text-amber-900">
                      Trabajo registrado: <strong>{managedCase.escalated_labor_name}</strong>
                    </p>
                  )}
                  <p className="mt-1 text-xs leading-relaxed text-amber-900/80">
                    Abre la falla en Navifault y registra la orden para confirmar lo que el
                    taller hizo. Gestionar es confirmar ese registro, no declararlo aquí.
                  </p>
                </section>
              )}
              <section className="border-border bg-muted/30 rounded-lg border p-3">
                <p className="text-foreground text-sm font-semibold leading-snug">
                  {managedCase.diagnostic || 'Diagnóstico no especificado'}
                </p>
                {managedCase.failure_mode_name && (
                  <p className="text-muted-foreground mt-1 text-xs">
                    {managedCase.failure_mode_name}
                  </p>
                )}
                <div className="text-muted-foreground mt-2.5 grid grid-cols-2 gap-y-1.5 text-xs">
                  <span>Controlador</span>
                  <span className="text-foreground font-medium">
                    {managedCase.controller || '—'}
                  </span>
                  <span>Protocolo</span>
                  <span className="text-foreground font-medium">{managedCase.source || '—'}</span>
                  <span>Primera ocurrencia</span>
                  <span className="text-foreground font-medium tabular-nums">
                    {formatDateTime(managedCase.first_seen_at)}
                  </span>
                  <span>Última ocurrencia</span>
                  <span className="text-foreground font-medium tabular-nums">
                    {formatDateTime(managedCase.last_seen_at)}
                  </span>
                  <span>Ocurrencias reportadas</span>
                  <span className="text-foreground font-medium tabular-nums">
                    {fmtInt(managedCase.reported_occurrences)} en{' '}
                    {fmtInt(managedCase.analytics_records)} registros
                  </span>
                  {managedCase.status === 'repeated' && (
                    <>
                      <span>Tras la gestión</span>
                      <span className="text-destructive font-semibold tabular-nums">
                        {fmtInt(managedCase.repeated_occurrences)} ocurrencias
                      </span>
                    </>
                  )}
                </div>
                {managedCase.last_note && (
                  <p className="bg-background mt-2.5 whitespace-pre-wrap rounded-md border p-2.5 text-xs leading-relaxed">
                    {managedCase.last_note}
                  </p>
                )}
              </section>

              <section>
                <h3 className="text-muted-foreground mb-2 text-[11px] font-bold uppercase tracking-wide">
                  Bitácora
                </h3>
                {actionsQuery.isLoading ? (
                  <Skeleton className="h-16 w-full" />
                ) : actionsQuery.isError ? (
                  <p className="text-muted-foreground text-xs">
                    No fue posible cargar la bitácora del caso.
                  </p>
                ) : (actionsQuery.data ?? []).length === 0 ? (
                  <p className="text-muted-foreground text-xs">Sin acciones registradas.</p>
                ) : (
                  <ol className="space-y-2">
                    {(actionsQuery.data ?? []).map((action) => (
                      <li
                        key={action.action_id}
                        className="border-border flex items-start justify-between gap-3 rounded-lg border p-2.5 text-xs"
                      >
                        <div className="min-w-0">
                          <p className="text-foreground font-semibold">
                            {action.action_type === 'managed'
                              ? 'Marcada como gestionada'
                              : 'Gestión revertida'}
                          </p>
                          <p className="text-muted-foreground mt-0.5">
                            {action.actor_name || 'Sin registrar'} ·{' '}
                            <span className="tabular-nums">
                              {formatDateTime(action.managed_at)}
                            </span>
                          </p>
                          {action.note && (
                            <p className="text-muted-foreground mt-1 whitespace-pre-wrap leading-relaxed">
                              {action.note}
                            </p>
                          )}
                        </div>
                        <Badge
                          variant={action.action_type === 'managed' ? 'success' : 'outline'}
                          className="shrink-0"
                        >
                          {action.action_type === 'managed' ? 'Gestión' : 'Reversión'}
                        </Badge>
                      </li>
                    ))}
                  </ol>
                )}
              </section>

            </>
          )}
        </div>

        <DialogFooter className="shrink-0">
          <Button variant="default" size="sm" onClick={() => onOpenChange(false)}>
            Cerrar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  );
}
