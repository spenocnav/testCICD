'use client';

import Link from 'next/link';
import * as React from 'react';
import {
  AlertOctagon,
  AlertTriangle,
  BookOpenCheck,
  Calendar,
  CarFront,
  ChevronLeft,
  ChevronRight,
  ClipboardPlus,
  Clock,
  Cpu,
  FileText,
  History,
  Image as ImageIcon,
  Layers,
  Loader2,
  Repeat,
  Send,
  ShieldAlert,
  Wrench,
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
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useCanEdit, useCanView, useHasPermission } from '@/lib/auth';
import {
  useNavifaultClientDescription,
  useNavifaultFaultCandidates,
  useNavifaultCorpusDocument,
  useNavifaultOriginalFaultDocument,
} from '@/lib/navifault';
import {
  useEscalateNavifaultFault,
  type NavifaultFaultManagementState,
} from '@/lib/navifault-management';

import { OrderRegistrationPanel } from './order-registration-panel';
import { useFaultTimeline } from '@/lib/reportes';
import type { FaultEvent } from '@/lib/types';
import { extractErrorMessage } from '@/lib/api-client';
import { CopyChip } from '@/components/common/copy-chip';
import { cn, newIdempotencyKey } from '@/lib/utils';
import { toast } from 'sonner';

import { addUtcDays, getSingleDayDateRange, getTimelineMaxLookbackDate } from './navifault-date';

interface FaultDetailDialogProps {
  fault: FaultEvent | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  dateFrom?: string;
  dateTo?: string;
  /**
   * Estado de gestión de esta falla, que la lista ya consultó para la página.
   * Se recibe por prop en vez de volver a pedirlo: la consulta cubre las filas
   * visibles de una vez y repetirla por ficha sería una petición por apertura.
   */
  managementState?: NavifaultFaultManagementState;
  /**
   * Abre la ficha directamente en el registro de la orden. Lo usa el botón
   * Gestionar: gestionar es confirmar lo que el taller registró, así que
   * aterrizar en la pestaña técnica obligaría a buscar dónde se hace.
   */
  openOrderPanel?: boolean;
}

type DialogTab = 'ficha' | 'timeline' | 'interpretacion';

const TIMELINE_PAGE_SIZE = 5;

function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return new Intl.DateTimeFormat('es-CO', {
      day: 'numeric',
      month: 'numeric',
      year: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(d);
  } catch {
    return isoString;
  }
}

function formatDateOnly(dStr: string | null | undefined): string {
  if (!dStr) return '—';
  const parts = dStr.split('-');
  if (parts.length === 3) {
    return `${parts[2]}/${parts[1]}/${parts[0]}`;
  }
  return dStr;
}

/** Cómo el HTML saneado nombra un destino del corpus. Lo pone el backend. */
const ATRIBUTO_DESTINO = 'data-navifault-target';

/**
 * Visor de un documento del corpus, con sus enlaces internos navegables.
 *
 * El `iframe` va con `sandbox="allow-same-origin"` y SIN `allow-scripts`, igual
 * que antes: el documento no puede ejecutar nada. Lo que permite navegar es que
 * el padre, desde fuera, lea su `contentDocument` y capture el clic. No hace
 * falta relajar el sandbox ni la CSP, y el HTML no lleva ninguna URL navegable
 * —sólo el identificador que el backend resolvió contra el corpus—.
 */
function CorpusViewer({
  html,
  title,
  onNavigate,
}: {
  html: string;
  title: string;
  onNavigate: (target: string) => void;
}) {
  const marco = React.useRef<HTMLIFrameElement>(null);
  // El manejador se guarda en una ref para que `onLoad` no dependa de su
  // identidad: el iframe recarga con cada documento y volver a montarlo por un
  // cambio de función perdería el que se acaba de pintar.
  const navegar = React.useRef(onNavigate);
  React.useEffect(() => {
    navegar.current = onNavigate;
  });

  const conectar = React.useCallback(() => {
    const documento = marco.current?.contentDocument;
    if (!documento) return;
    documento.addEventListener('click', (event) => {
      const ancla = (event.target as Element | null)?.closest?.(`[${ATRIBUTO_DESTINO}]`);
      const destino = ancla?.getAttribute(ATRIBUTO_DESTINO);
      if (!destino) return;
      // Los enlaces que el corpus no reconoce quedaron en `#`: dejarlos
      // navegar movería el documento a su propio ancla vacía.
      event.preventDefault();
      navegar.current(destino);
    });
  }, []);

  return (
    <div className="flex h-full min-h-[500px] w-full flex-1 flex-col overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-sm ring-1 ring-black/5">
      <iframe
        ref={marco}
        title={title}
        sandbox="allow-same-origin"
        referrerPolicy="no-referrer"
        srcDoc={html}
        onLoad={conectar}
        className="h-full min-h-[500px] w-full flex-1 border-0"
      />
    </div>
  );
}

