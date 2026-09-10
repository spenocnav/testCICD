'use client';

import {
  CheckCircle2,
  ClipboardPlus,
  ChevronDown,
  Clock3,
  RotateCcw,
  Search,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
import * as React from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { extractErrorMessage } from '@/lib/api-client';
import { cn } from '@/lib/utils';
import {
  useCloseTelematicsFault,
  useConfirmNavifaultFault,
  useNavifaultFaultEvidence,
  useNavifaultOrderWork,
  type NavifaultEvidenceVerification,
  type NavifaultFaultEvidenceEntry,
  type NavifaultOrderLabor,
  type NavifaultOrderWork,
} from '@/lib/navifault-management';

interface OrderRegistrationPanelProps {
  faultRowId: string;
  /**
   * Orden que el portal ya asocia a esta falla. Se carga sola: pedirle el
   * número a la persona cuando el sistema ya lo sabe es pedirle un dato que
   * está a la vista en otra pantalla. El campo se queda editable porque el otro
   * caso —una falla atendida en una orden abierta por otra razón— no tiene de
   * dónde salir más que de quien lo sabe.
   */
  knownWorkOrderNumber: number | null;
  /**
   * Hay una novedad persiguiendo la falla, pero el taller todavía no la ha
   * puesto en una orden. Sin decirlo, el panel se abre pidiendo un número que
   * nadie puede saber aún y eso se lee como un fallo del portal.
   */
  awaitingWorkshopOrder: boolean;
  /**
   * La falla es del propio equipo telemático. No es del vehículo, así que no
   * puede tener una orden suya: pedirla obligaría a abrir órdenes por trabajo
   * que nunca se hizo, y no ofrecer nada la dejaría abierta para siempre.
   */
  isTelematics: boolean;
  /** La nota con la que se cerró, cuando ya está gestionada. */
  lastNote?: string | null;
  /** La falla ya está gestionada: sólo se muestra lo confirmado. */
  alreadyManaged: boolean;
  onConfirmed?: () => void;
}

function LaborCard({ labor }: { labor: NavifaultOrderLabor }) {
  return (
    <div className="border-border rounded-lg border p-3">
      <p className="text-foreground text-sm font-semibold">{labor.name ?? 'Trabajo sin nombre'}</p>
      <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
        {labor.system && <span>Sistema: {labor.system}</span>}
        {labor.subsystem && <span>· {labor.subsystem}</span>}
        {labor.maintenance_type && <span>· {labor.maintenance_type}</span>}
      </div>
      {/* Con repuestos se cambió una pieza; sin ellos se intervino sin
          cambiarla. Sale del dato del taller, no de lo que alguien escribió. */}
      {labor.parts.length > 0 ? (
        <ul className="mt-2 space-y-1">
          {labor.parts.map((part, index) => (
            <li key={part.id ?? index} className="text-foreground text-xs">
              <Wrench className="mr-1 inline h-3 w-3 align-[-1px]" aria-hidden />
              {part.name ?? part.code ?? 'Repuesto sin nombre'}
              {part.qty != null && <span className="text-muted-foreground"> · {part.qty}</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground mt-2 text-xs italic">Sin repuestos: intervención</p>
      )}
    </div>
  );
}


/**
 * Rótulo de sección, con el mismo tratamiento que los de la ficha.
 *
 * Va en un `span` y no en un `p`: uno de sus sitios es el interior de un
 * `button` y otro el de un `label`, y ninguno de los dos admite contenido de
 * flujo. `block` le devuelve el comportamiento de párrafo donde hace falta.
 */
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-muted-foreground block text-center text-[11px] font-semibold uppercase tracking-wide">
      {children}
    </span>
  );
}

/**
 * Veredicto de la ventana de verificación.
 *
 * `pending` NO se maquilla de éxito: mientras el plazo no transcurra, lo único
 * honesto es decir que todavía se está comprobando. Afirmar que funcionó antes
 * es justo lo que esa ventana existe para impedir.
 */
function VerificationChip({
  verification,
  windowDays,
}: {
  verification: NavifaultEvidenceVerification;
  windowDays: number;
}) {
  const estilos: Record<NavifaultEvidenceVerification, string> = {
    held: 'border-emerald-300 bg-emerald-50 text-emerald-800',
    returned: 'border-amber-300 bg-amber-50 text-amber-900',
    pending: 'border-border bg-muted/60 text-muted-foreground',
  };
  const Icono = { held: ShieldCheck, returned: RotateCcw, pending: Clock3 }[verification];
  const texto = {
    held: `No volvió en ${windowDays} días`,
    returned: 'Volvió a aparecer',
    pending: 'En verificación',
  }[verification];
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium',
        estilos[verification],
      )}
    >
      <Icono className="h-3 w-3 shrink-0" aria-hidden />
      {texto}
    </span>
  );
}

