import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api-client';

/**
 * Estados del ciclo de una firma de falla.
 *
 * `escalada` y `pendiente_registro` NO son "resuelta": la falla sigue sonando y
 * visible. La primera espera al taller; la segunda espera a nosotros — el taller
 * ya cerró su orden, pero una OT no dice tipo, acción ni componente, y sin eso
 * Navifault no puede dar la falla por gestionada.
 */
export type NavifaultManagementState =
  | 'pending'
  | 'escalada'
  | 'pendiente_registro'
  | 'repeated'
  | 'managed';

export interface NavifaultManagedFaultCase {
  case_id: string | null;
  status: NavifaultManagementState;
  vehicle_id: string;
  plate: string | null;
  source: string | null;
  diagnostic_code: number | null;
  failure_mode: number | null;
  diagnostic: string | null;
  failure_mode_name: string | null;
  controller: string | null;
  attention_type: string | null;
  stop_red: boolean | null;
  stop_amber: boolean | null;
  malfunction: boolean | null;
  warning: boolean | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  sample_fault_row_id: string;
  analytics_records: number;
  reported_occurrences: number;
  repeated_occurrences: number;
  last_managed_at: string | null;
  last_managed_by: string | null;
  last_note: string | null;
  /**
   * El escalamiento del ciclo VIGENTE. Viene poblado en TODOS los estados, no
   * sólo en `escalada`: la falla y la novedad tienen ciclos de vida
   * independientes, así que una fila puede decir "Gestionada · Novedad #698
   * abierta". Llega en `null` cuando el escalamiento es de un ciclo anterior.
   */
  escalated_novedad_id: string | null;
  escalated_issue_number: number | null;
  escalated_at: string | null;
  escalated_by: string | null;
  /** `null` = nunca verificado contra CloudFleet, no "abierta". */
  escalated_external_is_done: boolean | null;
  escalated_work_order_number: number | null;
  /**
   * La issue fue borrada en CloudFleet. La falla ya no persigue nada y se puede
   * escalar de nuevo: la fila no puede seguir diciendo "abierta".
   */
  escalated_deleted_at: string | null;
  /**
   * Trabajo de la orden al que el taller ató la novedad (`associatedLabor`).
   * Sin él, la orden no dice cuál de sus trabajos corresponde a esta falla.
   */
  escalated_labor_id: number | null;
  escalated_labor_name: string | null;
  /** Ocurrencias posteriores al escalamiento. `null` si no está escalada. */
  occurrences_since_escalation: number | null;
}

export interface NavifaultManagedFaultCasesResponse {
  items: NavifaultManagedFaultCase[];
  total: number;
  limit: number;
  offset: number;
}

export interface NavifaultManagementSummary {
  managed_faults: number;
  repeated_faults: number;
  /** Con una novedad persiguiéndolas y sin confirmar: escalada + pendiente de registro. */
  escalated_faults: number;
  average_management_seconds: number | null;
  /** De la aparición de la falla a que llegó al taller. Aún no se pinta. */
  average_escalation_seconds: number | null;
  escalation_time_sample_size: number;
  management_time_sample_size: number;
  management_time_window_days: number;
}

export interface NavifaultManagedFaultAction {
  action_id: string;
  action_type: 'managed' | 'unmanaged';
  managed_at: string;
  managed_through_at: string | null;
  managed_through_row_id: string | null;
  actor_name: string | null;
  note: string | null;
}

export interface NavifaultFaultManagementState {
  fault_row_id: string;
  status: NavifaultManagementState;
  last_note?: string | null;
  /**
   * Novedad que persigue la falla, mientras el escalamiento gobierna el estado.
   * Permite ofrecer el enlace en vez de un botón de escalar que el backend va a
   * rechazar con 409.
   */
  escalated_novedad_id?: string | null;
  escalated_issue_number?: number | null;
  /**
   * Identificador que el taller copia en los trabajos de la orden. Se deriva de
   * la falla, así que existe aunque nunca se haya escalado: es lo que permite
   * mapear una falla a una orden que ya estaba abierta por otra razón.
   */
  navifault_reference?: string | null;
  /**
   * Orden que el portal YA asocia a la falla en su ciclo vigente: la que el
   * taller le puso a la novedad, o la que sostiene una gestión confirmada. El
   * panel la carga sola en vez de pedir un número que el sistema ya conoce.
   */
  work_order_number?: number | null;
  /**
   * Si el vehículo no está en CloudFleet no se le puede crear una novedad: el
   * proveedor rechaza el alta. Sin esto la ficha ofrecía escalar y fallaba
   * después de que la persona escribiera el comentario.
   */
  vehicle_in_cloudfleet?: boolean;
  /**
   * La falla la reporta el propio equipo telemático sobre sí mismo. Única
   * excepción al "sin orden no hay gestión": no es del vehículo, así que no
   * puede tener una orden suya. Lo decide el backend con la misma función que
   * autoriza el cierre, para que la pantalla no ofrezca una vía que el endpoint
   * rechaza.
   */
  is_telematics?: boolean;
}

