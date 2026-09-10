'use client';

import * as React from 'react';
import { Activity, Download, Users } from 'lucide-react';

import { PageTitle } from '@/components/layout/page-title';
import { UsageUserDialog } from '@/components/usage/usage-user-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useMe } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { buildXlsx, type XlsxCell, type XlsxSheet } from '@/lib/xlsx';
import { sectionLabel, useUsageSummary, type UsageRange, type UsageSummary } from '@/lib/usage';

const RANGE_PRESETS = [
  { days: 7, label: '7 días' },
  { days: 30, label: '30 días' },
  { days: 90, label: '90 días' },
] as const;

function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function rangeForDays(days: number): UsageRange {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - (days - 1));
  return { startDate: toIsoDate(start), endDate: toIsoDate(end) };
}

export function formatDateTime(value: string | null): string {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('es-CO', { dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return value;
  }
}

function KpiCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <article className="border-border bg-card shadow-soft rounded-lg border p-4">
      <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-muted-foreground mt-1 text-xs">{hint}</p>}
    </article>
  );
}

/** Barra horizontal proporcional; sin librería de gráficos a propósito:
 *  la pantalla es admin-only y no amerita cargar Recharts. */
export function BarRow({
  label,
  value,
  max,
  detail,
}: {
  label: string;
  value: number;
  max: number;
  detail?: string;
}) {
  const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="flex items-center gap-3 py-1">
      <span className="text-foreground w-40 shrink-0 truncate text-sm" title={label}>
        {label}
      </span>
      <div className="bg-muted h-3 flex-1 overflow-hidden rounded">
        {/* Sin modificador de opacidad: --primary es un var() plano y Tailwind
            v3 no genera la clase `bg-primary/70` (barra transparente). */}
        <div className="bg-primary h-full rounded opacity-80" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-16 shrink-0 text-right text-sm tabular-nums">
        {value.toLocaleString('es-CO')}
      </span>
      {detail !== undefined && (
        <span className="text-muted-foreground w-20 shrink-0 text-right text-xs">{detail}</span>
      )}
    </div>
  );
}

