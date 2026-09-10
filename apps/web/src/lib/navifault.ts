import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api-client';

export interface NavifaultFaultCandidate {
  fault_page_id: string;
  pub_id: string;
  language: string;
  engine_model: string | null;
  fault_code: number;
  variant: string;
  title: string | null;
  reason: string | null;
  effect: string | null;
}

export interface NavifaultFaultCandidatesResponse {
  status: 'matched' | 'ambiguous' | 'no_match' | 'unavailable';
  detail: string;
  candidates: NavifaultFaultCandidate[];
}

export function useNavifaultFaultCandidates(
  faultRowId: string | null | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['navifault', 'fault-candidates', faultRowId],
    queryFn: () =>
      api.get<NavifaultFaultCandidatesResponse>(
        `/api/v1/navifault/fault-candidates/${encodeURIComponent(faultRowId!)}`,
      ),
    enabled: enabled && Boolean(faultRowId),
    staleTime: 5 * 60 * 1000,
  });
}

export type NavifaultClientDescriptionStatus = 'pending' | 'ready' | 'processing' | 'failed';

export interface NavifaultClientDescriptionResponse {
  status: NavifaultClientDescriptionStatus;
  descripcion_correo_cliente: string | null;
  descripcion_plataforma_cliente: string | null;
  fault_page_id: string;
  pub_id: string;
  fault_code: number;
  variant: string;
  prompt_version: string;
  model_version: string;
  cached: boolean;
}

export function useNavifaultClientDescription(
  faultRowId: string | null | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['navifault', 'client-description', faultRowId],
    queryFn: () =>
      api.post<NavifaultClientDescriptionResponse>(
        `/api/v1/navifault/client-description/${encodeURIComponent(faultRowId!)}`,
      ),
    enabled: enabled && Boolean(faultRowId),
    retry: false,
    staleTime: 0,
    refetchInterval: (query) => {
      const currentStatus = query.state.data?.status;
      return currentStatus === 'pending' || currentStatus === 'processing' ? 2_000 : false;
    },
  });
}

export interface NavifaultOriginalFaultDocumentResponse {
  fault_page_id: string;
  pub_id: string;
  language: string;
  fault_code: number;
  variant: string;
  title: string | null;
  html: string;
  embedded_images: number;
  missing_images: number;
}

export function useNavifaultOriginalFaultDocument(
  faultRowId: string | null | undefined,
  enabled: boolean,
  faultPageId?: string | null,
) {
  return useQuery({
    queryKey: ['navifault', 'original-document', faultRowId, faultPageId ?? 'default'],
    queryFn: () => {
      const url = faultPageId
        ? `/api/v1/navifault/original-document/${encodeURIComponent(faultRowId!)}?fault_page_id=${encodeURIComponent(faultPageId)}`
        : `/api/v1/navifault/original-document/${encodeURIComponent(faultRowId!)}`;
      return api.get<NavifaultOriginalFaultDocumentResponse>(url);
    },
    enabled: enabled && Boolean(faultRowId),
    retry: false,
    staleTime: 5 * 60 * 1_000,
  });
}

export interface NavifaultCorpusDocument {
  kind: 'page' | 'analysis' | 'document';
  document_id: string;
  title: string | null;
  html: string;
  embedded_images: number;
  missing_images: number;
}

/**
 * Un documento del corpus alcanzado desde un enlace del visor.
 *
 * El destino llega como `clase:id` dentro del HTML saneado: el backend lo
 * resolvió contra el corpus al renderizar, así que el cliente nunca interpreta
 * una URL de Cummins ni necesita saber cómo están construidas.
 */
export function useNavifaultCorpusDocument(target: string | null, enabled: boolean) {
  const [kind = '', documentId = ''] = (target ?? '').split(/:(.*)/s);
  return useQuery({
    queryKey: ['navifault', 'corpus-document', target],
    queryFn: () =>
      api.get<NavifaultCorpusDocument>(
        `/api/v1/navifault/corpus-document/${encodeURIComponent(kind)}/${encodeURIComponent(documentId)}`,
      ),
    enabled: enabled && Boolean(kind && documentId),
    retry: false,
    staleTime: 5 * 60 * 1_000,
  });
}

