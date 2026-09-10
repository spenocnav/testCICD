'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import {
  ArrowLeft,
  Database,
  Eye,
  Gauge,
  KeyRound,
  Power,
  RotateCcw,
  ShieldCheck,
  Truck,
} from 'lucide-react';
import { toast } from 'sonner';

import { DatabaseCard } from '@/components/fleets/database-card';
import { ReprocessDialog } from '@/components/fleets/reprocess-dialog';
import { GEOTAB_STATUS_META, VehicleDetailDialog } from '@/components/fleets/vehicle-detail-dialog';
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
import { extractErrorMessage } from '@/lib/api-client';
import { useCanEdit } from '@/lib/auth';
import { useFleets } from '@/lib/fleets';
import type { Vehicle } from '@/lib/types';
import {
  useFleetDatabases,
  useFleetMotors,
  useFleetVehicles,
  useToggleVehicle,
} from '@/lib/vehicles';
import { cn } from '@/lib/utils';

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  tone,
}: {
  icon: React.ElementType;
  label: string;
  value: React.ReactNode;
  sub?: string;
  tone: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-white px-4 py-3 shadow-sm">
      <span className={cn('rounded-pill flex h-9 w-9 shrink-0 items-center justify-center', tone)}>
        <Icon className="h-4.5 w-4.5" aria-hidden />
      </span>
      <div className="min-w-0">
        <p className="font-heading text-xl font-extrabold leading-none">{value}</p>
        <p className="text-muted-foreground truncate text-xs">
          {label}
          {sub ? ` · ${sub}` : ''}
        </p>
      </div>
    </div>
  );
}

