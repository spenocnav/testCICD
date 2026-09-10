'use client';

import * as React from 'react';
import { History, Loader2, Lock, RotateCcw, ShieldAlert, TriangleAlert } from 'lucide-react';
import { useFieldArray, useForm, type UseFormRegister } from 'react-hook-form';
import { toast } from 'sonner';

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
import { Label } from '@/components/ui/label';
import { extractErrorMessage } from '@/lib/api-client';
import {
  useCalificacionConfig,
  useCalificacionConfigHistory,
  useResetCalificacionConfig,
  useSaveCalificacionConfig,
} from '@/lib/reportes';
import type { CalificacionCalibracion, CalificacionPenalizacion } from '@/lib/types';
import { cn } from '@/lib/utils';

/* ------------------------------------------------------------------ *
 * Contrato de campos
 * ------------------------------------------------------------------ */

/**
 * Campos que el API recibe como fracción 0..1 y que aquí se muestran y se
 * editan en porcentaje. Que el usuario tenga que escribir `0.3` para decir
 * «30 %» es una fuente de errores caros: un decimal mal puesto recalibra el
 * puntaje de toda una flota.
 */
const PERCENT_FIELDS = [
  'peso_qhs',
  'peso_qho',
  'peso_eficiente',
  'peso_ralenti',
  'peso_exceso_rpm',
  'efic_target',
  'ralenti_target',
  'ralenti_max',
] as const;

type PercentKey = (typeof PERCENT_FIELDS)[number];

/**
 * Campos que viajan tal cual: topes, agravación y umbrales en puntos.
 *
 * A diferencia de `PERCENT_FIELDS`, que además se recorre en tiempo de
 * ejecución para construir `PERCENT_KEY_SET`, de estos solo se necesita el
 * tipo. Declararlos como arreglo obligaba a enviar siete cadenas al navegador
 * que nadie leía.
 */
type AbsoluteKey =
  | 'eventos_cap'
  | 'rpm_cap_comercial_1000km'
  | 'rpm_cap_vocacional_100h'
  | 'rpm_high_weight'
  | 'umbral_en_riesgo'
  | 'umbral_cumple'
  | 'exposicion_minima_km_dia'
  | 'exposicion_minima_horas_dia'
  | 'qhs_default_weight';
type ScalarKey = PercentKey | AbsoluteKey;

const PERCENT_KEY_SET: ReadonlySet<string> = new Set<string>(PERCENT_FIELDS);

function isPercentKey(key: ScalarKey): key is PercentKey {
  return PERCENT_KEY_SET.has(key);
}

interface FieldSpec {
  key: ScalarKey;
  label: string;
  /** Sufijo visible: la unidad real del campo, no una decoración. */
  unit: string;
  hint: string;
  min: number;
  max?: number;
  /** `true` = el mínimo no se acepta (topes > 0). */
  exclusiveMin?: boolean;
  step: string;
  decimals: number;
}

interface FieldGroup {
  title: string;
  description: string;
  fields: FieldSpec[];
  /** Los campos cuyo total debe ser 100 %, para el marcador en vivo. */
  sumOf?: PercentKey[];
  columns?: 2 | 3;
}

