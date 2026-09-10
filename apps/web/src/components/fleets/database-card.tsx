'use client';

import * as React from 'react';
import { ChevronDown, Database, Gauge, KeyRound, ShieldCheck, Truck } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { GeotabDatabase, GeotabRule } from '@/lib/types';
import { administrativeSafeHabitRules } from '@/lib/rule-visibility';
import { cn } from '@/lib/utils';

function timeAgo(iso: string | null): string {
  if (!iso) return 'nunca';
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  if (mins < 60) return `hace ${mins} min`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `hace ${hours} h`;
  return `hace ${Math.round(hours / 24)} días`;
}

const RPM_BANDS = [
  { key: 'rango_bajo', label: 'Rango bajo' },
  { key: 'rango_economico', label: 'Rango económico' },
  { key: 'rango_balanceado', label: 'Rango balanceado' },
  { key: 'rango_potencia', label: 'Rango potencia' },
  { key: 'rango_potencia_ineficiente', label: 'Rango potencia ineficiente' },
  { key: 'exceso_rpm', label: 'Exceso de RPM' },
  { key: 'ralenti', label: 'Ralentí' },
] as const;

const SAFE_HABIT_DESCRIPTIONS = [
  'Excesos de velocidad',
  'Giros bruscos',
  'Excesos de RPM',
  'Frenadas bruscas',
  'Baches o Resaltos fuertes',
  'Aceleraciones bruscas',
] as const;

function RuleNames({ rules }: { rules: GeotabRule[] }) {
  if (rules.length === 0) {
    return <span className="text-muted-foreground">—</span>;
  }

  return (
    <div className="flex flex-col gap-1">
      {rules
        .slice()
        .sort((a, b) => a.name.localeCompare(b.name, 'es'))
        .map((rule) => (
          <span
            key={rule.id}
            title={`rule_id: ${rule.rule_id}`}
            className={cn(
              'text-xs leading-snug',
              !rule.is_active && 'text-muted-foreground line-through',
            )}
          >
            {rule.name}
          </span>
        ))}
    </div>
  );
}