function EvidenceCard({
  entry,
  windowDays,
  onUse,
}: {
  entry: NavifaultFaultEvidenceEntry;
  windowDays: number;
  onUse?: (note: string) => void;
}) {
  const fecha = new Date(entry.managed_at).toLocaleDateString('es-CO', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
  return (
    <div className="border-border/70 bg-muted/40 rounded-lg border p-3">
      {entry.note && <p className="text-foreground text-sm leading-relaxed">{entry.note}</p>}
      {entry.labors.map((labor, index) => (
        <div key={labor.id ?? index} className={cn(index > 0 && 'mt-2')}>
          <p className="text-foreground text-sm font-semibold">{labor.name ?? 'Trabajo'}</p>
          <div className="text-muted-foreground mt-0.5 flex flex-wrap gap-x-3 text-xs">
            {labor.system && <span>{labor.system}</span>}
            {labor.maintenance_type && <span>· {labor.maintenance_type}</span>}
          </div>
          {labor.parts.map((part, i) => (
            <p key={part.id ?? i} className="text-foreground mt-1 text-xs">
              <Wrench className="mr-1 inline h-3 w-3 align-[-1px]" aria-hidden />
              {part.name ?? part.code}
            </p>
          ))}
        </div>
      ))}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <VerificationChip verification={entry.verification} windowDays={windowDays} />
          <span className="text-muted-foreground text-[11px]">{fecha}</span>
        </div>
        {onUse && entry.note && (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-[11px]"
            onClick={() => onUse(entry.note as string)}
          >
            Usar esto
          </Button>
        )}
      </div>
    </div>
  );
}

/**
 * Qué se hizo con la misma falla en otros vehículos.
 *
 * Va ARRIBA del formulario y no en una pestaña aparte: se ofrece al escribir,
 * no sólo al leer. Y se muestra aunque ninguna entrada tenga veredicto todavía
 * —la evidencia no espera a la efectividad—; lo que falta es el veredicto, no
 * el dato.
 *
 * Nunca lleva placa, flota ni autor: cruza todas las flotas porque el
 * conocimiento técnico sobre un código es de Navitrans, mientras que el dato
 * operativo es del cliente.
 */
function EvidenceSection({
  faultRowId,
  onUse,
}: {
  faultRowId: string;
  onUse?: (note: string) => void;
}) {
  const evidencia = useNavifaultFaultEvidence(faultRowId, true);
  const entries = evidencia.data?.entries ?? [];
  // Plegada de entrada: la acción es escribir y cerrar, y la evidencia no debe
  // empujarla fuera de la vista. El CONTADOR viaja en el rótulo aunque esté
  // cerrada — una sección plegada sin él es valor invisible: nadie abre lo que
  // no sabe que tiene algo dentro.
  const [abierta, setAbierta] = React.useState(false);

  if (evidencia.isPending) return <Skeleton className="h-8 w-full shrink-0" />;
  if (entries.length === 0) return null;

  return (
    <div className="shrink-0 space-y-2">
      <button
        type="button"
        onClick={() => setAbierta((actual) => !actual)}
        aria-expanded={abierta}
        className="hover:bg-muted/60 flex w-full items-center justify-center gap-1.5 rounded-md py-1 transition-colors"
      >
        <SectionLabel>
          Qué se hizo en otros vehículos · {entries.length}
        </SectionLabel>
        <ChevronDown
          className={cn(
            'text-muted-foreground h-3 w-3 shrink-0 transition-transform',
            abierta && 'rotate-180',
          )}
          aria-hidden
        />
      </button>
      {abierta && (
        <div className="max-h-44 space-y-2 overflow-y-auto pr-1">
          {entries.map((entry, index) => (
            <EvidenceCard
              key={index}
              entry={entry}
              windowDays={evidencia.data?.verification_window_days ?? 30}
              onUse={onUse}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Registro de la orden de CloudFleet que resolvió la falla.
 *
 * Navifault no declara desenlaces: confirma el del taller. La persona escribe el
 * número de orden y el backend busca en ella los trabajos que llevan la
 * referencia de esta falla. Sin ninguno no hay nada que confirmar, y el panel
 * dice qué copiar en lugar de dejar que alguien invente un desenlace.
 */
export function OrderRegistrationPanel({
  faultRowId,
  knownWorkOrderNumber,
  awaitingWorkshopOrder,
  isTelematics,
  lastNote,
  alreadyManaged,
  onConfirmed,
}: OrderRegistrationPanelProps) {
  const [orderInput, setOrderInput] = React.useState(
    knownWorkOrderNumber ? String(knownWorkOrderNumber) : '',
  );
  const [found, setFound] = React.useState<NavifaultOrderWork | null>(null);
  const [notFound, setNotFound] = React.useState<string | null>(null);

  const lookup = useNavifaultOrderWork();
  const confirm = useConfirmNavifaultFault();

  const orderNumber = Number.parseInt(orderInput.trim(), 10);
  const orderIsValid = Number.isInteger(orderNumber) && orderNumber > 0;

  const buscar = async (numero: number) => {
    if (!Number.isInteger(numero) || numero <= 0 || lookup.isPending) return;
    setNotFound(null);
    setFound(null);
    try {
      const resultado = await lookup.mutateAsync({ faultRowId, workOrderNumber: numero });
      if (resultado.labors.length === 0) {
        setNotFound(resultado.reference);
        return;
      }
      setFound(resultado);
    } catch (error) {
      toast.error(extractErrorMessage(error, 'No fue posible consultar la orden'));
    }
  };

  // El efecto de abajo dispara la búsqueda automática, y sólo debe hacerlo
  // cuando cambia la falla o la orden que el portal conoce. Si `buscar` entrara
  // en sus dependencias, el efecto correría también al terminar la consulta
  // —la mutación cambia de identidad— y su `setFound(null)` borraría el
  // resultado recién encontrado.
  const buscarRef = React.useRef(buscar);
  React.useEffect(() => {
    buscarRef.current = buscar;
  });

  // Al cambiar de falla se descarta lo consultado: mostrar el resultado de otra
  // sería peor que no mostrar nada. Y si el portal ya sabe la orden, se consulta
  // sola en vez de esperar a que alguien escriba lo que el sistema conoce.
  React.useEffect(() => {
    setOrderInput(knownWorkOrderNumber ? String(knownWorkOrderNumber) : '');
    setFound(null);
    setNotFound(null);
    if (knownWorkOrderNumber) void buscarRef.current(knownWorkOrderNumber);
  }, [faultRowId, knownWorkOrderNumber]);

  const confirmar = async () => {
    if (!found || confirm.isPending) return;
    try {
      await confirm.mutateAsync({ faultRowId, workOrderNumber: found.work_order_number });
      toast.success(`Falla gestionada con la orden ${found.work_order_number}`);
      onConfirmed?.();
    } catch (error) {
      toast.error(extractErrorMessage(error, 'No fue posible confirmar la gestión'));
    }
  };

  if (isTelematics) {
    return (
      <TelematicsClosePanel
        faultRowId={faultRowId}
        alreadyManaged={alreadyManaged}
        lastNote={lastNote}
        onConfirmed={onConfirmed}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex gap-2">
        <Input
          value={orderInput}
          onChange={(event) => setOrderInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void buscar(orderNumber);
          }}
          inputMode="numeric"
          placeholder="Número de orden"
          aria-label="Número de orden de CloudFleet"
          className="h-9 text-sm"
          disabled={alreadyManaged}
        />
        <Button
          size="sm"
          variant="outline"
          className="h-9 shrink-0 gap-1.5 text-xs"
          onClick={() => void buscar(orderNumber)}
          disabled={!orderIsValid || lookup.isPending || alreadyManaged}
        >
          <Search className="h-3.5 w-3.5" />
          {lookup.isPending ? 'Buscando…' : 'Buscar'}
        </Button>
      </div>

      {/* Sin botón de aplicar: la verdad de una falla del vehículo viene de
          CloudFleet, no del portal. Aquí la evidencia informa la conversación
          con el taller; ofrecer un clic que la "aplicara" sería reintroducir
          el desenlace manual que se retiró. */}
      <EvidenceSection faultRowId={faultRowId} />

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
        {lookup.isPending && <Skeleton className="h-24 w-full" />}

        {awaitingWorkshopOrder && !lookup.isPending && !found && !notFound && (
          <div className="border-border bg-muted/40 rounded-lg border p-3">
            <p className="text-foreground text-sm font-semibold">
              El taller aún no ha puesto esta novedad en una orden
            </p>
            <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
              Cuando la asigne, la orden aparecerá aquí sola. Si la falla se atendió en una orden
              abierta por otra razón, escribe su número arriba.
            </p>
          </div>
        )}

        {notFound && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3">
            <p className="text-sm font-semibold text-amber-900">
              La orden no tiene trabajos de esta falla
            </p>
            <p className="mt-1 text-xs leading-relaxed text-amber-900/80">
              Copia la referencia <span className="font-mono font-semibold">{notFound}</span> en la
              descripción del trabajo que corresponda, dentro de CloudFleet, y vuelve a buscar.
            </p>
          </div>
        )}

        {found && (
          <>
            <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
              <ClipboardPlus className="h-3.5 w-3.5" aria-hidden />
              Orden {found.work_order_number}
              {found.work_order_status && <span>· {found.work_order_status}</span>}
            </div>
            {found.labors.map((labor, index) => (
              <LaborCard key={labor.id ?? index} labor={labor} />
            ))}
            {!found.work_order_finished && (
              <div className="rounded-lg border border-sky-300 bg-sky-50 p-3">
                <p className="text-sm font-semibold text-sky-900">El taller aún no ha terminado</p>
                <p className="mt-1 text-xs leading-relaxed text-sky-900/80">
                  Una falla se gestiona confirmando lo que el taller terminó. Mientras la orden
                  siga abierta el registro puede cambiar, así que se podrá confirmar cuando la
                  cierre o la marque en cierre técnico en CloudFleet.
                </p>
              </div>
            )}
          </>
        )}
      </div>

      {found && found.work_order_finished && !alreadyManaged && (
        <Button
          size="sm"
          className="w-full gap-1.5 text-xs"
          onClick={() => void confirmar()}
          disabled={confirm.isPending}
        >
          <CheckCircle2 className="h-3.5 w-3.5" />
          {confirm.isPending ? 'Confirmando…' : 'Confirmar gestión'}
        </Button>
      )}
    </div>
  );
}

/**
 * Cierre con nota de una falla del propio equipo telemático.
 *
 * La nota es obligatoria: es lo ÚNICO que queda del cierre. Sin orden, sin
 * trabajos y sin repuestos, un cierre sin texto no dejaría constancia de nada y
 * la falla desaparecería de la bandeja sin que nadie pudiera decir por qué.
 */
function TelematicsClosePanel({
  faultRowId,
  alreadyManaged,
  lastNote,
  onConfirmed,
}: {
  faultRowId: string;
  alreadyManaged: boolean;
  lastNote?: string | null;
  onConfirmed?: () => void;
}) {
  const [note, setNote] = React.useState('');
  const cerrar = useCloseTelematicsFault();

  React.useEffect(() => {
    setNote('');
  }, [faultRowId]);

  const confirmar = async () => {
    if (!note.trim() || cerrar.isPending) return;
    try {
      await cerrar.mutateAsync({ faultRowId, note: note.trim() });
      toast.success('Falla del equipo telemático cerrada');
      onConfirmed?.();
    } catch (error) {
      toast.error(extractErrorMessage(error, 'No fue posible cerrar la falla'));
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {!alreadyManaged && <EvidenceSection faultRowId={faultRowId} onUse={setNote} />}

      {alreadyManaged ? (
        <div className="flex min-h-0 flex-1 flex-col gap-1.5">
          <SectionLabel>Qué se hizo</SectionLabel>
          <div className="border-border min-h-0 flex-1 overflow-y-auto rounded-md border p-3">
            <p className="text-foreground whitespace-pre-wrap text-sm leading-relaxed">
              {lastNote?.trim() || 'La falla se cerró sin dejar nota.'}
            </p>
          </div>
          <p className="text-muted-foreground text-xs">
            La falla ya está cerrada. Si el equipo vuelve a reportarla, podrás cerrarla de nuevo.
          </p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-1.5">
          <label htmlFor="navifault-telematics-note">
            <SectionLabel>Qué se hizo</SectionLabel>
          </label>
          {/* Crece hasta llenar el panel en vez de dejar un vacío entre el campo
              y el botón. Y sin agarradera: con el campo ocupando todo el alto
              disponible, redimensionarlo no aporta nada. */}
          <Textarea
            id="navifault-telematics-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={4000}
            placeholder="Ej.: se reasentó el conector del equipo y dejó de reportar."
            className="min-h-32 flex-1 resize-none text-sm"
          />
          <p className="text-muted-foreground text-xs">
            Es lo único que queda del cierre, así que sin nota no se puede cerrar.
          </p>
        </div>
      )}

      {!alreadyManaged && (
        <Button
          size="sm"
          className="w-full gap-1.5 text-xs"
          onClick={() => void confirmar()}
          disabled={!note.trim() || cerrar.isPending}
        >
          <CheckCircle2 className="h-3.5 w-3.5" />
          {cerrar.isPending ? 'Cerrando…' : 'Cerrar falla'}
        </Button>
      )}
    </div>
  );
}