export default function FleetDetailPage() {
  const params = useParams<{ fleetId: string }>();
  const fleetId = params.fleetId;

  const { data: fleets } = useFleets();
  const { data: vehicles, isLoading, isError } = useFleetVehicles(fleetId);
  const { data: databases, isLoading: dbLoading, isError: dbError } = useFleetDatabases(fleetId);
  const { data: motors, isLoading: motorsLoading, isError: motorsError } = useFleetMotors(fleetId);
  const toggle = useToggleVehicle(fleetId);
  const canEdit = useCanEdit()('flotas');

  const fleet = fleets?.find((f) => f.id === fleetId);
  const [detail, setDetail] = React.useState<Vehicle | null>(null);
  const [reprocessOpen, setReprocessOpen] = React.useState(false);

  const onToggle = async (vehicle: Vehicle) => {
    try {
      await toggle.mutateAsync({ id: vehicle.id, is_active: !vehicle.is_active });
      toast.success(
        `Vehículo "${vehicle.plate}" ${vehicle.is_active ? 'desactivado' : 'activado'}`,
      );
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo actualizar el vehículo'));
    }
  };

  const activeVehicles = vehicles?.filter((v) => v.is_active).length ?? 0;
  const credentials = databases?.flatMap((d) => d.credentials) ?? [];
  const activeCreds = credentials.filter((c) => c.is_active).length;
  const totalRules = databases?.reduce((acc, d) => acc + d.rules.length, 0) ?? 0;

  const colSpan = canEdit ? 8 : 7;
  // Los rangos por motor solo participan del cálculo en este modo.
  const isRpmMode = fleet?.range_mode === 'rpm';

  return (
    <section className="space-y-6">
      {/* Encabezado */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" aria-label="Volver a flotas" asChild>
          <Link href="/gestion/flotas">
            <ArrowLeft aria-hidden />
          </Link>
        </Button>
        <span className="rounded-pill bg-accent-cream text-accent-yellow flex h-10 w-10 items-center justify-center">
          <Truck className="h-5 w-5" aria-hidden />
        </span>
        <div className="min-w-0">
          <h1 className="font-heading flex flex-wrap items-center gap-2 text-2xl font-extrabold tracking-tight">
            {fleet?.name ?? 'Flota'}
            {fleet && (
              <Badge variant="outline" className="font-mono font-normal">
                {fleet.code}
              </Badge>
            )}
            {fleet && (
              <Badge variant={fleet.is_active ? 'success' : 'outline'}>
                {fleet.is_active ? 'Activa' : 'Inactiva'}
              </Badge>
            )}
            {fleet && (
              // Solo informativo: el modo se configura en Navi Vehículos.
              <Badge
                variant="outline"
                title={
                  fleet.range_mode === 'rpm'
                    ? 'Las bandas se calculan con los rangos de RPM del motor.'
                    : 'Las bandas se calculan con las reglas de Geotab.'
                }
              >
                {fleet.range_mode === 'rpm' ? 'Rangos por RPM' : 'Rangos por Reglas'}
              </Badge>
            )}
            {fleet?.ralenti_analysis_enabled && (
              <Badge
                variant="info"
                title="La flota tiene contratado el Análisis de Ralentí: el ETL extrae sus episodios y Reportes muestra la pestaña."
              >
                Análisis de Ralentí
              </Badge>
            )}
          </h1>
          <p className="text-muted-foreground text-sm">
            Vehículos, bases geotab y credenciales del cliente. Réplica de solo lectura — la fuente
            de verdad es Navi Vehículos.
          </p>
        </div>
      </div>

      {/* Métricas */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          icon={Truck}
          label="Vehículos activos"
          value={isLoading ? '…' : `${activeVehicles}/${vehicles?.length ?? 0}`}
          tone="bg-accent-cream text-accent-yellow"
        />
        <StatCard
          icon={Database}
          label="Bases geotab"
          value={dbLoading ? '…' : (databases?.length ?? 0)}
          tone="bg-accent-blue/10 text-accent-blue"
        />
        <StatCard
          icon={KeyRound}
          label="Credenciales"
          value={dbLoading ? '…' : `${activeCreds}/${credentials.length}`}
          sub="activas"
          tone="bg-accent-lime/20 text-[#3F5E00]"
        />
        <StatCard
          icon={ShieldCheck}
          label="Reglas geotab"
          value={dbLoading ? '…' : totalRules}
          tone="bg-brand-red/10 text-brand-red"
        />
      </div>

      {/* Bases geotab y credenciales */}
      <div className="space-y-3">
        <h2 className="font-heading flex items-center gap-2 text-lg font-bold">
          <Database className="text-accent-blue h-4.5 w-4.5" aria-hidden />
          Bases geotab y credenciales
        </h2>
        {dbLoading ? (
          <div className="grid gap-3 xl:grid-cols-2">
            {Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} className="h-48 w-full rounded-lg" />
            ))}
          </div>
        ) : dbError ? (
          <p className="text-destructive rounded-lg border bg-white px-4 py-6 text-center text-sm">
            No se pudieron cargar las bases geotab.
          </p>
        ) : databases && databases.length > 0 ? (
          <div className="grid gap-3 xl:grid-cols-2">
            {databases.map((db) => (
              <DatabaseCard key={db.id} db={db} />
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground rounded-lg border bg-white px-4 py-6 text-center text-sm">
            Esta flota no tiene bases geotab registradas.
          </p>
        )}
      </div>

      {/* Motores */}
      <div className="space-y-3">
        <h2 className="font-heading flex items-center gap-2 text-lg font-bold">
          <Gauge className="text-brand-red h-4.5 w-4.5" aria-hidden />
          Motores
        </h2>
        {motorsLoading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : motorsError ? (
          <p className="text-destructive rounded-lg border bg-white px-4 py-6 text-center text-sm">
            No se pudieron cargar los motores.
          </p>
        ) : motors && motors.length > 0 ? (
          <div className="rounded-md border bg-white">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Motor</TableHead>
                  <TableHead className="text-right">Vehículos</TableHead>
                  <TableHead className="text-right">
                    <span title="Velocidad nominal gobernada sin carga">Velocidad gobernada</span>
                  </TableHead>
                  <TableHead className="text-right">
                    <span title="Capacidad máxima de sobrevelocidad">Sobrevelocidad máx.</span>
                  </TableHead>
                  {isRpmMode && <TableHead className="text-center">Rangos de RPM</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {motors.map((motor) => (
                  <TableRow key={motor.motor_type}>
                    <TableCell>
                      <span className="font-mono font-semibold">{motor.motor_type}</span>
                      {motor.description && (
                        <p className="text-muted-foreground text-xs">{motor.description}</p>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-right tabular-nums">
                      {motor.vehicle_count}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {motor.governed_speed_rpm === null ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        <>
                          {motor.governed_speed_rpm}
                          <span className="text-muted-foreground text-xs"> RPM</span>
                        </>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {motor.max_overspeed_rpm === null ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        <>
                          {motor.max_overspeed_rpm}
                          <span className="text-muted-foreground text-xs"> RPM</span>
                        </>
                      )}
                    </TableCell>
                    {/* Solo importa en modo 'rpm': ahí un motor sin rangos deja
                        sus vehículos fuera del cálculo de bandas. */}
                    {isRpmMode && (
                      <TableCell className="text-center">
                        {motor.rpm_band_count > 0 ? (
                          <Badge variant="success">Configurados</Badge>
                        ) : (
                          <Badge
                            variant="destructive"
                            title="Sin rangos configurados en Navi Vehículos: estos vehículos quedan fuera del cálculo de bandas."
                          >
                            Sin configurar
                          </Badge>
                        )}
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <p className="text-muted-foreground rounded-lg border bg-white px-4 py-6 text-center text-sm">
            Esta flota no tiene motores registrados.
          </p>
        )}
      </div>

      {/* Vehículos */}
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-heading flex items-center gap-2 text-lg font-bold">
            <Truck className="text-accent-yellow h-4.5 w-4.5" aria-hidden />
            Vehículos
          </h2>
          {canEdit && (
            <Button variant="outline" size="sm" onClick={() => setReprocessOpen(true)}>
              <RotateCcw aria-hidden />
              Reprocesar
            </Button>
          )}
        </div>
        <div className="rounded-md border bg-white">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Placa</TableHead>
                <TableHead>Marca / Línea</TableHead>
                <TableHead>Modelo</TableHead>
                <TableHead>Combustible</TableHead>
                <TableHead>Base geotab</TableHead>
                <TableHead>Geotab</TableHead>
                <TableHead className="text-center">Estado</TableHead>
                {canEdit && <TableHead className="text-right">Acciones</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                Array.from({ length: 4 }).map((_, i) => (
                  <TableRow key={i}>
                    <TableCell colSpan={colSpan}>
                      <Skeleton className="h-6 w-full" />
                    </TableCell>
                  </TableRow>
                ))
              ) : isError ? (
                <TableRow>
                  <TableCell colSpan={colSpan} className="text-destructive text-center">
                    No se pudieron cargar los vehículos.
                  </TableCell>
                </TableRow>
              ) : vehicles && vehicles.length > 0 ? (
                vehicles.map((vehicle) => {
                  const status = GEOTAB_STATUS_META[vehicle.geotab_customer_status] ?? {
                    label: vehicle.geotab_customer_status,
                    variant: 'outline' as const,
                  };
                  return (
                    <TableRow key={vehicle.id}>
                      <TableCell>
                        <button
                          type="button"
                          onClick={() => setDetail(vehicle)}
                          className="hover:text-accent-blue cursor-pointer font-mono font-semibold hover:underline"
                        >
                          {vehicle.plate}
                        </button>
                        {vehicle.nombre_vehiculo && (
                          <p className="text-muted-foreground text-xs">{vehicle.nombre_vehiculo}</p>
                        )}
                      </TableCell>
                      <TableCell>
                        <span className="font-medium">{vehicle.marca ?? '—'}</span>
                        {(vehicle.marketing_model_name ||
                          vehicle.service_model_name ||
                          vehicle.linea) && (
                          <span className="text-muted-foreground">
                            {' '}
                            ·{' '}
                            {vehicle.marketing_model_name ||
                              vehicle.service_model_name ||
                              vehicle.linea}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {vehicle.ano_modelo ?? '—'}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {vehicle.tipo_combustible ?? '—'}
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {vehicle.database_name ?? '—'}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <Badge variant={status.variant}>{status.label}</Badge>
                          {vehicle.geotab_device_id && (
                            <code className="text-muted-foreground text-xs">
                              {vehicle.geotab_device_id}
                            </code>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-center">
                        <Badge variant={vehicle.is_active ? 'success' : 'outline'}>
                          {vehicle.is_active ? 'Activo' : 'Inactivo'}
                        </Badge>
                      </TableCell>
                      {canEdit && (
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Ver detalle de ${vehicle.plate}`}
                              onClick={() => setDetail(vehicle)}
                            >
                              <Eye aria-hidden />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`${vehicle.is_active ? 'Desactivar' : 'Activar'} ${vehicle.plate}`}
                              className={
                                vehicle.is_active ? 'text-destructive' : 'text-emerald-600'
                              }
                              disabled={toggle.isPending}
                              onClick={() => onToggle(vehicle)}
                            >
                              <Power aria-hidden />
                            </Button>
                          </div>
                        </TableCell>
                      )}
                    </TableRow>
                  );
                })
              ) : (
                <TableRow>
                  <TableCell colSpan={colSpan} className="text-muted-foreground text-center">
                    Esta flota no tiene vehículos.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <VehicleDetailDialog
        vehicle={detail}
        fleetId={fleetId}
        onOpenChange={(open) => !open && setDetail(null)}
      />

      <ReprocessDialog
        open={reprocessOpen}
        onOpenChange={setReprocessOpen}
        fleetId={fleetId}
        vehicles={vehicles ?? []}
      />
    </section>
  );
}