export function DatabaseCard({ db }: { db: GeotabDatabase }) {
  const [open, setOpen] = React.useState(false);
  const operationRules = db.rules.filter((rule) => rule.category === 'operacion');
  const safeHabitRules = administrativeSafeHabitRules(db.rules);
  const motorTypes = Array.from(
    new Set(operationRules.map((rule) => rule.motor_type ?? 'Todos los motores')),
  ).sort((a, b) => a.localeCompare(b, 'es'));

  return (
    <article
      className={cn(
        'border-accent-blue/70 group rounded-lg border border-l-4 bg-white shadow-sm',
        !db.is_active && 'border-l-brand-clear opacity-80',
      )}
    >
      {/* Encabezado de la base */}
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-controls={`database-content-${db.id}`}
        className="flex w-full cursor-pointer flex-wrap items-center gap-3 px-5 py-4 text-left"
      >
        <span className="bg-accent-blue/10 text-accent-blue rounded-pill flex h-9 w-9 shrink-0 items-center justify-center">
          <Database className="h-4.5 w-4.5" aria-hidden />
        </span>
        <div className="min-w-0">
          <h3 className="truncate font-mono text-sm font-bold tracking-tight">
            {db.database_name}
          </h3>
          <p className="text-muted-foreground text-xs">
            key física: <code className="bg-muted rounded px-1 py-0.5">{db.database_key}</code>
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {db.plate_prefix && (
            <Badge variant="outline" className="font-mono">
              prefijo {db.plate_prefix}
            </Badge>
          )}
          <Badge variant="info" className="uppercase">
            {db.connection_type}
          </Badge>
          <Badge variant="outline" className="gap-1">
            <Truck className="h-3 w-3" aria-hidden />
            {db.vehicle_count}
          </Badge>
          <Badge variant={db.is_active ? 'success' : 'outline'}>
            {db.is_active ? 'Activa' : 'Inactiva'}
          </Badge>
          <ChevronDown
            className={cn(
              'text-muted-foreground h-4 w-4 transition-transform duration-300',
              open && 'rotate-180',
            )}
            aria-hidden
          />
        </div>
      </button>

      <div
        id={`database-content-${db.id}`}
        className={cn(
          'grid overflow-hidden transition-[grid-template-rows] duration-300 ease-out',
          open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className="min-h-0 overflow-hidden">
          <Separator />
          {/* Credenciales: solo metadata, el password jamás llega al frontend */}
          <section aria-label={`Credenciales de ${db.database_name}`} className="px-5 py-3">
            <h4 className="text-muted-foreground mb-2 text-[11px] font-semibold uppercase tracking-wider">
              Credenciales · pool de rotación
            </h4>
            {db.credentials.length === 0 ? (
              <p className="text-muted-foreground text-sm">Sin credenciales registradas.</p>
            ) : (
              <ul className="space-y-1.5">
                {db.credentials.map((cred) => (
                  <li
                    key={cred.id}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-gray-50/80 px-3 py-2"
                  >
                    <KeyRound
                      className={cn(
                        'h-3.5 w-3.5 shrink-0',
                        cred.is_active ? 'text-accent-yellow' : 'text-brand-clear',
                      )}
                      aria-hidden
                    />
                    <span className="font-mono text-xs font-semibold">{cred.username}</span>
                    <span aria-hidden className="text-brand-clear select-none tracking-widest">
                      ••••••••
                    </span>
                    {cred.label && (
                      <span className="text-muted-foreground text-xs italic">{cred.label}</span>
                    )}
                    <span className="text-muted-foreground ml-auto text-[11px]">
                      uso: {timeAgo(cred.last_used_at)}
                    </span>
                    <Badge variant={cred.is_active ? 'success' : 'outline'}>
                      {cred.is_active ? 'Activa' : 'Inactiva'}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Reglas por categoría */}
          {db.rules.length > 0 && (
            <>
              <Separator />
              <section aria-label={`Reglas de ${db.database_name}`} className="px-5 py-3">
                <h4 className="text-muted-foreground mb-2 text-[11px] font-semibold uppercase tracking-wider">
                  Reglas geotab
                </h4>
                <div className="space-y-4">
                  {operationRules.length > 0 && (
                    <div className="overflow-hidden rounded-md border">
                      <div className="bg-accent-blue/5 flex items-center gap-2 border-b px-3 py-2">
                        <Gauge className="text-accent-blue h-3.5 w-3.5" aria-hidden />
                        <h5 className="text-xs font-bold">Operación</h5>
                      </div>
                      {motorTypes.map((motorType) => {
                        const rules = operationRules.filter(
                          (rule) => (rule.motor_type ?? 'Todos los motores') === motorType,
                        );
                        const rows = [
                          ...RPM_BANDS,
                          ...(rules.some((rule) => !rule.band)
                            ? [{ key: '__without_band', label: 'Sin banda declarada' }]
                            : []),
                        ];

                        return (
                          <div key={motorType} className="[&+&]:border-t">
                            <div className="bg-muted/40 px-3 py-1.5 text-[11px] font-semibold">
                              Motor: {motorType}
                            </div>
                            <Table>
                              <TableHeader>
                                <TableRow>
                                  <TableHead className="w-[28%]">Banda</TableHead>
                                  <TableHead className="w-[36%]">Ascenso</TableHead>
                                  <TableHead className="w-[36%]">Descenso</TableHead>
                                </TableRow>
                              </TableHeader>
                              <TableBody>
                                {rows.map((band) => {
                                  const bandRules = rules.filter((rule) =>
                                    band.key === '__without_band'
                                      ? !rule.band
                                      : rule.band === band.key,
                                  );
                                  return (
                                    <TableRow key={band.key}>
                                      <TableCell className="font-medium">{band.label}</TableCell>
                                      <TableCell>
                                        <RuleNames
                                          rules={bandRules.filter((rule) => !rule.is_descenso)}
                                        />
                                      </TableCell>
                                      <TableCell>
                                        <RuleNames
                                          rules={bandRules.filter((rule) => rule.is_descenso)}
                                        />
                                      </TableCell>
                                    </TableRow>
                                  );
                                })}
                              </TableBody>
                            </Table>
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {safeHabitRules.length > 0 && (
                    <div className="overflow-hidden rounded-md border">
                      <div className="bg-accent-lime/10 flex items-center gap-2 border-b px-3 py-2">
                        <ShieldCheck className="h-3.5 w-3.5 text-[#3F5E00]" aria-hidden />
                        <h5 className="text-xs font-bold">Hábitos seguros</h5>
                      </div>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="w-[34%]">Descripción</TableHead>
                            <TableHead>Regla</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {safeHabitRules
                            .slice()
                            .sort((a, b) => {
                              const aIndex = SAFE_HABIT_DESCRIPTIONS.indexOf(
                                a.description as (typeof SAFE_HABIT_DESCRIPTIONS)[number],
                              );
                              const bIndex = SAFE_HABIT_DESCRIPTIONS.indexOf(
                                b.description as (typeof SAFE_HABIT_DESCRIPTIONS)[number],
                              );
                              return (
                                (aIndex < 0 ? Number.MAX_SAFE_INTEGER : aIndex) -
                                  (bIndex < 0 ? Number.MAX_SAFE_INTEGER : bIndex) ||
                                a.name.localeCompare(b.name, 'es')
                              );
                            })
                            .map((rule) => (
                              <TableRow key={rule.id}>
                                <TableCell className="font-medium">
                                  {rule.description ?? (
                                    <span className="text-muted-foreground">Sin descripción</span>
                                  )}
                                </TableCell>
                                <TableCell>
                                  <RuleNames rules={[rule]} />
                                </TableCell>
                              </TableRow>
                            ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </article>
  );
}