function DailyActivity({ data }: { data: UsageSummary }) {
  const max = Math.max(1, ...data.daily.map((d) => d.requests));
  return (
    <section className="border-border bg-card shadow-soft rounded-lg border p-5">
      <h2 className="mb-3 text-sm font-semibold">Actividad por día</h2>
      {data.daily.length === 0 ? (
        <p className="text-muted-foreground text-sm">Sin actividad en el rango.</p>
      ) : (
        <div className="flex h-40 items-end gap-1 overflow-x-auto border-b pb-1">
          {data.daily.map((d) => (
            <div
              key={d.day}
              // h-full es imprescindible: la barra interna mide su altura en %
              // y un wrapper sin altura definida la colapsa a 0.
              className="group flex h-full min-w-2 flex-1 flex-col justify-end sm:min-w-0"
              title={`${d.day}: ${d.requests.toLocaleString('es-CO')} peticiones · ${d.users} usuarios`}
            >
              <div
                className="bg-primary w-full rounded-t opacity-70 transition-opacity group-hover:opacity-100"
                style={{ height: `${Math.max(3, (d.requests / max) * 100)}%` }}
              />
              {data.daily.length <= 31 && (
                <span className="text-muted-foreground mt-1 hidden text-[10px] [writing-mode:vertical-rl] sm:block">
                  {d.day.slice(5)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      {data.daily.length > 0 && (
        <div className="text-muted-foreground mt-2 flex justify-between text-xs">
          <span>{data.daily[0]?.day}</span>
          <span>{data.daily[data.daily.length - 1]?.day}</span>
        </div>
      )}
    </section>
  );
}

function exportUsage(data: UsageSummary) {
  const rows = (header: string[], values: (string | number | null)[][]): XlsxCell[][] => [
    header.map((value) => ({ value, style: 2 })),
    ...values.map((row) => row.map((value) => ({ value }))),
  ];
  const sheets: XlsxSheet[] = [
    {
      name: 'Usuarios',
      rows: rows(
        ['Nombre', 'Correo', 'Peticiones', 'Sección', 'Última actividad', 'Estado'],
        data.users.map((u) => [
          u.full_name ?? 'Usuario eliminado',
          u.email,
          u.requests,
          sectionLabel(u.top_section),
          u.last_seen,
          u.is_active ? 'Activo' : 'Inactivo',
        ]),
      ),
    },
    {
      name: 'Actividad diaria',
      rows: rows(
        ['Día', 'Peticiones', 'Usuarios'],
        data.daily.map((d) => [d.day, d.requests, d.users]),
      ),
    },
    {
      name: 'Secciones',
      rows: rows(
        ['Sección', 'Peticiones', 'Usuarios'],
        data.sections.map((s) => [sectionLabel(s.section), s.requests, s.users]),
      ),
    },
    {
      name: 'Flotas',
      rows: rows(
        ['Flota', 'Peticiones', 'Usuarios'],
        data.fleets.map((f) => [f.fleet_name ?? f.fleet_id, f.requests, f.users]),
      ),
    },
    {
      name: 'Rutas',
      rows: rows(
        ['Método', 'Ruta', 'Peticiones', 'Promedio ms', 'p95 ms'],
        data.routes.map((r) => [r.method, r.route, r.requests, r.avg_ms, r.p95_ms]),
      ),
    },
  ];
  const url = URL.createObjectURL(buildXlsx(sheets));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'uso_portal_' + data.start_date + '_' + data.end_date + '.xlsx';
  link.click();
  URL.revokeObjectURL(url);
}

function UsersTable({
  data,
  onSelect,
}: {
  data: UsageSummary;
  onSelect: (userId: string) => void;
}) {
  return (
    <section className="border-border bg-card shadow-soft rounded-lg border">
      <header className="flex items-center gap-2 border-b p-4">
        <Users className="text-muted-foreground size-4" />
        <h2 className="text-sm font-semibold">Usuarios más activos</h2>
        <span className="text-muted-foreground text-xs">— clic en una fila para el desglose</span>
      </header>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Usuario</TableHead>
              <TableHead className="text-right">Peticiones</TableHead>
              <TableHead>Sección principal</TableHead>
              <TableHead>Última actividad</TableHead>
              <TableHead>Estado</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.users.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-muted-foreground text-center">
                  Sin actividad registrada en el rango.
                </TableCell>
              </TableRow>
            )}
            {data.users.map((u) => (
              <TableRow
                key={u.user_id}
                className="cursor-pointer"
                onClick={() => onSelect(u.user_id)}
              >
                <TableCell>
                  <p className="font-medium">{u.full_name ?? 'Usuario eliminado'}</p>
                  <p className="text-muted-foreground text-xs">{u.email ?? u.user_id}</p>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {u.requests.toLocaleString('es-CO')}
                </TableCell>
                <TableCell>{sectionLabel(u.top_section)}</TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {formatDateTime(u.last_seen)}
                </TableCell>
                <TableCell>
                  {u.is_active === null ? (
                    <Badge variant="outline">Eliminado</Badge>
                  ) : u.is_active ? (
                    <Badge variant="success">Activo</Badge>
                  ) : (
                    <Badge variant="outline">Inactivo</Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function RoutesTable({ data }: { data: UsageSummary }) {
  return (
    <section className="border-border bg-card shadow-soft rounded-lg border">
      <header className="border-b p-4">
        <h2 className="text-sm font-semibold">Endpoints más consultados</h2>
      </header>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Ruta</TableHead>
              <TableHead className="text-right">Peticiones</TableHead>
              <TableHead className="text-right">Promedio</TableHead>
              <TableHead className="text-right">p95</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.routes.map((r) => (
              <TableRow key={`${r.method} ${r.route}`}>
                <TableCell>
                  <code className="text-xs">
                    {r.method} {r.route}
                  </code>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {r.requests.toLocaleString('es-CO')}
                </TableCell>
                <TableCell className="text-right tabular-nums">{Math.round(r.avg_ms)} ms</TableCell>
                <TableCell
                  className={cn(
                    'text-right tabular-nums',
                    r.p95_ms >= 3000 && 'text-destructive font-medium',
                  )}
                >
                  {Math.round(r.p95_ms)} ms
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

export function UsageClient() {
  const me = useMe();
  const isAdmin = me.data?.user.roles.some((role) => role.code === 'admin') ?? false;

  const [days, setDays] = React.useState<number>(30);
  const range = React.useMemo(() => rangeForDays(days), [days]);
  const [selectedUser, setSelectedUser] = React.useState<string | null>(null);
  const [isExporting, setIsExporting] = React.useState(false);

  const summary = useUsageSummary(range);

  if (me.isPending) {
    return <Skeleton className="h-64 w-full" />;
  }
  if (!isAdmin) {
    return (
      <section className="border-destructive/30 bg-destructive/5 rounded-lg border p-8 text-center">
        <p className="text-sm font-medium">
          La auditoría de uso es exclusiva del administrador de la plataforma.
        </p>
      </section>
    );
  }

  const data = summary.data;
  const maxSection = Math.max(1, ...(data?.sections.map((s) => s.requests) ?? [1]));
  const maxFleet = Math.max(1, ...(data?.fleets.map((f) => f.requests) ?? [1]));

  return (
    <section>
      <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <PageTitle
          icon={Activity}
          title="Uso del portal"
          description="Quién entra, qué consulta y sobre qué flotas. Solo peticiones autenticadas."
        />
        <div className="flex flex-wrap justify-end gap-1">
          {RANGE_PRESETS.map((preset) => (
            <Button
              key={preset.days}
              variant={days === preset.days ? 'default' : 'outline'}
              size="sm"
              onClick={() => setDays(preset.days)}
            >
              {preset.label}
            </Button>
          ))}
          <Button
            variant="outline"
            size="sm"
            disabled={!data || isExporting}
            onClick={() => {
              if (!data) return;
              setIsExporting(true);
              try {
                exportUsage(data);
              } finally {
                setIsExporting(false);
              }
            }}
          >
            <Download className="h-4 w-4" />
            {isExporting ? 'Exportando…' : 'Excel'}
          </Button>
        </div>
      </header>

      {summary.isError && (
        <section className="border-destructive/30 bg-destructive/5 mb-6 rounded-lg border p-6 text-center text-sm">
          No se pudo cargar el resumen de uso.
        </section>
      )}

      {summary.isLoading && !data && (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full" />
            ))}
          </div>
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      )}

      {data && (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <KpiCard
              label="Usuarios activos"
              value={data.active_users.toLocaleString('es-CO')}
              hint={`${data.start_date} → ${data.end_date}`}
            />
            <KpiCard
              label="Peticiones"
              value={data.total_requests.toLocaleString('es-CO')}
              hint="sin latido de sesión (/me, /auth)"
            />
            <KpiCard
              label="Secciones en uso"
              value={String(data.sections.length)}
              hint={
                data.sections[0]
                  ? `la más usada: ${sectionLabel(data.sections[0].section)}`
                  : undefined
              }
            />
          </div>

          <DailyActivity data={data} />

          <UsersTable data={data} onSelect={setSelectedUser} />

          <div className="grid gap-6 lg:grid-cols-2">
            <section className="border-border bg-card shadow-soft rounded-lg border p-5">
              <h2 className="mb-3 text-sm font-semibold">Secciones más revisadas</h2>
              {data.sections.map((s) => (
                <BarRow
                  key={s.section}
                  label={sectionLabel(s.section)}
                  value={s.requests}
                  max={maxSection}
                  detail={`${s.users} usuario${s.users === 1 ? '' : 's'}`}
                />
              ))}
              {data.sections.length === 0 && (
                <p className="text-muted-foreground text-sm">Sin datos.</p>
              )}
            </section>

            <section className="border-border bg-card shadow-soft rounded-lg border p-5">
              <h2 className="mb-3 text-sm font-semibold">Flotas más consultadas</h2>
              <p className="text-muted-foreground mb-2 text-xs">
                Según el filtro de flota aplicado; una consulta con “Todas” no suma aquí.
              </p>
              {data.fleets.map((f) => (
                <BarRow
                  key={f.fleet_id}
                  label={f.fleet_name ?? f.fleet_id}
                  value={f.requests}
                  max={maxFleet}
                  detail={`${f.users} usuario${f.users === 1 ? '' : 's'}`}
                />
              ))}
              {data.fleets.length === 0 && (
                <p className="text-muted-foreground text-sm">Sin datos.</p>
              )}
            </section>
          </div>

          <RoutesTable data={data} />
        </div>
      )}

      <UsageUserDialog userId={selectedUser} range={range} onClose={() => setSelectedUser(null)} />
    </section>
  );
}