export function FaultDetailDialog({
  fault,
  open,
  onOpenChange,
  dateFrom,
  dateTo,
  managementState,
  openOrderPanel = false,
}: FaultDetailDialogProps) {
  const [activeTab, setActiveTab] = React.useState<DialogTab>('ficha');
  const [timelineOffset, setTimelineOffset] = React.useState(0);
  const [orderPanelOpen, setOrderPanelOpen] = React.useState(false);
  // Al abrir desde Gestionar, la ficha arranca con el registro de la orden a la
  // vista. Sólo al abrirse: después el panel lo gobierna quien lo mira.
  React.useEffect(() => {
    if (open && openOrderPanel) setOrderPanelOpen(true);
  }, [open, openOrderPanel]);
  const [escalateOpen, setEscalateOpen] = React.useState(false);
  const [escalatePriority, setEscalatePriority] = React.useState<'low' | 'medium' | 'high'>(
    'medium',
  );
  const [escalateComment, setEscalateComment] = React.useState('');
  // Una clave por APERTURA del diálogo, no por clic: un doble clic no puede
  // producir dos órdenes de trabajo, y CloudFleet no permite borrar issues.
  const [escalateKey, setEscalateKey] = React.useState('');
  const [documentDialogOpen, setDocumentDialogOpen] = React.useState(false);
  /**
   * Pila propia de navegación por el corpus, NO el historial del navegador.
   * Vacía significa "la FC de esta falla"; cada enlace seguido apila su destino.
   * Propia porque el visor vive en un diálogo: el Atrás del navegador cerraría
   * la pantalla entera en vez de retroceder un documento. Y hace falta —el
   * análisis de una FC enlaza a siete documentos técnicos, así que se baja
   * varios niveles enseguida.
   */
  const [pilaCorpus, setPilaCorpus] = React.useState<string[]>([]);
  const contentScrollRef = React.useRef<HTMLDivElement>(null);
  const canManageFaults = useCanEdit()('navifault');
  // El manual Cummins es de Navitrans; el cliente recibe la comunicación
  // redactada para él. Sin este permiso no se piden candidatas ni documento:
  // el backend responde 403 y pedirlos sólo produciría un error en pantalla.
  const canViewCorpus = useCanView()('navifault_corpus');
  // Escalar crea una orden de trabajo REAL e irreversible en CloudFleet, así que
  // es del rol admin y no de un permiso delegable. `'admin'` no existe en el
  // catálogo: sólo lo satisface el bypass del rol, igual que en /gestion/uso.
  const canEscalate = useHasPermission()('admin');
  // La falla ya tiene una novedad persiguiéndola. El backend responde 409 en
  // ese caso, así que ofrecer el botón sólo llevaba a escribir un comentario
  // para recibir un error al enviar.
  const escalatedIssueNumber = managementState?.escalated_issue_number ?? null;
  const escalatedNovedadId = managementState?.escalated_novedad_id ?? null;
  const yaEscalada = escalatedNovedadId != null;
  // `undefined` sólo mientras el estado no ha llegado; se asume que sí está
  // para no bloquear el botón por una consulta en vuelo.
  const vehiculoEnCloudfleet = managementState?.vehicle_in_cloudfleet !== false;
  const referencia = managementState?.navifault_reference ?? null;
  const ordenConocida = managementState?.work_order_number ?? null;
  // Una falla gestionada que no ha vuelto a aparecer no se escala: no hay nada
  // que perseguir y la novedad nueva le duplicaría el trabajo al taller, sin
  // vuelta atrás porque CloudFleet no deja borrar issues. El backend lo rechaza
  // igual; esto evita que la persona escriba el comentario para nada. `repeated`
  // NO entra: ahí la falla volvió y escalarla es justo lo que corresponde.
  const gestionadaSinReaparecer = managementState?.status === 'managed';
  // La falla es del propio equipo Geotab: no tiene orden de trabajo posible, y
  // el panel cambia de propósito en vez de pedir un número que no existe.
  const esDelEquipoTelematico = managementState?.is_telematics === true;
  // Hay novedad persiguiendo la falla pero el taller aún no la puso en una
  // orden. No es un fallo del portal: es un paso del taller que falta, y el
  // panel tiene que decirlo en vez de pedir un número que nadie puede saber.
  const esperandoOrdenDelTaller =
    !ordenConocida &&
    (managementState?.status === 'escalada' || managementState?.status === 'pendiente_registro');
  // El botón deja de ser una etiqueta genérica y dice en qué punto va el
  // proceso, para que no haya que abrirlo para averiguarlo.
  const etiquetaOrden = orderPanelOpen
    ? 'Ocultar'
    : esDelEquipoTelematico
      ? gestionadaSinReaparecer
        ? 'Ver cierre'
        : 'Cerrar con nota'
      : ordenConocida
      ? `Orden ${ordenConocida}`
      : esperandoOrdenDelTaller
        ? 'Orden pendiente'
        : 'Registro de orden';

  const defaultRange = React.useMemo(() => getSingleDayDateRange(), []);
  const [timelineDateFrom, setTimelineDateFrom] = React.useState(dateFrom ?? defaultRange.dateFrom);
  const [timelineDateTo, setTimelineDateTo] = React.useState(dateTo ?? defaultRange.dateTo);

  const minAllowedDate = React.useMemo(() => getTimelineMaxLookbackDate(), []);
  const maxAllowedDate = defaultRange.today;

  React.useEffect(() => {
    if (open) {
      setActiveTab('ficha');
      setTimelineOffset(0);
      setOrderPanelOpen(false);
      setDocumentDialogOpen(false);
      setTimelineDateFrom(dateFrom ?? defaultRange.dateFrom);
      setTimelineDateTo(dateTo ?? defaultRange.dateTo);
    }
  }, [open, fault?.row_id, dateFrom, dateTo, defaultRange.dateFrom, defaultRange.dateTo]);

  React.useEffect(() => {
    contentScrollRef.current?.scrollTo({ top: 0, behavior: 'instant' });
  }, [timelineOffset, activeTab]);

  const handleApplyPreset = (days: number) => {
    const today = defaultRange.today;
    const newFrom = days === 1 ? today : addUtcDays(today, -(days - 1));
    setTimelineDateFrom(newFrom);
    setTimelineDateTo(today);
    setTimelineOffset(0);
  };

  const is1DayActive =
    timelineDateTo === defaultRange.today && timelineDateFrom === defaultRange.today;
  const is3DaysActive =
    timelineDateTo === defaultRange.today &&
    timelineDateFrom === addUtcDays(defaultRange.today, -2);
  const is7DaysActive =
    timelineDateTo === defaultRange.today &&
    timelineDateFrom === addUtcDays(defaultRange.today, -6);

  const {
    data: timelineData,
    isLoading: timelineLoading,
    isFetching: timelineFetching,
  } = useFaultTimeline({
    vehicle_id: fault?.vehicle_id,
    codigo_diagnostico: fault?.codigo_diagnostico,
    codigo_modo_de_falla: fault?.codigo_modo_de_falla,
    diagnostico: fault?.diagnostico,
    date_from: timelineDateFrom,
    date_to: timelineDateTo,
    limit: TIMELINE_PAGE_SIZE,
    offset: timelineOffset,
    enabled: open && activeTab === 'timeline' && Boolean(fault?.vehicle_id),
  });
  const candidatesQuery = useNavifaultFaultCandidates(
    fault?.row_id,
    open && activeTab === 'interpretacion' && canViewCorpus,
  );
  // Con corpus se espera a que la resolución diga `matched`, para no encolar una
  // generación sobre una FC que no existe. Sin corpus no hay resolución que
  // esperar y la condición se cae sola: si se conservara, el cliente se quedaría
  // sin la única cosa que este módulo redacta para él.
  const clientDescriptionQuery = useNavifaultClientDescription(
    fault?.row_id,
    open &&
      activeTab === 'interpretacion' &&
      (!canViewCorpus || candidatesQuery.data?.status === 'matched'),
  );
  const destinoCorpus = pilaCorpus.at(-1) ?? null;
  const corpusQuery = useNavifaultCorpusDocument(
    destinoCorpus,
    documentDialogOpen && canViewCorpus,
  );
  const originalDocumentQuery = useNavifaultOriginalFaultDocument(
    fault?.row_id,
    documentDialogOpen && canViewCorpus,
  );
  // Lo que el visor pinta ahora mismo: la FC de esta falla si la pila está
  // vacía, o el documento al que llevó el último enlace seguido.
  const corpusVisible = React.useMemo(() => {
    if (destinoCorpus) {
      return corpusQuery.data
        ? { html: corpusQuery.data.html, title: corpusQuery.data.title || 'Documento Cummins' }
        : null;
    }
    return originalDocumentQuery.data
      ? {
          html: originalDocumentQuery.data.html,
          title: `Documento Cummins FC ${originalDocumentQuery.data.fault_code}`,
        }
      : null;
  }, [destinoCorpus, corpusQuery.data, originalDocumentQuery.data]);
  const escalate = useEscalateNavifaultFault();

  if (!fault) return null;

  const openEscalate = () => {
    setEscalatePriority('medium');
    setEscalateComment('');
    // Una clave por APERTURA del diálogo, no por clic: un doble clic no puede
    // producir dos órdenes de trabajo, y CloudFleet no permite borrar issues.
    // `crypto.randomUUID()` directo no sirve: por HTTP no es contexto seguro y
    // no existe, así que el diálogo no llegaba a abrirse.
    setEscalateKey(newIdempotencyKey());
    setEscalateOpen(true);
  };

  const handleEscalate = async () => {
    if (!fault?.row_id || !escalateKey) return;
    try {
      const result = await escalate.mutateAsync({
        faultRowId: fault.row_id,
        priority: escalatePriority,
        comment: escalateComment,
        idempotencyKey: escalateKey,
      });
      setEscalateOpen(false);
      toast.success(
        result.cloudfleet_issue_number
          ? `Escalada a la novedad #${result.cloudfleet_issue_number}`
          : 'Falla escalada a novedades',
      );
    } catch (error) {
      toast.error(extractErrorMessage(error, 'No se pudo escalar la falla'));
    }
  };

  const timelineItems = timelineData?.items ?? [];
  const totalTimelineEvents = timelineData?.total ?? 0;
  const totalPages = Math.ceil(totalTimelineEvents / TIMELINE_PAGE_SIZE);
  const currentPage = Math.floor(timelineOffset / TIMELINE_PAGE_SIZE) + 1;

  const isUrgente =
    fault.tipo_de_atencion?.toLowerCase().includes('urgente') ||
    fault.tipo_de_atencion?.toLowerCase().includes('nivel 1') ||
    Boolean(fault.luz_de_parada_roja);

  const isPrioritaria =
    fault.tipo_de_atencion?.toLowerCase().includes('prioritaria') ||
    fault.tipo_de_atencion?.toLowerCase().includes('nivel 2') ||
    Boolean(fault.luz_de_parada_amber);

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          className={cn(
            'flex h-[700px] max-h-[90vh] flex-col overflow-hidden p-6 shadow-2xl transition-all duration-200',
            orderPanelOpen ? 'max-w-5xl pr-[392px]' : 'max-w-2xl',
          )}
        >
          {/* Encabezado del Diálogo Limpio (Sin línea divisoria) */}
          <DialogHeader className="shrink-0 space-y-3 pb-2">
            <div className="flex flex-wrap items-center justify-between gap-3 pr-8">
              <div className="flex items-center gap-3">
                <span className="bg-destructive/10 text-destructive border-destructive/20 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border shadow-sm">
                  <AlertTriangle className="h-5 w-5" aria-hidden />
                </span>
                <div>
                  <DialogTitle className="font-heading text-foreground text-lg font-bold tracking-tight">
                    Ficha Técnica de Diagnóstico
                  </DialogTitle>
                  {/* La referencia va en la misma línea que la placa y el
                      código: identifica la falla igual que ellos. Fuera de la
                      descripción accesible para no meter un control dentro del
                      texto que lee el lector de pantalla como descripción. */}
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <DialogDescription className="text-muted-foreground text-xs">
                      Vehículo{' '}
                      <span className="text-foreground font-semibold">{fault.movil || '—'}</span> ·
                      Código{' '}
                      <span className="text-foreground font-semibold">
                        {fault.codigo_diagnostico ?? '—'}
                      </span>
                    </DialogDescription>
                    {referencia && canManageFaults && (
                      <CopyChip
                        value={referencia}
                        title="Copiar la referencia para pegarla en el trabajo de la orden"
                        showIcon={false}
                        // Sin borde ni etiqueta: la referencia se lee como un
                        // dato más de la identidad. El fondo al pasar el cursor
                        // es lo único que revela que se puede copiar.
                        className="text-muted-foreground hover:text-foreground h-5 border-transparent px-1 py-0 text-[11px]"
                      />
                    )}
                  </div>
                </div>
              </div>
              <div>
                {isUrgente ? (
                  <Badge variant="destructive" className="px-3 py-1 text-xs font-bold shadow-sm">
                    Nivel 1 - Urgente
                  </Badge>
                ) : isPrioritaria ? (
                  <Badge variant="warning" className="px-3 py-1 text-xs font-bold shadow-sm">
                    Nivel 2 - Prioritaria
                  </Badge>
                ) : (
                  <Badge variant="info" className="px-3 py-1 text-xs font-bold shadow-sm">
                    {fault.tipo_de_atencion || 'Nivel 3 - Pronta'}
                  </Badge>
                )}
              </div>
            </div>

            {/* Segmented Tabs con Diseño Ultra Minimalista */}
            <div className="flex justify-center pt-0.5">
              <div className="bg-muted/50 border-border/40 inline-flex items-center gap-0.5 rounded-lg border p-0.5">
                <button
                  type="button"
                  onClick={() => setActiveTab('ficha')}
                  className={cn(
                    'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-all duration-150',
                    activeTab === 'ficha'
                      ? 'bg-background text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground font-medium',
                  )}
                >
                  <Wrench
                    className={cn(
                      'h-3.5 w-3.5 transition-opacity',
                      activeTab === 'ficha' ? 'opacity-100' : 'opacity-60',
                    )}
                  />
                  Ficha Técnica
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('timeline')}
                  className={cn(
                    'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-all duration-150',
                    activeTab === 'timeline'
                      ? 'bg-background text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground font-medium',
                  )}
                >
                  <History
                    className={cn(
                      'h-3.5 w-3.5 transition-opacity',
                      activeTab === 'timeline' ? 'opacity-100' : 'opacity-60',
                    )}
                  />
                  Línea de Tiempo
                  {activeTab === 'timeline' && timelineData?.total != null && (
                    <span className="py-0.2 bg-muted text-foreground rounded px-1 font-mono text-[10px] font-semibold transition-colors">
                      {timelineData.total}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('interpretacion')}
                  className={cn(
                    'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-all duration-150',
                    activeTab === 'interpretacion'
                      ? 'bg-background text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground font-medium',
                  )}
                >
                  <BookOpenCheck
                    className={cn(
                      'h-3.5 w-3.5 transition-opacity',
                      activeTab === 'interpretacion' ? 'opacity-100' : 'opacity-60',
                    )}
                  />
                  Descripción de la Falla
                </button>
              </div>
            </div>
          </DialogHeader>

          {/* Contenido Principal con Altura Estable */}
          <div ref={contentScrollRef} className="min-h-0 flex-1 overflow-y-auto py-1 pr-0.5">
            {activeTab === 'ficha' ? (
              <div className="space-y-3.5">
                {/* Tarjeta Principal del Diagnóstico */}
                <div className="border-border bg-muted/40 rounded-lg border p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <span className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                        Diagnóstico Geotab
                      </span>
                      <h3 className="text-foreground mt-1 text-base font-bold">
                        {fault.diagnostico || 'Diagnóstico no especificado'}
                      </h3>
                    </div>
                    <div className="shrink-0 text-right">
                      <span className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                        Código
                      </span>
                      <p className="text-foreground mt-1 font-mono text-base font-bold">
                        {fault.codigo_diagnostico != null ? fault.codigo_diagnostico : '—'}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Estado de Lámparas de Tablero */}
                <div>
                  <h4 className="text-muted-foreground mb-2 text-xs font-bold uppercase tracking-wider">
                    Lámparas de Alerta en Tablero
                  </h4>
                  <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
                    {/* Luz Roja Stop */}
                    <div
                      className={`border-border flex items-center gap-3 rounded-lg border p-3 ${
                        fault.luz_de_parada_roja
                          ? 'bg-destructive/10 text-destructive font-medium'
                          : 'bg-background/50 text-muted-foreground opacity-60'
                      }`}
                    >
                      <div
                        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
                          fault.luz_de_parada_roja
                            ? 'bg-destructive text-white shadow-sm'
                            : 'bg-muted text-muted-foreground'
                        }`}
                      >
                        <AlertOctagon className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-xs font-bold leading-tight">Luz Roja (Stop)</p>
                        <p className="text-[11px] leading-tight">
                          {fault.luz_de_parada_roja ? 'Activa (Parada)' : 'Apagada'}
                        </p>
                      </div>
                    </div>

                    {/* Luz Ámbar Warning */}
                    <div
                      className={`border-border flex items-center gap-3 rounded-lg border p-3 ${
                        fault.luz_de_parada_amber
                          ? 'bg-accent-yellow/15 font-medium text-[#7A5300]'
                          : 'bg-background/50 text-muted-foreground opacity-60'
                      }`}
                    >
                      <div
                        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
                          fault.luz_de_parada_amber
                            ? 'bg-accent-yellow text-white shadow-sm'
                            : 'bg-muted text-muted-foreground'
                        }`}
                      >
                        <AlertTriangle className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-xs font-bold leading-tight">Luz Ámbar (Warning)</p>
                        <p className="text-[11px] leading-tight">
                          {fault.luz_de_parada_amber ? 'Activa (Atención)' : 'Apagada'}
                        </p>
                      </div>
                    </div>

                    {/* Lámpara Avería MIL */}
                    <div
                      className={`border-border flex items-center gap-3 rounded-lg border p-3 ${
                        fault.lampara_de_averia
                          ? 'bg-accent-yellow/15 font-medium text-[#7A5300]'
                          : 'bg-background/50 text-muted-foreground opacity-60'
                      }`}
                    >
                      <div
                        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
                          fault.lampara_de_averia
                            ? 'bg-accent-yellow text-white shadow-sm'
                            : 'bg-muted text-muted-foreground'
                        }`}
                      >
                        <Wrench className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-xs font-bold leading-tight">Lámpara MIL (Avería)</p>
                        <p className="text-[11px] leading-tight">
                          {fault.lampara_de_averia ? 'Activa (Check)' : 'Apagada'}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Grilla de Atributos Técnicos */}
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {/* Vehículo / Placa */}
                  <div className="border-border bg-muted/20 flex items-center gap-3 rounded-lg border p-3">
                    <div className="bg-muted text-foreground/70 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                      <CarFront className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-foreground text-xs font-bold">Vehículo / Móvil</p>
                      <p className="text-muted-foreground text-xs font-semibold">
                        {fault.movil || 'Sin placa'}
                      </p>
                    </div>
                  </div>

                  {/* Última Ocurrencia */}
                  <div className="border-border bg-muted/20 flex items-center gap-3 rounded-lg border p-3">
                    <div className="bg-muted text-foreground/70 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                      <Clock className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-foreground text-xs font-bold">Última Ocurrencia</p>
                      <p className="text-muted-foreground font-mono text-xs">
                        {formatDateTime(fault.fecha_de_falla || fault.fecha)}
                      </p>
                    </div>
                  </div>

                  {/* Controlador / Módulo ECU */}
                  <div className="border-border bg-muted/20 flex items-center gap-3 rounded-lg border p-3">
                    <div className="bg-muted text-foreground/70 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                      <Cpu className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-foreground text-xs font-bold">Controlador / ECU</p>
                      <p
                        className="text-muted-foreground truncate text-xs"
                        title={fault.nombre_de_controlador || 'No identificado'}
                      >
                        {fault.nombre_de_controlador || 'No identificado'}
                      </p>
                    </div>
                  </div>

                  {/* Modo de Falla (FMI) */}
                  <div className="border-border bg-muted/20 flex items-center gap-3 rounded-lg border p-3">
                    <div className="bg-muted text-foreground/70 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                      <Layers className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-foreground text-xs font-bold">Modo de Falla (FMI)</p>
                      <p className="text-muted-foreground truncate text-xs">
                        {fault.codigo_modo_de_falla != null
                          ? `FMI ${fault.codigo_modo_de_falla}${fault.modo_de_falla ? ` · ${fault.modo_de_falla}` : ''}`
                          : fault.modo_de_falla || 'Sin modo reportado'}
                      </p>
                    </div>
                  </div>

                  {/* Estado de Falla */}
                  <div className="border-border bg-muted/20 flex items-center gap-3 rounded-lg border p-3">
                    <div className="bg-muted text-foreground/70 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                      <ShieldAlert className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-foreground text-xs font-bold">Estado de Falla</p>
                      <p className="text-muted-foreground text-xs">
                        {fault.estado_de_falla && fault.estado_de_falla !== 'None'
                          ? fault.estado_de_falla
                          : 'No especificado'}
                      </p>
                    </div>
                  </div>

                  {/* Recuento de Fallas Registrado */}
                  <div className="border-border bg-muted/20 flex items-center gap-3 rounded-lg border p-3">
                    <div className="bg-muted text-foreground/70 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                      <Repeat className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-foreground text-xs font-bold">Recuento Registrado</p>
                      <p className="text-muted-foreground text-xs font-semibold">
                        {fault.recuento_de_fallos ?? 1}{' '}
                        {(fault.recuento_de_fallos ?? 1) === 1 ? 'vez' : 'veces'}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            ) : activeTab === 'timeline' ? (
              /* Pestaña: Línea de Tiempo (Historial Paginado) */
              <div className="space-y-3">
                {/* Banner y Selector de Periodo de la Línea de Tiempo */}
                <div className="border-border bg-muted/30 flex flex-col gap-2.5 rounded-lg border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Calendar className="text-muted-foreground h-4 w-4 shrink-0" />
                      <div className="text-xs">
                        <span className="text-muted-foreground">Periodo: </span>
                        <span className="text-foreground font-bold">
                          {formatDateOnly(timelineDateFrom)} — {formatDateOnly(timelineDateTo)}
                        </span>
                      </div>
                    </div>
                    <Badge variant="info" className="text-xs font-bold">
                      {timelineLoading ? 'Cargando...' : `${totalTimelineEvents} eventos`}
                    </Badge>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-2 pt-0.5">
                    {/* Presets Rápidos */}
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => handleApplyPreset(1)}
                        className={cn(
                          'rounded px-2.5 py-1 text-[11px] font-semibold transition-colors',
                          is1DayActive
                            ? 'bg-primary text-primary-foreground shadow-xs'
                            : 'bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground',
                        )}
                      >
                        Último día
                      </button>
                      <button
                        type="button"
                        onClick={() => handleApplyPreset(3)}
                        className={cn(
                          'rounded px-2.5 py-1 text-[11px] font-semibold transition-colors',
                          is3DaysActive
                            ? 'bg-primary text-primary-foreground shadow-xs'
                            : 'bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground',
                        )}
                      >
                        3 días
                      </button>
                      <button
                        type="button"
                        onClick={() => handleApplyPreset(7)}
                        className={cn(
                          'rounded px-2.5 py-1 text-[11px] font-semibold transition-colors',
                          is7DaysActive
                            ? 'bg-primary text-primary-foreground shadow-xs'
                            : 'bg-muted/70 text-muted-foreground hover:bg-muted hover:text-foreground',
                        )}
                      >
                        7 días
                      </button>
                    </div>

                    {/* Selectores de Fecha Desde / Hasta con límites min/max */}
                    <div className="flex items-center gap-1.5 text-xs">
                      <input
                        type="date"
                        value={timelineDateFrom}
                        min={minAllowedDate}
                        max={timelineDateTo || maxAllowedDate}
                        onChange={(e) => {
                          if (e.target.value) {
                            setTimelineDateFrom(e.target.value);
                            setTimelineOffset(0);
                          }
                        }}
                        className="border-input bg-background text-foreground focus-visible:ring-primary h-7 rounded border px-2 text-xs focus-visible:outline-none focus-visible:ring-1"
                      />
                      <span className="text-muted-foreground text-xs">—</span>
                      <input
                        type="date"
                        value={timelineDateTo}
                        min={timelineDateFrom || minAllowedDate}
                        max={maxAllowedDate}
                        onChange={(e) => {
                          if (e.target.value) {
                            setTimelineDateTo(e.target.value);
                            setTimelineOffset(0);
                          }
                        }}
                        className="border-input bg-background text-foreground focus-visible:ring-primary h-7 rounded border px-2 text-xs focus-visible:outline-none focus-visible:ring-1"
                      />
                    </div>
                  </div>
                </div>

                {/* Lista Cronológica */}
                {timelineLoading && !timelineData ? (
                  <div className="space-y-2 py-4">
                    <Skeleton className="h-11 w-full rounded-md" />
                    <Skeleton className="h-11 w-full rounded-md" />
                    <Skeleton className="h-11 w-full rounded-md" />
                    <Skeleton className="h-11 w-full rounded-md" />
                    <Skeleton className="h-11 w-full rounded-md" />
                  </div>
                ) : timelineItems.length === 0 ? (
                  <div className="text-muted-foreground py-12 text-center">
                    <Clock className="text-muted-foreground/40 mx-auto mb-2 h-8 w-8" />
                    <p className="text-sm font-semibold">
                      No se encontraron eventos en este periodo
                    </p>
                    <p className="mt-1 text-xs">
                      Prueba ampliando el rango a 3 o 7 días con los controles superiores.
                    </p>
                  </div>
                ) : (
                  <div
                    className={cn(
                      'space-y-2 transition-opacity duration-200',
                      timelineFetching && 'pointer-events-none opacity-60',
                    )}
                  >
                    {timelineItems.map((item, idx) => (
                      <div
                        key={item.row_id || idx}
                        className="border-border bg-background hover:bg-muted/50 flex items-center justify-between gap-3 rounded-lg border px-3.5 py-2.5 text-xs transition-colors"
                      >
                        <div className="flex items-center gap-3">
                          <span className="bg-muted text-muted-foreground flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold">
                            {totalTimelineEvents - (timelineOffset + idx)}
                          </span>
                          <div>
                            <p className="text-foreground font-mono text-xs font-bold">
                              {formatDateTime(item.fecha_de_falla)}
                            </p>
                            {item.estado_de_falla && item.estado_de_falla !== 'None' && (
                              <p className="text-muted-foreground text-[10px]">
                                Estado: {item.estado_de_falla}
                              </p>
                            )}
                          </div>
                        </div>

                        <div className="flex shrink-0 items-center gap-2">
                          {item.luz_de_parada_roja && (
                            <span
                              title="Luz Roja (Stop)"
                              className="bg-destructive ring-destructive/25 inline-flex h-2.5 w-2.5 rounded-full ring-2"
                            />
                          )}
                          {item.luz_de_parada_amber && (
                            <span
                              title="Luz Ámbar (Warning)"
                              className="bg-accent-yellow ring-accent-yellow/25 inline-flex h-2.5 w-2.5 rounded-full ring-2"
                            />
                          )}
                          {item.lampara_de_averia && (
                            <span
                              title="Lámpara MIL (Avería)"
                              className="ring-accent-yellow/25 inline-flex h-2.5 w-2.5 rounded-full bg-[#ffb301] ring-2"
                            />
                          )}
                          {item.tipo_de_atencion && (
                            <span className="text-muted-foreground text-[11px] font-medium">
                              {item.tipo_de_atencion}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Paginación Compacta de la Línea de Tiempo */}
                {totalTimelineEvents > TIMELINE_PAGE_SIZE && (
                  <div className="border-border/60 flex items-center justify-between border-t px-1 pt-3">
                    <span className="text-muted-foreground text-[11px] font-medium">
                      Mostrando {timelineOffset + 1}–
                      {Math.min(timelineOffset + TIMELINE_PAGE_SIZE, totalTimelineEvents)} de{' '}
                      {totalTimelineEvents} eventos
                    </span>
                    <div className="flex items-center gap-1.5">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={timelineOffset === 0 || timelineFetching}
                        onClick={() =>
                          setTimelineOffset((prev) => Math.max(0, prev - TIMELINE_PAGE_SIZE))
                        }
                        className="h-7 gap-1 px-2 text-xs"
                      >
                        <ChevronLeft className="h-3.5 w-3.5" />
                        Anterior
                      </Button>
                      <span className="text-foreground px-1 text-xs font-semibold">
                        {currentPage} / {totalPages}
                      </span>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={
                          timelineOffset + TIMELINE_PAGE_SIZE >= totalTimelineEvents ||
                          timelineFetching
                        }
                        onClick={() => setTimelineOffset((prev) => prev + TIMELINE_PAGE_SIZE)}
                        className="h-7 gap-1 px-2 text-xs"
                      >
                        Siguiente
                        <ChevronRight className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            ) : canViewCorpus && candidatesQuery.isLoading ? (
              <div className="space-y-3 py-2">
                <Skeleton className="h-16 w-full rounded-lg" />
                <Skeleton className="h-36 w-full rounded-lg" />
              </div>
            ) : canViewCorpus && (candidatesQuery.isError || !candidatesQuery.data) ? (
              <div className="border-border bg-muted/20 rounded-lg border p-5 text-sm">
                <p className="text-foreground font-semibold">
                  No fue posible consultar el manual Cummins
                </p>
                <p className="text-muted-foreground mt-1 text-xs">
                  Intenta abrir nuevamente la ficha. No se generó ninguna descripción por IA.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {clientDescriptionQuery.isLoading ||
                clientDescriptionQuery.data?.status === 'pending' ||
                clientDescriptionQuery.data?.status === 'processing' ? (
                  <div className="border-border bg-card space-y-2 rounded-lg border p-4">
                    <div className="flex items-center gap-2">
                      <Loader2 className="text-primary h-4 w-4 animate-spin" />
                      <span className="text-foreground text-xs font-semibold">
                        Generando interpretación con IA...
                      </span>
                    </div>
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-4 w-full" />
                    <p className="text-muted-foreground text-[11px]">
                      Puede tardar un momento.
                      {canViewCorpus ? ' La información del manual ya está disponible abajo.' : ''}
                    </p>
                  </div>
                ) : clientDescriptionQuery.data?.status === 'ready' ? (
                  <section className="border-primary/25 bg-primary/5 space-y-3 rounded-lg border p-4">
                    <div>
                      <p className="text-primary text-[11px] font-bold uppercase tracking-wide">
                        Interpretación
                      </p>
                      <p className="text-foreground mt-1 text-xs font-medium leading-relaxed">
                        {clientDescriptionQuery.data.descripcion_plataforma_cliente}
                      </p>
                    </div>
                    {clientDescriptionQuery.data.descripcion_correo_cliente && (
                      <div className="border-primary/15 border-t pt-2.5">
                        <p className="text-primary/80 text-[11px] font-semibold uppercase tracking-wide">
                          Versión para correo
                        </p>
                        <p className="text-foreground mt-1 text-xs leading-relaxed">
                          {clientDescriptionQuery.data.descripcion_correo_cliente}
                        </p>
                      </div>
                    )}
                  </section>
                ) : clientDescriptionQuery.isError ||
                  clientDescriptionQuery.data?.status === 'failed' ? (
                  <div className="border-border bg-muted/20 rounded-lg border p-4">
                    <p className="text-foreground text-xs font-semibold">
                      La interpretación por IA no está disponible
                    </p>
                    <p className="text-muted-foreground mt-1 text-[11px] leading-relaxed">
                      No fue posible generarla para esta falla.
                      {canViewCorpus
                        ? ' La información publicada por Cummins se muestra a continuación sin cambios.'
                        : ' Consulta con Navitrans el detalle técnico de esta falla.'}
                    </p>
                  </div>
                ) : null}

                {(candidatesQuery.data?.candidates ?? []).map((candidate) => (
                  <article
                    key={candidate.fault_page_id}
                    className="border-border bg-background space-y-3 rounded-lg border p-4"
                  >
                    <div>
                      <p className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wide">
                        Manual {candidate.pub_id} · {candidate.language}
                        {candidate.variant ? ` · Variante ${candidate.variant}` : ''}
                      </p>
                      <h4 className="text-foreground mt-1 text-sm font-bold leading-snug">
                        FC {candidate.fault_code} — {candidate.title || 'Título no publicado'}
                      </h4>
                    </div>
                    {candidate.engine_model && (
                      <p className="text-muted-foreground text-xs">
                        <span className="font-semibold">Motor:</span> {candidate.engine_model}
                      </p>
                    )}
                    {candidate.reason && (
                      <div className="text-xs leading-relaxed">
                        <p className="text-foreground font-semibold">Razón Cummins</p>
                        <p className="text-muted-foreground mt-0.5">{candidate.reason}</p>
                      </div>
                    )}
                    {candidate.effect && (
                      <div className="text-xs leading-relaxed">
                        <p className="text-foreground font-semibold">Efecto publicado</p>
                        <p className="text-muted-foreground mt-0.5">{candidate.effect}</p>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            )}
          </div>

          <DialogFooter className="flex shrink-0 flex-wrap justify-center gap-2 border-t pt-3 sm:justify-center">
            {canViewCorpus && (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={() => setDocumentDialogOpen(true)}
              >
                <BookOpenCheck className="h-3.5 w-3.5" />
                Ver documento original
              </Button>
            )}
            {canManageFaults && (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={() => setOrderPanelOpen((current) => !current)}
              >
                <ClipboardPlus className="h-3.5 w-3.5" />
                {etiquetaOrden}
              </Button>
            )}
            {canEscalate &&
              (yaEscalada ? (
                // Se muestra deshabilitado en vez de ocultarse: que el botón
                // desaparezca haría pensar que se perdió el permiso. Y el
                // enlace lleva a lo que sí se puede hacer, que es mirar la
                // novedad que ya persigue la falla.
                <Button
                  asChild
                  variant="outline"
                  size="sm"
                  className="gap-1.5 text-xs"
                  title="Esta falla ya tiene una novedad abierta persiguiéndola"
                >
                  <Link href={`/novedades/${escalatedNovedadId}`}>
                    <Send className="h-3.5 w-3.5" />
                    {/* Sin la palabra "Escalada": con ella el pie no cabe en
                        una fila, el botón de cerrar se va abajo y el modal
                        aparece con scroll. El estado lo dice el propio enlace
                        —una novedad persiguiendo la falla— y el title lo
                        explica al detenerse encima. */}
                    {escalatedIssueNumber
                      ? `Novedad #${escalatedIssueNumber}`
                      : 'Ya escalada'}
                  </Link>
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 text-xs"
                  onClick={openEscalate}
                  disabled={!vehiculoEnCloudfleet || gestionadaSinReaparecer}
                  title={
                    gestionadaSinReaparecer
                      ? 'La falla está gestionada y no ha vuelto a aparecer. Podrás escalarla si vuelve a presentarse; si la gestión no corresponde, corrige la orden en CloudFleet.'
                      : vehiculoEnCloudfleet
                        ? undefined
                        : `${fault.movil || 'Este vehículo'} no está registrado en CloudFleet, así que no se le puede crear una novedad.`
                  }
                >
                  <Send className="h-3.5 w-3.5" />
                  {vehiculoEnCloudfleet ? 'Escalar a novedad' : 'Vehículo sin CloudFleet'}
                </Button>
              ))}
            <Button
              variant="default"
              size="sm"
              onClick={() => onOpenChange(false)}
              className="text-xs"
            >
              Cerrar Ficha
            </Button>
          </DialogFooter>

          {/* Registro de la orden de CloudFleet. Navifault no declara
              desenlaces: confirma el del taller, que es la única fuente de
              verdad de lo que se hizo. */}
          {orderPanelOpen && canManageFaults && (
            <aside className="border-border bg-card absolute inset-y-0 right-0 z-10 flex w-[368px] flex-col border-l px-4 py-5 shadow-[-8px_0_20px_-18px_rgba(0,0,0,0.45)]">
              <div className="min-w-0 pr-8">
                <h3 className="text-foreground text-sm font-bold">
                  {esDelEquipoTelematico ? 'Cierre de la falla' : 'Registro de la orden'}
                </h3>
                <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
                  {esDelEquipoTelematico
                    ? gestionadaSinReaparecer
                      ? 'Con qué nota se cerró esta falla del equipo telemático.'
                      : 'La reporta el equipo Geotab sobre sí mismo, no el vehículo. No tiene orden de trabajo: se cierra con una nota.'
                    : 'Lo que el taller registró en CloudFleet para esta falla.'}
                </p>
              </div>

              <div className="mt-4 flex min-h-0 flex-1 flex-col">
                <OrderRegistrationPanel
                  faultRowId={fault.row_id}
                  knownWorkOrderNumber={ordenConocida}
                  awaitingWorkshopOrder={esperandoOrdenDelTaller}
                  isTelematics={esDelEquipoTelematico}
                  lastNote={managementState?.last_note}
                  alreadyManaged={managementState?.status === 'managed'}
                  onConfirmed={() => setOrderPanelOpen(false)}
                />
              </div>
            </aside>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={documentDialogOpen}
        onOpenChange={(abierto) => {
          setDocumentDialogOpen(abierto);
          // La pila se vacía al cerrar: reabrir el visor tiene que devolver a
          // la FC de esta falla, no al documento donde alguien se quedó.
          if (!abierto) setPilaCorpus([]);
        }}
      >
        <DialogContent className="flex h-[90vh] max-h-[94vh] max-w-6xl flex-col overflow-hidden border-slate-200 p-0 shadow-2xl">
          <DialogHeader className="bg-card shrink-0 border-b px-6 py-4 pr-12">
            <div className="flex flex-wrap items-center gap-2.5">
              <div className="bg-primary/10 text-primary flex h-7 w-7 items-center justify-center rounded-md">
                <FileText className="h-4 w-4" />
              </div>
              <DialogTitle className="text-foreground text-base font-bold">
                {destinoCorpus
                  ? corpusQuery.data?.title || 'Documento Cummins'
                  : 'Documento Original Cummins'}
              </DialogTitle>
              {!destinoCorpus && originalDocumentQuery.data && (
                <>
                  <Badge
                    variant="outline"
                    className="border-primary/25 bg-primary/8 text-primary text-xs font-bold"
                  >
                    FC {originalDocumentQuery.data.fault_code}
                  </Badge>
                  <Badge
                    variant="default"
                    className="bg-slate-100 text-[11px] font-semibold uppercase text-slate-700"
                  >
                    {originalDocumentQuery.data.language || 'ES'}
                  </Badge>
                </>
              )}
            </div>
            <DialogDescription className="text-muted-foreground mt-1 text-xs">
              {destinoCorpus
                ? `Alcanzado desde la FC${pilaCorpus.length > 1 ? ` · ${pilaCorpus.length} niveles` : ''} · Documentación técnica oficial de fábrica.`
                : originalDocumentQuery.data
                ? `Publicación: ${originalDocumentQuery.data.pub_id} · Variante: ${originalDocumentQuery.data.variant || 'Estándar'} · Documentación técnica oficial de fábrica.`
                : 'Vista técnica de la página oficial de código de falla.'}
            </DialogDescription>
          </DialogHeader>

          <div className="flex min-h-0 flex-1 flex-col bg-slate-100/60 p-3 sm:p-4">
            {originalDocumentQuery.isLoading ? (
              <div className="bg-card flex h-full min-h-[450px] flex-col items-center justify-center rounded-xl border border-slate-200/80 p-6 text-center shadow-sm">
                <Loader2 className="text-primary mb-3 h-8 w-8 animate-spin" />
                <p className="text-foreground text-sm font-semibold">
                  Cargando documento original...
                </p>
                <p className="text-muted-foreground mt-1 max-w-sm text-xs">
                  Recuperando HTML oficial y diagramas del corpus técnico Cummins.
                </p>
              </div>
            ) : originalDocumentQuery.isError ? (
              /* Que no haya documento NO es un fallo: la mayoría de las fallas
                 no tienen publicación en el manual. Un 404 se presenta como
                 ausencia y en tono neutro; lo demás sí es un error y se pinta
                 como tal. El texto del 404 lo escribe el backend para la
                 persona, así que se muestra tal cual. */
              (() => {
                const err = originalDocumentQuery.error as
                  | { status?: number; detail?: unknown }
                  | undefined;
                const sinDocumento = err?.status === 404;
                const motivo = extractErrorMessage(originalDocumentQuery.error, '');
                return (
                  <div
                    className={cn(
                      'flex h-full min-h-[450px] flex-col items-center justify-center rounded-xl border p-6 text-center',
                      sinDocumento
                        ? 'border-border bg-muted/20'
                        : 'border-destructive/20 bg-destructive/5',
                    )}
                  >
                    <div
                      className={cn(
                        'mb-3 rounded-full p-3',
                        sinDocumento ? 'bg-muted' : 'bg-destructive/10',
                      )}
                    >
                      {sinDocumento ? (
                        <FileText className="text-muted-foreground h-6 w-6" />
                      ) : (
                        <AlertTriangle className="text-destructive h-6 w-6" />
                      )}
                    </div>
                    <p
                      className={cn(
                        'text-sm font-semibold',
                        sinDocumento ? 'text-foreground' : 'text-destructive',
                      )}
                    >
                      {sinDocumento
                        ? 'Esta falla no tiene documento en el manual Cummins'
                        : 'No fue posible abrir el documento'}
                    </p>
                    <p className="text-muted-foreground mt-1.5 max-w-md text-xs leading-relaxed">
                      {sinDocumento ? (
                        <>
                          El código {fault.codigo_diagnostico ?? '—'}
                          {fault.codigo_modo_de_falla != null && ` · FMI ${fault.codigo_modo_de_falla}`}{' '}
                          del controlador {fault.nombre_de_controlador || 'sin identificar'} no
                          corresponde a ninguna publicación del corpus. Suele pasar cuando el
                          código no está en la numeración de Cummins o el motor del vehículo no
                          está cubierto por el manual.
                        </>
                      ) : (
                        motivo ||
                        'El corpus no respondió. Vuelve a intentarlo; si persiste, avisa a soporte.'
                      )}
                    </p>
                    {sinDocumento && (
                      <p className="text-muted-foreground/80 mt-3 max-w-md text-[11px] leading-relaxed">
                        La pestaña Descripción de la Falla sí puede tener una interpretación para
                        el cliente aunque no exista el documento técnico.
                      </p>
                    )}
                  </div>
                );
              })()
            ) : corpusVisible ? (
              <CorpusViewer
                html={corpusVisible.html}
                title={corpusVisible.title}
                onNavigate={(destino) => setPilaCorpus((actual) => [...actual, destino])}
              />
            ) : null}
          </div>

          {originalDocumentQuery.data && (
            <DialogFooter className="bg-card shrink-0 items-center border-t px-6 py-3 sm:justify-between">
              <div className="text-muted-foreground flex flex-wrap items-center gap-3 text-xs">
                <span className="inline-flex items-center gap-1.5 font-medium text-slate-700">
                  <ImageIcon className="h-3.5 w-3.5 text-emerald-600" />
                  Imágenes disponibles: {originalDocumentQuery.data.embedded_images}
                </span>
                {originalDocumentQuery.data.missing_images > 0 && (
                  <span className="inline-flex items-center gap-1 font-medium text-amber-600">
                    · Sin recuperar: {originalDocumentQuery.data.missing_images}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {/* Atrás retrocede un documento; con la cadena profunda hace
                    falta además volver de un salto, porque un análisis enlaza a
                    siete documentos y cada uno sigue enlazando. */}
                {pilaCorpus.length > 1 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-xs"
                    onClick={() => setPilaCorpus([])}
                  >
                    Volver a la FC
                  </Button>
                )}
                {pilaCorpus.length > 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => setPilaCorpus((actual) => actual.slice(0, -1))}
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                    Atrás
                  </Button>
                )}
                <Button variant="outline" size="sm" onClick={() => setDocumentDialogOpen(false)}>
                  Cerrar documento
                </Button>
              </div>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={escalateOpen}
        onOpenChange={(nextOpen) => {
          if (!escalate.isPending) setEscalateOpen(nextOpen);
        }}
      >
        <DialogContent className="z-[60] sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Escalar a novedad</DialogTitle>
            <DialogDescription>
              Se creará una orden de trabajo en CloudFleet para que el taller vea esta falla en la
              próxima intervención. La falla <strong>no</strong> queda gestionada: sigue activa
              hasta que alguien registre el desenlace.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <span className="text-sm font-medium">Prioridad</span>
              <div className="flex gap-1.5">
                {(
                  [
                    ['low', 'Baja'],
                    ['medium', 'Media'],
                    ['high', 'Alta'],
                  ] as const
                ).map(([value, label]) => (
                  <Button
                    key={value}
                    type="button"
                    size="sm"
                    variant={escalatePriority === value ? 'default' : 'outline'}
                    className="text-xs"
                    onClick={() => setEscalatePriority(value)}
                  >
                    {label}
                  </Button>
                ))}
              </div>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="navifault-escalate-comment" className="text-sm font-medium">
                Qué debe saber el taller <span className="text-muted-foreground">(opcional)</span>
              </label>
              <Textarea
                id="navifault-escalate-comment"
                value={escalateComment}
                onChange={(event) => setEscalateComment(event.target.value)}
                maxLength={4000}
                placeholder="Contexto que no esté en los datos de la falla: qué se observó, qué se descartó."
                className="min-h-24"
              />
              <p className="text-muted-foreground text-[11px] leading-snug">
                Se envía primero, y debajo van la placa, el código, el FMI, el controlador y las
                lámparas activas.
              </p>
            </div>
            <p className="text-muted-foreground text-[11px] leading-snug">
              CloudFleet no permite borrar órdenes: una creada por error hay que cerrarla desde su
              propia interfaz.
            </p>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={escalate.isPending}
              onClick={() => setEscalateOpen(false)}
            >
              Cancelar
            </Button>
            <Button disabled={escalate.isPending} onClick={handleEscalate}>
              {escalate.isPending ? 'Escalando…' : 'Escalar'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </>
  );
}