const FIELD_GROUPS: FieldGroup[] = [
  {
    title: 'Reparto general',
    description:
      'Cuánto pesa la seguridad y cuánto la operación en el Q General. Deben sumar 100 %.',
    sumOf: ['peso_qhs', 'peso_qho'],
    columns: 2,
    fields: [
      {
        key: 'peso_qhs',
        label: 'Hábitos seguros (Q.H. Seguros)',
        unit: '%',
        hint: 'Eventos de conducción por unidad de exposición.',
        min: 0,
        max: 100,
        step: '0.1',
        decimals: 1,
      },
      {
        key: 'peso_qho',
        label: 'Hábitos de operación (Q.H. Operación)',
        unit: '%',
        hint: 'Cómo se usa el motor: rango eficiente, ralentí y excesos de RPM.',
        min: 0,
        max: 100,
        step: '0.1',
        decimals: 1,
      },
    ],
  },
  {
    title: 'Dentro de operación',
    description:
      'Cómo se reparte el bloque de operación entre sus tres componentes. Deben sumar 100 %.',
    sumOf: ['peso_eficiente', 'peso_ralenti', 'peso_exceso_rpm'],
    columns: 3,
    fields: [
      {
        key: 'peso_eficiente',
        label: 'Rango eficiente',
        unit: '%',
        hint: 'Tiempo del motor dentro del rango económico.',
        min: 0,
        max: 100,
        step: '0.1',
        decimals: 1,
      },
      {
        key: 'peso_ralenti',
        label: 'Ralentí',
        unit: '%',
        hint: 'Tiempo de motor encendido sin operar.',
        min: 0,
        max: 100,
        step: '0.1',
        decimals: 1,
      },
      {
        key: 'peso_exceso_rpm',
        label: 'Excesos de RPM',
        unit: '%',
        hint: 'Densidad de excesos de RPM del vehículo.',
        min: 0,
        max: 100,
        step: '0.1',
        decimals: 1,
      },
    ],
  },
  {
    title: 'Metas y ventanas',
    description:
      'Las metas de tiempo de cada componente. El ralentí usa una ventana: hasta el objetivo vale 100 y en el máximo vale 0.',
    columns: 3,
    fields: [
      {
        key: 'efic_target',
        label: 'Meta de tiempo eficiente',
        unit: '%',
        hint: 'Con este porcentaje de tiempo en rango eficiente el componente vale 100.',
        min: 0,
        max: 100,
        step: '0.1',
        decimals: 1,
      },
      {
        key: 'ralenti_target',
        label: 'Ralentí objetivo',
        unit: '%',
        hint: 'Hasta aquí el componente de ralentí vale 100.',
        min: 0,
        max: 100,
        step: '0.1',
        decimals: 1,
      },
      {
        key: 'ralenti_max',
        label: 'Ralentí máximo',
        unit: '%',
        hint: 'Desde aquí el componente de ralentí vale 0. Debe ser mayor que el objetivo.',
        min: 0,
        max: 100,
        step: '0.1',
        decimals: 1,
      },
    ],
  },
  {
    title: 'Topes de densidad de eventos',
    description:
      'Cuántos eventos por unidad de exposición llevan un componente a 0. Un tope más alto es más indulgente.',
    columns: 2,
    fields: [
      {
        key: 'eventos_cap',
        label: 'Tope de eventos de seguridad',
        unit: 'ev. ponderados / 1000 km',
        hint: 'Con esta densidad de eventos ponderados, Q.H. Seguros llega a 0.',
        min: 0,
        exclusiveMin: true,
        step: '0.1',
        decimals: 1,
      },
      {
        key: 'rpm_high_weight',
        label: 'Agravación sobre la gobernada',
        unit: '×',
        hint: 'Cuánto más pesa un exceso que además supera la velocidad gobernada del motor. 1 = igual que los demás.',
        min: 1,
        step: '0.1',
        decimals: 2,
      },
      {
        key: 'rpm_cap_comercial_1000km',
        label: 'Tope de excesos de RPM · comercial',
        unit: 'excesos / 1000 km',
        hint: 'Vehículos que se miden por kilómetros recorridos.',
        min: 0,
        exclusiveMin: true,
        step: '0.1',
        decimals: 1,
      },
      {
        key: 'rpm_cap_vocacional_100h',
        label: 'Tope de excesos de RPM · vocacional',
        unit: 'excesos / 100 h ECM',
        hint: 'Vehículos que se miden por horas de motor.',
        min: 0,
        exclusiveMin: true,
        step: '0.1',
        decimals: 1,
      },
    ],
  },
  {
    title: 'Umbrales de estado',
    description:
      'Los cortes en puntos que deciden si un vehículo no cumple, está en riesgo o cumple.',
    columns: 2,
    fields: [
      {
        key: 'umbral_en_riesgo',
        label: 'No cumple por debajo de',
        unit: 'pts',
        hint: 'Debajo de este puntaje el estado es «No cumple».',
        min: 0,
        max: 100,
        step: '1',
        decimals: 0,
      },
      {
        key: 'umbral_cumple',
        label: 'Cumple desde',
        unit: 'pts',
        hint: 'Desde este puntaje el estado es «Cumple». Entre los dos, «En riesgo».',
        min: 0,
        max: 100,
        step: '1',
        decimals: 0,
      },
    ],
  },
  {
    title: 'Exposición mínima para calificar',
    description:
      'Cuánto tiene que operar un vehículo para que su puntaje signifique algo. Se expresa por día del periodo consultado, así que el mínimo se ajusta solo a un rango de un mes o de tres. 0 desactiva la regla.',
    columns: 2,
    fields: [
      {
        key: 'exposicion_minima_km_dia',
        label: 'Mínimo · comercial',
        unit: 'km / día',
        hint: 'Vehículos que se miden por kilómetros. Por debajo del mínimo el vehículo no recibe puntaje y no entra en el promedio; sigue en la tabla con el puntaje que habría tenido.',
        min: 0,
        step: '0.5',
        decimals: 2,
      },
      {
        key: 'exposicion_minima_horas_dia',
        label: 'Mínimo · vocacional',
        unit: 'h ECM / día',
        hint: 'Vehículos que se miden por horas de motor.',
        min: 0,
        step: '0.1',
        decimals: 2,
      },
    ],
  },
];

const ALL_FIELDS: FieldSpec[] = FIELD_GROUPS.flatMap((group) => group.fields);

const DEFAULT_WEIGHT_SPEC: FieldSpec = {
  key: 'qhs_default_weight',
  label: 'Tipo de evento no listado',
  unit: '×',
  hint: 'Severidad que se aplica a un tipo de evento que no aparece en la lista.',
  min: 0,
  step: '0.1',
  decimals: 2,
};

/* ------------------------------------------------------------------ *
 * Formato y parseo
 * ------------------------------------------------------------------ */