/**
 * Qué se hizo para resolver la falla. Lista CERRADA, y el orden es el de la
 * interfaz: de la intervención más material a la que no la hubo.
 *
 * `sin_hallazgo` y `dispositivo_telematico` existen para no obligar a inventar
 * una reparación cuando no la hubo. Sin ellos la falla se quedaría sin gestionar
 * para siempre o alguien registraría algo falso.
 */
export const NAVIFAULT_OUTCOME_TYPES = [
  {
    value: 'cambio_componente',
    label: 'Cambio de componente',
    hint: 'Se reemplazó la pieza.',
    requiresComponent: true,
  },
  {
    value: 'reparacion_componente',
    label: 'Reparación de componente',
    hint: 'Se intervino sin reemplazar: arnés, conexión, empalme.',
    requiresComponent: true,
  },
  {
    value: 'procedimiento',
    label: 'Procedimiento',
    hint: 'Limpieza, calibración, regeneración, software.',
    requiresComponent: false,
  },
  {
    value: 'sin_hallazgo',
    label: 'Sin hallazgo',
    hint: 'Se revisó y la falla no se replicó.',
    requiresComponent: false,
  },
  {
    value: 'dispositivo_telematico',
    label: 'Dispositivo telemático',
    hint: 'La falla era del equipo Geotab, no del vehículo.',
    requiresComponent: false,
  },
] as const;

export type NavifaultOutcomeType = (typeof NAVIFAULT_OUTCOME_TYPES)[number]['value'];

/** El componente es lo que hace comparable el historial entre equipos. */
export function outcomeRequiresComponent(type: NavifaultOutcomeType | ''): boolean {
  return NAVIFAULT_OUTCOME_TYPES.some((item) => item.value === type && item.requiresComponent);
}

export type NavifaultOutcomeVerificationStatus =
  | 'pending_verification'
  | 'verified_effective'
  | 'recurred'
  | 'invalidated';

export interface NavifaultFaultOutcome {
  outcome_id: string;
  outcome_type: NavifaultOutcomeType;
  outcome_action: string;
  component_name: string | null;
  outcome_detail: string | null;
  resolved_at: string;
  created_at: string;
  created_by: string | null;
  verification_status: NavifaultOutcomeVerificationStatus;
  verification_due_at: string;
  recurrence_at: string | null;
  context_scope: 'exact_cummins_page' | 'vehicle_signature';
}

export interface NavifaultOutcomeEffectiveness {
  outcome_type: NavifaultOutcomeType;
  component_name: string | null;
  outcome_action: string;
  completed_outcomes: number;
  effective_outcomes: number;
  recurred_outcomes: number;
  pending_outcomes: number;
  minimum_completed_outcomes: number;
  effectiveness_percent: number | null;
}

export interface NavifaultFaultOutcomesResponse {
  records: NavifaultFaultOutcome[];
  effectiveness: NavifaultOutcomeEffectiveness[];
  manual_match: 'direct' | 'ambiguous' | 'no_match' | 'unavailable';
  detail: string;
}

export interface NavifaultOutcomeSuggestionsResponse {
  suggestions: NavifaultOutcomeEffectiveness[];
  manual_match: 'direct' | 'ambiguous' | 'no_match' | 'unavailable';
}

/**
 * Qué se hizo en OTROS equipos para esta misma FC, y qué tan bien funcionó.
 *
 * Cruza todas las flotas y por eso el backend no publica placa, vehículo, flota
 * ni autor. Se ofrece al escribir, no sólo al leer: es lo que hace que el
 * vocabulario converja por uso —elegir es más fácil, y más útil, que escribir—
 * sin imponer una lista cerrada de acciones que la primera reparación nueva no
 * podría expresar.
 */