export function useNavifaultFaultManagementStates(faultRowIds: string[], enabled: boolean) {
  const canonicalRowIds = [...new Set(faultRowIds)].sort();
  return useQuery({
    queryKey: ['navifault', 'management', 'event-states', canonicalRowIds],
    queryFn: () =>
      api.post<NavifaultFaultManagementState[]>('/api/v1/navifault/management/event-states', {
        fault_row_ids: canonicalRowIds,
      }),
    enabled: enabled && canonicalRowIds.length > 0,
    staleTime: 60_000,
  });
}

export function useNavifaultManagedFaultCases(
  states: NavifaultManagementState[],
  scopeKey: string,
  limit = 50,
  offset = 0,
  enabled = true,
) {
  // Las claves llevan `scopeKey` porque el alcance de flota viaja en un header
  // que TanStack no ve: sin él, una entrada podría contener casos de otra flota.
  const canonicalStates = [...states].sort();
  return useQuery({
    queryKey: ['navifault', 'management', 'cases', scopeKey, canonicalStates, limit, offset],
    queryFn: ({ signal }) =>
      api.get<NavifaultManagedFaultCasesResponse>('/api/v1/navifault/management/cases', {
        query: { state: canonicalStates, limit, offset },
        signal,
      }),
    enabled,
    refetchInterval: 300_000,
    placeholderData: (previous) => previous,
  });
}

export function useNavifaultManagedFaultCaseActions(
  caseId: string | null | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['navifault', 'management', 'case-actions', caseId],
    queryFn: ({ signal }) =>
      api.get<NavifaultManagedFaultAction[]>(
        `/api/v1/navifault/management/cases/${encodeURIComponent(caseId!)}/actions`,
        { signal },
      ),
    enabled: enabled && Boolean(caseId),
    staleTime: 60_000,
  });
}

export function useNavifaultManagementSummary(scopeKey: string, enabled: boolean) {
  return useQuery({
    queryKey: ['navifault', 'management', 'summary', scopeKey],
    queryFn: () => api.get<NavifaultManagementSummary>('/api/v1/navifault/management/summary'),
    enabled,
    refetchInterval: 300_000,
    placeholderData: (previous) => previous,
  });
}

export interface NavifaultEscalationResponse {
  novedad_id: string;
  cloudfleet_issue_number: number | null;
  status: NavifaultManagementState;
  escalated_at: string;
  created: boolean;
}

/**
 * Escala una falla a una novedad de CloudFleet.
 *
 * Crea una issue REAL e irreversible —CloudFleet responde 405 a `DELETE`—, así
 * que la clave de idempotencia se genera una vez al abrir el diálogo y no en
 * cada clic: un doble clic no puede producir dos órdenes de trabajo.
 */
/** Repuesto consumido por un trabajo de la orden. */
export interface NavifaultOrderPart {
  id: number | null;
  name: string | null;
  code: string | null;
  qty: number | null;
}

/** Trabajo de la orden que lleva la referencia de la falla. */
export interface NavifaultOrderLabor {
  id: number | null;
  name: string | null;
  code: string | null;
  system: string | null;
  subsystem: string | null;
  maintenance_type: string | null;
  /** Con repuestos se cambió una pieza; sin ellos se intervino sin cambiarla. */
  replaced_component: boolean;
  parts: NavifaultOrderPart[];
}

export interface NavifaultOrderWork {
  work_order_number: number;
  work_order_status: string | null;
  /** La referencia que se buscó: es lo que hay que copiar si no aparece nada. */
  reference: string;
  /**
   * ¿La orden terminó? Lo decide el backend. La pantalla NO replica la lista de
   * estados de cierre: duplicar la regla es cómo las dos versiones se separan
   * sin que ningún tipo ni prueba lo detecte.
   */
  work_order_finished: boolean;
  labors: NavifaultOrderLabor[];
}