function fmtNum(value: number, decimals: number): string {
  return value.toLocaleString('es-CO', {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

/** Valor por defecto ya en la unidad que ve el usuario.
 *
 * Devuelve `null` cuando el API no publica ese campo: los parámetros añadidos
 * después de la primera versión del contrato son opcionales en el tipo, y
 * pintar «NaN» donde debía ir «por defecto: 5 km/día» es peor que no decir
 * nada. */
function defaultDisplay(spec: FieldSpec, defaults: CalificacionCalibracion): string | null {
  const raw = defaults[spec.key];
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return null;
  const value = isPercentKey(spec.key) ? raw * 100 : raw;
  return `${fmtNum(value, spec.decimals)} ${spec.unit}`;
}

/**
 * Acepta coma o punto como separador decimal: en es-CO se escribe «33,3» y
 * rechazarlo se leería como un error del sistema, no del usuario.
 */
function parseNumber(text: string): number | null {
  const normalized = text.trim().replace(/\s/g, '').replace(',', '.');
  if (normalized === '') return null;
  if (!/^-?\d*\.?\d*$/.test(normalized)) return null;
  const value = Number(normalized);
  return Number.isFinite(value) ? value : null;
}

/** Redondeo a 6 decimales: evita mandar 0,30000000000000004 al backend. */
function toFraction(percent: number): number {
  return Math.round((percent / 100) * 1e6) / 1e6;
}

/**
 * Resumen legible de en qué se aparta una versión guardada de los valores por
 * defecto. El backend guarda versiones completas, no diffs: comparar contra el
 * defecto es lo que responde «qué se cambió» sin inventar un historial de
 * campo por campo que el API no publica.
 */
function diffContraDefaults(
  config: CalificacionCalibracion,
  defaults: CalificacionCalibracion,
): string[] {
  const out: string[] = [];
  for (const spec of [...ALL_FIELDS, DEFAULT_WEIGHT_SPEC]) {
    const value = config[spec.key];
    const porDefecto = defaults[spec.key];
    // Un parámetro que esta versión del API no publica no es "una diferencia":
    // no hay contra qué compararlo.
    if (typeof value !== 'number' || typeof porDefecto !== 'number') continue;
    if (Math.abs(value - porDefecto) <= 1e-9) continue;
    const shown = isPercentKey(spec.key) ? value * 100 : value;
    out.push(`${spec.label} ${fmtNum(shown, spec.decimals)} ${spec.unit}`);
  }
  const pesosDistintos = Object.keys({
    ...(defaults.qhs_event_weights ?? {}),
    ...(config.qhs_event_weights ?? {}),
  }).filter(
    (evento) =>
      (config.qhs_event_weights?.[evento] ?? null) !==
      (defaults.qhs_event_weights?.[evento] ?? null),
  );
  if (pesosDistintos.length > 0) {
    out.push(
      pesosDistintos.length === 1
        ? 'severidad de 1 tipo de evento'
        : `severidad de ${pesosDistintos.length} tipos de evento`,
    );
  }
  if (
    config.promedio_ponderado != null &&
    config.promedio_ponderado !== (defaults.promedio_ponderado ?? true)
  ) {
    out.push(config.promedio_ponderado ? 'promedio ponderado' : 'promedio simple');
  }
  // Se comparan solo las apagadas y no el mapa entero: una versión guardada
  // antes de que existiera la penalización no la menciona, y eso significa
  // «activa», igual que el default. Marcarla como diferencia sería mentir.
  const apagadas = Object.entries(config.penalizaciones ?? {})
    .filter(([, activa]) => activa === false)
    .map(([code]) => code);
  if (apagadas.length > 0) {
    out.push(
      apagadas.length === 1
        ? '1 penalización desactivada'
        : `${apagadas.length} penalizaciones desactivadas`,
    );
  }
  return out;
}

function inputValue(
  spec: FieldSpec,
  valores: CalificacionCalibracion,
  defaults: CalificacionCalibracion,
): string {
  // Un parámetro que el API no publica cae al valor del sistema, nunca a 0: en
  // la exposición mínima, 0 significa "regla apagada" y sería una decisión que
  // nadie tomó.
  const guardado = valores[spec.key];
  const porDefecto = defaults[spec.key];
  const raw =
    typeof guardado === 'number' && Number.isFinite(guardado)
      ? guardado
      : typeof porDefecto === 'number' && Number.isFinite(porDefecto)
        ? porDefecto
        : 0;
  const value = isPercentKey(spec.key) ? raw * 100 : raw;
  // Se normaliza el ruido de coma flotante que produce la conversión (0.7*100).
  return String(Math.round(value * 1e6) / 1e6);
}

/* ------------------------------------------------------------------ *
 * Estado del formulario
 * ------------------------------------------------------------------ */

interface EventoRow {
  evento: string;
  peso: string;
}

/**
 * Todos los campos son texto: el usuario escribe, y la conversión a número (y
 * de porcentaje a fracción) ocurre una sola vez, al validar. Un `number` en el
 * estado obliga a decidir qué es un input vacío y produce `NaN` visibles.
 */
type FormValues = Record<ScalarKey, string> & {
  eventos: EventoRow[];
  /** Modo de agregación de la cifra de flota: ponderado por exposición o
   * promedio simple. Booleano por el mismo motivo que las penalizaciones: no
   * hay nada que parsear ni un estado intermedio inválido. */
  promedio_ponderado: boolean;
  /** Booleano por código de penalización. No es texto: no hay nada que parsear
   * ni un estado intermedio inválido, así que el interruptor manda el valor
   * final directo. */
  penalizaciones: Record<string, boolean>;
};

function toFormValues(config: {
  valores: CalificacionCalibracion;
  defaults: CalificacionCalibracion;
  penalizaciones_disponibles: CalificacionPenalizacion[];
}): FormValues {
  const scalars = {} as Record<ScalarKey, string>;
  for (const spec of [...ALL_FIELDS, DEFAULT_WEIGHT_SPEC]) {
    scalars[spec.key] = inputValue(spec, config.valores, config.defaults);
  }
  // Se listan los tipos de la calibración vigente Y los del sistema: si el
  // backend agrega un tipo nuevo, aquí aparece con su valor por defecto.
  const eventNames = Array.from(
    new Set([
      ...Object.keys(config.defaults.qhs_event_weights ?? {}),
      ...Object.keys(config.valores.qhs_event_weights ?? {}),
    ]),
  ).sort((a, b) => a.localeCompare(b, 'es-CO'));

  // Se parte del catálogo del backend Y de lo que ya tiene guardado la flota.
  // Un código guardado que el catálogo de esta versión no conoce no se pinta,
  // pero se conserva: guardar sin él lo borraría y lo volvería a activar en
  // silencio, que es justo lo que el cliente creía haber apagado.
  const penalizaciones: Record<string, boolean> = {};
  for (const code of Object.keys(config.valores.penalizaciones ?? {})) {
    penalizaciones[code] = config.valores.penalizaciones?.[code] !== false;
  }
  for (const penalizacion of config.penalizaciones_disponibles) {
    penalizaciones[penalizacion.code] =
      config.valores.penalizaciones?.[penalizacion.code] ??
      config.defaults.penalizaciones?.[penalizacion.code] ??
      // Sin dato en ninguna de las dos fuentes manda el default del registro,
      // que es «activa»: una penalización nueva aplica salvo decisión expresa.
      true;
  }

  return {
    ...scalars,
    penalizaciones,
    // Ausente en las dos fuentes manda el histórico: ponderado. Un API anterior
    // a la función no debe verse como "esta flota eligió el promedio simple".
    promedio_ponderado:
      config.valores.promedio_ponderado ?? config.defaults.promedio_ponderado ?? true,
    eventos: eventNames.map((evento) => ({
      evento,
      peso: String(
        config.valores.qhs_event_weights?.[evento] ??
          config.defaults.qhs_event_weights?.[evento] ??
          config.valores.qhs_default_weight,
      ),
    })),
  };
}

interface Validation {
  /** Error por campo escalar, para pintarlo junto al input. */
  fieldErrors: Partial<Record<ScalarKey, string>>;
  /** Error por fila de evento, indexado igual que `eventos`. */
  eventErrors: Record<number, string>;
  /** Reglas cruzadas incumplidas, en el orden en que se muestran. */
  reasons: string[];
  /** Suma en vivo (en %) de cada grupo de pesos. */
  sums: Record<string, number | null>;
  /** Listo para enviar; `null` mientras algo no cuadre. */
  payload: CalificacionCalibracion | null;
}

function validate(values: FormValues): Validation {
  const fieldErrors: Partial<Record<ScalarKey, string>> = {};
  const eventErrors: Record<number, string> = {};
  const reasons: string[] = [];
  // Parcial a propósito: un campo vacío o fuera de rango no entra, y las
  // reglas cruzadas solo se evalúan cuando sus dos extremos existen.
  const parsed: Partial<Record<ScalarKey, number>> = {};

  for (const spec of [...ALL_FIELDS, DEFAULT_WEIGHT_SPEC]) {
    const value = parseNumber(values[spec.key] ?? '');
    if (value == null) {
      fieldErrors[spec.key] = 'Escribe un número.';
      continue;
    }
    if (spec.exclusiveMin && value <= spec.min) {
      fieldErrors[spec.key] = `Debe ser mayor que ${fmtNum(spec.min, spec.decimals)}.`;
      continue;
    }
    if (!spec.exclusiveMin && value < spec.min) {
      fieldErrors[spec.key] = `No puede ser menor que ${fmtNum(spec.min, spec.decimals)}.`;
      continue;
    }
    if (spec.max != null && value > spec.max) {
      fieldErrors[spec.key] = `No puede pasar de ${fmtNum(spec.max, spec.decimals)}.`;
      continue;
    }
    parsed[spec.key] = value;
  }

  const sums: Record<string, number | null> = {};
  for (const group of FIELD_GROUPS) {
    if (!group.sumOf) continue;
    const complete = group.sumOf.every((key) => parsed[key] != null);
    const total = complete
      ? group.sumOf.reduce((acc, key) => acc + (parsed[key] as number), 0)
      : null;
    sums[group.title] = total;
    if (total != null && Math.abs(total - 100) > 0.01) {
      reasons.push(`«${group.title}» debe sumar 100 %; suma ${fmtNum(total, 2)} %.`);
    }
  }

  if (parsed.ralenti_target != null && parsed.ralenti_max != null) {
    if (parsed.ralenti_target >= parsed.ralenti_max) {
      reasons.push('El ralentí objetivo debe ser menor que el ralentí máximo.');
    }
  }
  if (parsed.umbral_en_riesgo != null && parsed.umbral_cumple != null) {
    if (parsed.umbral_en_riesgo >= parsed.umbral_cumple) {
      reasons.push('El umbral de «No cumple» debe ser menor que el de «Cumple».');
    }
  }

  const weights: Record<string, number> = {};
  const seen = new Set<string>();
  const rows = values.eventos ?? [];
  rows.forEach((row, index) => {
    const name = row.evento.trim();
    if (name === '') {
      eventErrors[index] = 'Escribe el tipo de evento.';
      return;
    }
    if (seen.has(name)) {
      eventErrors[index] = 'Este tipo de evento está repetido.';
      return;
    }
    const weight = parseNumber(row.peso);
    if (weight == null || weight < 0) {
      eventErrors[index] = 'La severidad debe ser un número mayor o igual a 0.';
      return;
    }
    seen.add(name);
    weights[name] = weight;
  });

  const hasFieldErrors = Object.keys(fieldErrors).length > 0;
  const hasEventErrors = Object.keys(eventErrors).length > 0;
  if (hasFieldErrors) reasons.unshift('Hay campos vacíos o fuera de rango.');
  if (hasEventErrors) reasons.push('Revisa la severidad por tipo de evento.');

  // Sin motivos pendientes, los 16 campos están parseados y en rango: recién
  // ahí se puede tratar `parsed` como completo.
  const ok = reasons.length === 0;
  const p = parsed as Record<ScalarKey, number>;
  const payload: CalificacionCalibracion | null = ok
    ? {
        peso_qhs: toFraction(p.peso_qhs),
        peso_qho: toFraction(p.peso_qho),
        peso_eficiente: toFraction(p.peso_eficiente),
        peso_ralenti: toFraction(p.peso_ralenti),
        peso_exceso_rpm: toFraction(p.peso_exceso_rpm),
        efic_target: toFraction(p.efic_target),
        ralenti_target: toFraction(p.ralenti_target),
        ralenti_max: toFraction(p.ralenti_max),
        eventos_cap: p.eventos_cap,
        rpm_cap_comercial_1000km: p.rpm_cap_comercial_1000km,
        rpm_cap_vocacional_100h: p.rpm_cap_vocacional_100h,
        rpm_high_weight: p.rpm_high_weight,
        umbral_en_riesgo: p.umbral_en_riesgo,
        umbral_cumple: p.umbral_cumple,
        exposicion_minima_km_dia: p.exposicion_minima_km_dia,
        exposicion_minima_horas_dia: p.exposicion_minima_horas_dia,
        promedio_ponderado: values.promedio_ponderado !== false,
        qhs_event_weights: weights,
        qhs_default_weight: p.qhs_default_weight,
        // Se envía el mapa completo, no solo las apagadas: el backend guarda
        // versiones completas y una clave ausente cae al default, así que
        // omitir las activas dejaría el guardado a merced de que ese default
        // no cambie nunca.
        penalizaciones: { ...(values.penalizaciones ?? {}) },
      }
    : null;

  return { fieldErrors, eventErrors, reasons, sums, payload };
}

/* ------------------------------------------------------------------ *
 * Piezas de presentación
 * ------------------------------------------------------------------ */

function fmtFecha(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString('es-CO', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function Seccion({
  title,
  description,
  children,
  badge,
}: {
  title: string;
  description: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="border-border rounded-lg border p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="font-heading text-sm font-bold tracking-tight">{title}</h3>
          <p className="text-muted-foreground mt-0.5 text-xs leading-snug">{description}</p>
        </div>
        {badge}
      </div>
      {children}
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Diálogo
 * ------------------------------------------------------------------ */

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Flota a calibrar. Debe ser UNA: la calibración es por flota. */
  fleetId: string;
  fleetName?: string | null;
}

export function CalificacionCalibracionDialog({ open, onOpenChange, fleetId, fleetName }: Props) {
  const configQ = useCalificacionConfig(fleetId, open);
  const saveMutation = useSaveCalificacionConfig();
  const resetMutation = useResetCalificacionConfig();

  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [confirmReset, setConfirmReset] = React.useState(false);
  const historyQ = useCalificacionConfigHistory(fleetId, open && historyOpen);

  const config = configQ.data;

  const form = useForm<FormValues>({
    defaultValues: { eventos: [], penalizaciones: {} } as unknown as FormValues,
  });
  const eventos = useFieldArray({ control: form.control, name: 'eventos' });

  // Se rehidrata al abrir y cuando llega/cambia la config del servidor. La
  // marca de tiempo entra en la dependencia para que un reset (que devuelve los
  // valores por defecto) repueble el formulario en vez de dejar lo tecleado.
  React.useEffect(() => {
    if (!open || !config) return;
    form.reset(toFormValues(config));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, config?.fleet_id, config?.origen, config?.actualizado_en]);

  React.useEffect(() => {
    if (!open) {
      setConfirmReset(false);
      setHistoryOpen(false);
      saveMutation.reset();
      resetMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const values = form.watch();
  const validation = React.useMemo(
    () => (config ? validate(values) : null),
    // `values` es un objeto nuevo en cada render de `watch`; se compara por
    // contenido para no revalidar en cada pintado.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, JSON.stringify(values)],
  );

  const busy = saveMutation.isPending || resetMutation.isPending;
  const blocked = validation?.reasons ?? [];
  // Se cuentan solo las del catálogo: una clave guardada que esta versión de la
  // web no sabe pintar tampoco se puede resumir en la insignia.
  const penalizacionesApagadas = (config?.penalizaciones_disponibles ?? []).filter(
    (penalizacion) => values.penalizaciones?.[penalizacion.code] === false,
  ).length;
  const canSave = !!validation?.payload && !busy;

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const payload = validation?.payload;
    if (!payload) return;
    try {
      await saveMutation.mutateAsync({ fleetId, valores: payload });
      toast.success('Calibración guardada. La calificación se está recalculando.');
      onOpenChange(false);
    } catch {
      // El mensaje del backend se muestra dentro del diálogo, literal.
    }
  };

  const onReset = async () => {
    try {
      await resetMutation.mutateAsync(fleetId);
      toast.success('Calibración restablecida a los valores por defecto.');
      setConfirmReset(false);
      await configQ.refetch();
    } catch {
      // Idem: el error se muestra abajo con el texto del backend.
    }
  };

  const actualizado = fmtFecha(config?.actualizado_en);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Calibrar la calificación{fleetName ? ` · ${fleetName}` : ''}</DialogTitle>
          <DialogDescription>
            Estos parámetros solo aplican a esta flota. Ajustan cómo se reparte y se escala la nota,
            no de dónde salen los datos.
          </DialogDescription>
        </DialogHeader>

        {configQ.isLoading && (
          <p className="text-muted-foreground flex items-center gap-2 py-8 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Cargando la calibración de la flota…
          </p>
        )}

        {configQ.isError && (
          <p className="text-destructive py-6 text-sm">
            {extractErrorMessage(configQ.error, 'No se pudo cargar la calibración.')}
          </p>
        )}

        {config && validation && (
          <form onSubmit={onSubmit} noValidate className="space-y-4">
            {/* Procedencia: qué calibración está vigente y quién la dejó así. */}
            <div className="bg-muted/40 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-3 py-2 text-xs">
              <Badge variant={config.origen === 'flota' ? 'info' : 'outline'}>
                {config.origen === 'flota'
                  ? 'Calibración propia de la flota'
                  : 'Valores por defecto'}
              </Badge>
              {actualizado && (
                <span className="text-muted-foreground">
                  Último cambio: {actualizado}
                  {config.actualizado_por ? ` · ${config.actualizado_por}` : ''}
                </span>
              )}
              <button
                type="button"
                onClick={() => setHistoryOpen((current) => !current)}
                className="text-accent-blue ml-auto inline-flex items-center gap-1.5 font-medium hover:underline"
              >
                <History className="h-3.5 w-3.5" aria-hidden />
                {historyOpen ? 'Ocultar historial' : 'Ver historial'}
              </button>
            </div>

            {historyOpen && (
              <div className="border-border max-h-52 overflow-y-auto rounded-lg border p-3 text-xs">
                {historyQ.isLoading && <p className="text-muted-foreground">Cargando historial…</p>}
                {historyQ.isError && (
                  <p className="text-destructive">
                    {extractErrorMessage(historyQ.error, 'No se pudo cargar el historial.')}
                  </p>
                )}
                {historyQ.data?.length === 0 && (
                  <p className="text-muted-foreground">
                    Esta flota nunca se ha recalibrado: sigue con los valores por defecto.
                  </p>
                )}
                <ul className="space-y-2">
                  {(historyQ.data ?? []).map((entry, index) => {
                    const diffs = entry.config
                      ? diffContraDefaults(entry.config, config.defaults)
                      : [];
                    return (
                      <li
                        key={entry.id ?? `${entry.creado_en}-${index}`}
                        className="border-border border-b pb-2 last:border-0 last:pb-0"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">
                            {fmtFecha(entry.creado_en) ?? 'Fecha no registrada'}
                          </span>
                          <span className="text-muted-foreground">
                            {entry.actor_email ?? entry.actor_user_id ?? 'autor no registrado'}
                          </span>
                          <Badge variant={entry.es_reset ? 'outline' : 'info'}>
                            {entry.es_reset ? 'Restablecido al defecto' : 'Calibración guardada'}
                          </Badge>
                          {entry.valida === false && (
                            <Badge variant="warning">Ya no es aplicable</Badge>
                          )}
                        </div>
                        {diffs.length > 0 && (
                          <p className="text-muted-foreground mt-1">
                            Distinto del defecto en: {diffs.join(' · ')}
                          </p>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            {/* Grupos de campos escalares. */}
            {FIELD_GROUPS.map((group) => {
              const total = group.sumOf ? validation.sums[group.title] : undefined;
              const sumOk = total != null && Math.abs(total - 100) <= 0.01;
              return (
                <Seccion
                  key={group.title}
                  title={group.title}
                  description={group.description}
                  badge={
                    group.sumOf ? (
                      <Badge variant={sumOk ? 'success' : 'destructive'}>
                        {total == null ? 'Suma incompleta' : `Suman ${fmtNum(total, 2)} %`}
                      </Badge>
                    ) : undefined
                  }
                >
                  <div
                    className={cn(
                      'grid gap-3',
                      group.columns === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-2',
                    )}
                  >
                    {group.fields.map((spec) => (
                      <CampoNumerico
                        key={spec.key}
                        spec={spec}
                        defaults={config.defaults}
                        error={validation.fieldErrors[spec.key]}
                        current={values[spec.key]}
                        register={form.register}
                      />
                    ))}
                  </div>
                </Seccion>
              );
            })}

            {/* Severidad por tipo de evento. */}
            <Seccion
              title="Severidad por tipo de evento"
              description="Cuánto pesa cada tipo de evento de hábitos seguros al contar la densidad por 1000 km. 1 = peso normal."
            >
              <div className="space-y-2">
                {eventos.fields.map((field, index) => (
                  <div key={field.id} className="flex items-start gap-2">
                    <Input
                      aria-label={`Tipo de evento ${index + 1}`}
                      className="flex-1"
                      {...form.register(`eventos.${index}.evento` as const)}
                    />
                    <div className="w-28">
                      <Input
                        aria-label={`Severidad del evento ${index + 1}`}
                        inputMode="decimal"
                        className={cn(validation.eventErrors[index] && 'border-destructive')}
                        {...form.register(`eventos.${index}.peso` as const)}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => eventos.remove(index)}
                    >
                      Quitar
                    </Button>
                  </div>
                ))}
                {Object.entries(validation.eventErrors).map(([index, message]) => (
                  <p key={index} role="alert" className="text-destructive text-xs font-medium">
                    Fila {Number(index) + 1}: {message}
                  </p>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => eventos.append({ evento: '', peso: '1' })}
                >
                  Agregar tipo de evento
                </Button>
              </div>

              <div className="mt-4 max-w-xs">
                <CampoNumerico
                  spec={DEFAULT_WEIGHT_SPEC}
                  defaults={config.defaults}
                  error={validation.fieldErrors.qhs_default_weight}
                  current={values.qhs_default_weight}
                  register={form.register}
                />
              </div>
            </Seccion>

            {/* Cómo se agrega la cifra de flota. Va aparte de los pesos porque
                no cambia el puntaje de ningún vehículo: cambia la pregunta que
                responde el gauge. */}
            <Seccion
              title="Cifra de la flota"
              description="Cómo se combinan los puntajes de los vehículos en el promedio general y en la evolución mensual. No cambia el puntaje de ningún vehículo."
              badge={
                values.promedio_ponderado === false ? (
                  <Badge variant="warning">Promedio simple</Badge>
                ) : undefined
              }
            >
              <div
                className={cn(
                  'border-border flex items-start gap-3 rounded-lg border p-3',
                  values.promedio_ponderado === false && 'bg-muted/40',
                )}
              >
                <Perilla
                  activa={values.promedio_ponderado !== false}
                  etiqueta="Ponderar por exposición"
                  disabled={busy}
                  describedBy="calib-agregacion-desc"
                  onChange={(activa) =>
                    form.setValue('promedio_ponderado', activa, { shouldDirty: true })
                  }
                />
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">Ponderar por exposición</span>
                    <Badge variant={values.promedio_ponderado !== false ? 'success' : 'warning'}>
                      {values.promedio_ponderado !== false ? 'Ponderado' : 'Simple'}
                    </Badge>
                  </div>
                  <p
                    id="calib-agregacion-desc"
                    className="text-muted-foreground text-xs leading-snug"
                  >
                    <strong className="text-foreground font-semibold">Ponderado</strong> — un
                    vehículo que hizo 5.000 km pesa más que uno que hizo 500, porque aportó más
                    operación. Responde «¿cómo se condujo esta flota?».
                    <br />
                    <strong className="text-foreground font-semibold">Simple</strong> — un
                    vehículo, un voto, sin importar cuánto operó. Responde «¿cómo van mis
                    vehículos?», y es lo razonable en flotas pequeñas o muy dispares en
                    kilometraje, donde el ponderado deja la cifra describiendo sólo a los dos o
                    tres vehículos intensivos.
                  </p>
                  <p className="text-muted-foreground text-[11px] leading-snug">
                    En los dos modos entran los mismos vehículos: quién se califica lo deciden la
                    exposición mínima y las penalizaciones, no esta perilla.
                  </p>
                </div>
              </div>
            </Seccion>

            {/* Penalizaciones: van del lado ajustable porque son una decisión de
                política del cliente, no un dato del motor. La lista sale del
                catálogo del backend; si el API todavía no lo publica, la sección
                no se pinta en vez de inventar los códigos. */}
            {config.penalizaciones_disponibles.length > 0 && (
              <Seccion
                title="Penalizaciones"
                description="Reglas que anulan el puntaje de un vehículo cuando se incumplen, sin promediarlas con el resto."
                badge={
                  penalizacionesApagadas > 0 ? (
                    <Badge variant="warning">
                      {penalizacionesApagadas === 1
                        ? '1 desactivada'
                        : `${penalizacionesApagadas} desactivadas`}
                    </Badge>
                  ) : undefined
                }
              >
                {/* El aviso va ARRIBA de los interruptores a propósito: la duda
                    que resuelve —«¿apagarla me esconde los excesos?»— aparece
                    antes de apagar, no después. */}
                <div className="border-accent-blue/30 bg-accent-blue/5 mb-3 flex items-start gap-2 rounded-lg border p-3">
                  <ShieldAlert className="text-accent-blue mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                  <p className="text-muted-foreground text-xs leading-snug">
                    Desactivar una penalización no oculta nada: los excesos se siguen detectando,
                    contando y mostrando en el detalle del vehículo y en los reportes. Lo único que
                    deja de pasar es que anulen el puntaje.
                  </p>
                </div>

                <div className="space-y-2">
                  {config.penalizaciones_disponibles.map((penalizacion) => (
                    <InterruptorPenalizacion
                      key={penalizacion.code}
                      penalizacion={penalizacion}
                      activa={values.penalizaciones?.[penalizacion.code] !== false}
                      activaPorDefecto={
                        config.defaults.penalizaciones?.[penalizacion.code] !== false
                      }
                      disabled={busy}
                      onChange={(activa) =>
                        form.setValue(
                          'penalizaciones',
                          { ...(values.penalizaciones ?? {}), [penalizacion.code]: activa },
                          { shouldDirty: true },
                        )
                      }
                    />
                  ))}
                </div>
              </Seccion>
            )}

            {/* Frontera del módulo: lo que sale del motor no se negocia aquí. */}
            <section className="border-border bg-muted/30 flex items-start gap-3 rounded-lg border p-4">
              <Lock className="text-muted-foreground mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <div className="space-y-1 text-xs leading-snug">
                <p className="text-foreground font-semibold">Esto no se edita aquí</p>
                <p className="text-muted-foreground">
                  La velocidad gobernada, la sobrevelocidad máxima y los rangos de RPM salen de la
                  ficha del motor en Navi Vehículos: son datos del motor, no una preferencia de la
                  flota. Una penalización se puede desactivar, pero el límite contra el que se mide
                  no: para corregirlo hay que corregir la ficha del motor.
                </p>
              </div>
            </section>

            {/* Por qué no se puede guardar todavía, en vivo. */}
            {blocked.length > 0 && (
              <div className="border-destructive/40 bg-destructive/5 rounded-lg border p-3">
                <p className="text-destructive flex items-center gap-2 text-xs font-semibold">
                  <TriangleAlert className="h-4 w-4" aria-hidden />
                  Falta cuadrar esto antes de guardar
                </p>
                <ul className="text-destructive mt-1 list-disc space-y-0.5 pl-8 text-xs">
                  {blocked.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Mensaje del backend, literal: es el que explica qué regla falló. */}
            {saveMutation.isError && (
              <p role="alert" className="text-destructive text-sm font-medium">
                {extractErrorMessage(saveMutation.error, 'No se pudo guardar la calibración.')}
              </p>
            )}
            {resetMutation.isError && (
              <p role="alert" className="text-destructive text-sm font-medium">
                {extractErrorMessage(resetMutation.error, 'No se pudo restablecer la calibración.')}
              </p>
            )}

            <p className="text-muted-foreground text-xs leading-snug">
              Guardar recalcula el puntaje de todos los vehículos de esta flota en todo el
              histórico, no solo de aquí en adelante: las notas y los estados que ya se consultaron
              van a cambiar.
            </p>

            <DialogFooter className="flex-wrap gap-2">
              {confirmReset ? (
                <div className="mr-auto flex items-center gap-2">
                  <span className="text-xs">¿Volver a los valores por defecto?</span>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    disabled={busy}
                    onClick={onReset}
                  >
                    {resetMutation.isPending && (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    )}
                    Sí, restablecer
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmReset(false)}
                  >
                    Cancelar
                  </Button>
                </div>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="mr-auto"
                  disabled={busy || config.origen !== 'flota'}
                  title={
                    config.origen !== 'flota'
                      ? 'La flota ya usa los valores por defecto.'
                      : 'Borra la calibración de la flota y vuelve a los valores por defecto.'
                  }
                  onClick={() => setConfirmReset(true)}
                >
                  <RotateCcw className="h-4 w-4" aria-hidden />
                  Restablecer valores por defecto
                </Button>
              )}
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={busy}
              >
                Cancelar
              </Button>
              <Button
                type="submit"
                disabled={!canSave}
                title={blocked[0] ?? 'Guarda la calibración y recalcula el histórico de la flota.'}
              >
                {saveMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
                Guardar y recalcular
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ *
 * Interruptor de penalización
 * ------------------------------------------------------------------ */

/**
 * Un interruptor por penalización, con su nombre y su descripción a la vista.
 *
 * La descripción se muestra siempre en vez de esconderla tras un tooltip: lo
 * que hace esta perilla —dejar el puntaje del vehículo en 0— no es adivinable
 * desde el nombre, y apagarla por error cambia el histórico de toda la flota.
 */
/**
 * La perilla en sí, compartida por las penalizaciones y por el modo de
 * agregación. Es un `role="switch"` y no un checkbox porque no es un ítem de
 * una selección sino el estado encendido/apagado de una regla.
 */
function Perilla({
  activa,
  etiqueta,
  disabled,
  describedBy,
  onChange,
}: {
  activa: boolean;
  /** Nombre accesible del control; se lee con lector de pantalla. */
  etiqueta: string;
  disabled?: boolean;
  describedBy?: string;
  onChange: (activa: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={activa}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!activa)}
      className={cn(
        'focus-visible:ring-ring mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full',
        'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1',
        'disabled:cursor-not-allowed disabled:opacity-50',
        activa ? 'bg-brand-red' : 'bg-muted-foreground/40',
      )}
    >
      <span className="sr-only">{etiqueta}</span>
      <span
        aria-hidden
        className={cn(
          'h-4 w-4 rounded-full bg-white shadow transition-transform',
          activa ? 'translate-x-[18px]' : 'translate-x-0.5',
        )}
      />
    </button>
  );
}

function InterruptorPenalizacion({
  penalizacion,
  activa,
  activaPorDefecto,
  disabled,
  onChange,
}: {
  penalizacion: CalificacionPenalizacion;
  activa: boolean;
  activaPorDefecto: boolean;
  disabled?: boolean;
  onChange: (activa: boolean) => void;
}) {
  const descriptionId = `penalizacion-${penalizacion.code}-desc`;
  return (
    <div
      className={cn(
        'border-border flex items-start gap-3 rounded-lg border p-3',
        !activa && 'bg-muted/40',
      )}
    >
      <Perilla
        activa={activa}
        etiqueta={penalizacion.nombre}
        disabled={disabled}
        describedBy={descriptionId}
        onChange={onChange}
      />
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{penalizacion.nombre}</span>
          <Badge variant={activa ? 'success' : 'warning'}>
            {activa ? 'Activa' : 'Desactivada'}
          </Badge>
          {/* Solo se señala cuando difiere del defecto, igual que los campos
              numéricos: lo interesante es lo que esta flota decidió cambiar. */}
          {activa !== activaPorDefecto && (
            <span className="text-accent-blue text-[11px] font-medium">cambiado</span>
          )}
        </div>
        <p id={descriptionId} className="text-muted-foreground text-[11px] leading-snug">
          {penalizacion.descripcion}
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Campo
 * ------------------------------------------------------------------ */

function CampoNumerico({
  spec,
  defaults,
  error,
  current,
  register,
}: {
  spec: FieldSpec;
  defaults: CalificacionCalibracion;
  error?: string;
  current: string;
  register: UseFormRegister<FormValues>;
}) {
  const defaultRaw = defaults[spec.key];
  const parsedCurrent = parseNumber(current ?? '');
  const currentRaw =
    parsedCurrent == null
      ? null
      : isPercentKey(spec.key)
        ? toFraction(parsedCurrent)
        : parsedCurrent;
  const changed =
    currentRaw != null &&
    typeof defaultRaw === 'number' &&
    Math.abs(currentRaw - defaultRaw) > 1e-9;
  const porDefecto = defaultDisplay(spec, defaults);

  return (
    <div className="space-y-1">
      <Label htmlFor={`calib-${spec.key}`} className="text-xs">
        {spec.label}
      </Label>
      <div className="flex items-center gap-2">
        <Input
          id={`calib-${spec.key}`}
          inputMode="decimal"
          step={spec.step}
          aria-invalid={error ? true : undefined}
          className={cn('h-9', error && 'border-destructive')}
          {...register(spec.key)}
        />
        <span className="text-muted-foreground shrink-0 text-xs">{spec.unit}</span>
      </div>
      <p className="text-muted-foreground text-[11px] leading-snug">{spec.hint}</p>
      <p className="text-[11px] leading-snug">
        {porDefecto && (
          <span className="text-muted-foreground">Por defecto: {porDefecto}</span>
        )}
        {changed && <span className="text-accent-blue font-medium"> · cambiado</span>}
      </p>
      {error && (
        <p role="alert" className="text-destructive text-[11px] font-medium">
          {error}
        </p>
      )}
    </div>
  );
}