/**
 * Consulta qué registró el taller para una falla dentro de una orden.
 *
 * No gestiona nada. Es mutación y no consulta porque el backend puede avanzar
 * el ciclo de la falla al resolverla, y porque la orden la escribe el usuario:
 * no hay una clave estable que cachear.
 */
export function useNavifaultOrderWork() {
  return useMutation({
    mutationFn: (body: { faultRowId: string; workOrderNumber: number }) =>
      api.post<NavifaultOrderWork>('/api/v1/navifault/management/order-work', {
        fault_row_id: body.faultRowId,
        work_order_number: body.workOrderNumber,
      }),
  });
}

/** Confirma lo registrado en la orden, que es lo que gestiona la falla. */
export function useConfirmNavifaultFault() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { faultRowId: string; workOrderNumber: number; note?: string }) =>
      api.post<NavifaultOrderWork>('/api/v1/navifault/management/confirm', {
        fault_row_id: body.faultRowId,
        work_order_number: body.workOrderNumber,
        note: body.note?.trim() || undefined,
      }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'cases'] }),
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'event-states'] }),
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'summary'] }),
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'case-actions'] }),
      ]),
  });
}

export function useEscalateNavifaultFault() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      faultRowId: string;
      priority: 'low' | 'medium' | 'high';
      comment?: string;
      idempotencyKey: string;
    }) =>
      api.post<NavifaultEscalationResponse>(
        '/api/v1/navifault/management/escalate',
        {
          fault_row_id: body.faultRowId,
          priority: body.priority,
          comment: body.comment?.trim() || undefined,
        },
        { headers: { 'Idempotency-Key': body.idempotencyKey } },
      ),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'cases'] }),
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'event-states'] }),
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'summary'] }),
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'case-actions'] }),
        // La novedad recién creada aparece en el módulo de novedades.
        queryClient.invalidateQueries({ queryKey: ['novedades'] }),
      ]),
  });
}

/**
 * Cierra con una nota una falla del propio equipo telemático.
 *
 * Reintroduce el cierre con nota suelta que se retiró cuando gestionar pasó a
 * exigir el desenlace. La contradicción es sólo aparente: aquello obligaba a
 * declarar qué se hizo **cuando había algo que declarar**; aquí no lo hay, y
 * pedirlo sería pedir que alguien lo invente.
 */
export function useCloseTelematicsFault() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ faultRowId, note }: { faultRowId: string; note: string }) =>
      api.post<NavifaultManagedFaultAction>('/api/v1/navifault/management/telematics-note', {
        fault_row_id: faultRowId,
        note,
      }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'event-states'] }),
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'summary'] }),
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'cases'] }),
        queryClient.invalidateQueries({ queryKey: ['navifault', 'management', 'case-actions'] }),
      ]),
  });
}

export type NavifaultEvidenceVerification = 'held' | 'returned' | 'pending';

export interface NavifaultFaultEvidenceEntry {
  /** Cierre con nota: es lo único que quedó de esa gestión. */
  note: string | null;
  /** Se confirmó contra una orden. El número no viaja: identificaría al dueño. */
  from_work_order: boolean;
  labors: NavifaultOrderLabor[];
  managed_at: string;
  /**
   * `pending` no es "sin datos": es que la ventana de verificación todavía no
   * ha transcurrido, así que aún no se puede afirmar que funcionara. La lista
   * se muestra igual — lo que espera es el veredicto, no la evidencia.
   */
  verification: NavifaultEvidenceVerification;
}

export interface NavifaultFaultEvidence {
  entries: NavifaultFaultEvidenceEntry[];
  verification_window_days: number;
}

/**
 * Qué se hizo con esta misma falla en otros vehículos.
 *
 * Cruza todas las flotas y llega anónima: ni placa, ni flota, ni autor. El
 * conocimiento técnico sobre un código es de Navitrans; el dato operativo es
 * del cliente.
 */
export function useNavifaultFaultEvidence(faultRowId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['navifault', 'management', 'evidence', faultRowId],
    queryFn: ({ signal }) =>
      api.get<NavifaultFaultEvidence>(
        `/api/v1/navifault/management/evidence/${encodeURIComponent(faultRowId as string)}`,
        { signal },
      ),
    enabled: enabled && !!faultRowId,
    staleTime: 60_000,
  });
}
