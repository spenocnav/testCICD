/**
 * Tipos del API compartidos con el frontend.
 *
 * Estos espejan los schemas Pydantic de `apps/api`. Cuando se ejecute
 * `pnpm gen:types` (Fase 6) serán reemplazados por tipos generados desde OpenAPI.
 */

export interface RoleSummary {
  id: string;
  code: string;
  name: string;
}

export interface Permission {
  id: string;
  code: string;
  description: string | null;
  resource: string | null;
  action: string | null;
}

export interface ModuleRead {
  id: string;
  code: string;
  name: string;
  description: string | null;
  order: number;
  is_active: boolean;
}

export interface RoleDetail extends RoleSummary {
  description: string | null;
  is_system: boolean;
  permissions: Permission[];
}

export interface Fleet {
  id: string;
  code: string;
  name: string;
  is_active: boolean;
  /**
   * De dónde salen las bandas de RPM de la flota. Lo decide Navi Vehículos
   * (`customers.range_mode`); acá es de solo lectura.
   * - `reglas`: bandas derivadas de las reglas Geotab (default).
   * - `rpm`: bandas por los rangos de RPM del motor.
   */
  range_mode?: 'reglas' | 'rpm';
  /** ¿Tiene >=1 vehículo activo? El selector oculta las vacías. Default true. */
  has_vehicles?: boolean;
  /**
   * Módulo "Análisis Ralentí" contratado. Se activa en /gestion/flotas y gobierna
   * tanto la extracción del ETL como la visibilidad de la pestaña en Reportes
   * (visible sólo si TODAS las flotas del alcance lo tienen activo).
   */
  ralenti_analysis_enabled?: boolean;
}

/**
 * Motor presente en una flota. Réplica de solo lectura de Navi Vehículos: las
 * velocidades en `null` significan "aún no capturadas allá", nunca 0.
 */
export interface FleetMotor {
  motor_type: string;
  description: string | null;
  /** Velocidad nominal gobernada sin carga, en RPM. */
  governed_speed_rpm: number | null;
  /** Capacidad máxima de sobrevelocidad, en RPM. */
  max_overspeed_rpm: number | null;
  vehicle_count: number;
  /** Bandas del eje de RPM configuradas; 0 = motor sin configurar. */
  rpm_band_count: number;
}

export interface GeotabCredential {
  id: string;
  username: string;
  label: string | null;
  is_active: boolean;
  last_used_at: string | null;
  synced_at: string | null;
}

export interface GeotabRule {
  id: string;
  rule_id: string;
  name: string;
  category: 'operacion' | 'habito_seguro';
  event_type: string | null;
  motor_type: string | null;
  band: string | null;
  is_descenso: boolean;
  description: string | null;
  is_active: boolean;
}

export interface GeotabDatabase {
  id: string;
  database_name: string;
  database_key: string;
  connection_type: string;
  plate_prefix: string | null;
  is_active: boolean;
  synced_at: string | null;
  credentials: GeotabCredential[];
  rules: GeotabRule[];
  vehicle_count: number;
}

export interface SyncResult {
  full: boolean;
  generated_at: string | null;
  fleets: number;
  databases: number;
  credentials: number;
  rules: number;
  vehicles: number;
  deactivated: Record<string, number>;
}

export interface VehicleExtractionState {
  dataset: string;
  watermark: string | null;
  backfill_from: string | null;
  from_date: string;
  last_run_at: string | null;
  status: string;
  last_error: string | null;
}

export interface Vehicle {
  id: string;
  plate: string;
  vin: string | null;
  geotab_device_id: string | null;
  geotab_customer_status: string;
  fleet_id: string | null;
  fleet_name: string | null;
  geotab_database_id: string | null;
  database_name: string | null;
  marca: string | null;
  linea: string | null;
  marketing_model_name: string | null;
  service_model_name: string | null;
  ano_modelo: string | null;
  tipo_combustible: string | null;
  nombre_vehiculo: string | null;
  vocacional: boolean;
  category: string;
  engine_number: string | null;
  technical_number: string | null;
  cpl: string | null;
  motor_type: string | null;
  group_key: string | null;
  rpm_class: string | null;
  tank_volume: number | null;
  /** Grupo interno del cliente (fleet_vehicle_groups.id); null = sin grupo. */
  vehicle_group_id: string | null;
  is_active: boolean;
  synced_at: string | null;
}

/**
 * Nodo del árbol de grupos internos de una flota (categoría/subcategoría).
 * El árbol viaja plano: `parent_id` referencia otro nodo y null es la raíz.
 */
export interface FleetVehicleGroup {
  id: string;
  fleet_id: string;
  parent_id: string | null;
  name: string;
  is_active: boolean;
}

/** Cómo se emparejó un documento con un grupo de vehículos. */
export type MotorCurveMatch = 'cpl' | 'motor' | 'ambiguo';

export interface MotorCurveCoverage {
  cpl: string | null;
  vehicle_count: number;
  match: MotorCurveMatch;
}

export interface MotorCurve {
  id: string;
  motor_type: string;
  cpl: string | null;
  original_filename: string | null;
  content_type: string | null;
  file_size: number | null;
  source_updated_at: string | null;
  /** El emparejamiento más fuerte; el detalle honesto está en `coverage`. */
  match: MotorCurveMatch;
  vehicle_count: number;
  coverage: MotorCurveCoverage[];
  /** Estado de la caché del binario en el portal. */
  estado: string;
  cacheado: boolean;
}

export interface MotorWithoutCurve {
  motor_type: string;
  cpl: string | null;
  vehicle_count: number;
}

export interface MotorCurvesResponse {
  curvas: MotorCurve[];
  sin_curva: MotorWithoutCurve[];
}

export interface PaginatedVehicles {
  items: Vehicle[];
  total: number;
  total_all: number;
  active_total: number;
  limit: number;
  offset: number;
}

export type DataQualitySeverity = 'critical' | 'warning';

export interface DataQualityExample {
  label: string;
  detail: string | null;
}

export interface DataQualityMetric {
  key: string;
  label: string;
  count: number;
  severity: DataQualitySeverity;
  description: string;
  examples: DataQualityExample[];
}

export interface DataQualitySummary {
  generated_at: string;
  stale_after_hours: number;
  fleet_count: number;
  active_vehicles: number;
  healthy_vehicles: number;
  health_score: number;
  geotab_databases: number;
  latest_master_sync_at: string | null;
  metrics: DataQualityMetric[];
}

/**
 * `no_data` es terminal, no una tarea: el día no tuvo distancia en NINGUNA de las
 * dos fuentes, así que no existe la disyuntiva ECM contra GPS que una persona
 * pudiera resolver. Antes caía en `pending` e inflaba el contador de pendientes
 * con trabajo inexistente.
 */
export type DistanceReviewStatus =
  | 'pending'
  | 'auto_corrected'
  | 'resolved'
  | 'excluded'
  | 'no_data';
export type DistanceResolutionAction = 'use_ecm' | 'use_gps' | 'exclude' | 'restore_auto';

export interface DistanceAnomaly {
  fact_row_id: string;
  vehicle_id: string | null;
  fecha: string | null;
  placa: string | null;
  motor_type: string | null;
  kms_ecm: number | null;
  kms_gps: number | null;
  kms_effective: number | null;
  distance_diff_km: number | null;
  distance_diff_pct: number | null;
  distance_source: string | null;
  distance_quality_status: string | null;
  distance_quality_reason: string | null;
  gps_quality_valid: boolean;
  distance_quality_fingerprint: string | null;
  resolution_action: string | null;
  review_status: DistanceReviewStatus;
}

export interface DistanceAnomalyList {
  items: DistanceAnomaly[];
  total: number;
  limit: number;
  offset: number;
}

export type SyncRunKind = 'cloudfleet' | 'master' | 'novedades' | 'reportes';
export type SyncRunTrigger = 'manual' | 'worker' | 'cli';
export type SyncRunMode = 'full' | 'incremental';
/** 'partial': corrió completo pero algún dominio no crítico quedó sin actualizar. */
export type SyncRunStatus = 'success' | 'error' | 'partial';

export interface SyncRun {
  id: string;
  kind: SyncRunKind;
  trigger: SyncRunTrigger;
  mode: SyncRunMode;
  status: SyncRunStatus;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  result: Record<string, unknown> | null;
  error: string | null;
  actor_email: string | null;
}

/**
 * Corrida en vuelo. Las internas (CloudFleet) salen de `sync_run`; Reportes
 * conserva su cola en `etl_trigger_request`.
 */
export interface ActiveSyncRun {
  id: string;
  kind: SyncRunKind;
  /** 'pending': encolada, el worker aún no la reclama. */
  status: 'pending' | 'running';
  started_at: string | null;
  queued_at: string;
  /** Transcurrido medido por el servidor; el cliente lo sigue sumando localmente. */
  elapsed_ms: number;
  actor_email: string | null;
}

export interface SyncRunList {
  items: SyncRun[];
  /** En vuelo. No entran en la paginación: se muestran siempre arriba. */
  active: ActiveSyncRun[];
  /** Total de corridas CERRADAS que matchean el filtro. */
  total: number;
  limit: number;
  offset: number;
}

export type NovedadPriority = 'low' | 'medium' | 'high';
export type CloudfleetStatus = 'pending' | 'sent' | 'failed';

export interface NovedadAttachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  download_url: string;
}

export interface NovedadListItem {
  id: string;
  vehicle_id: string | null;
  fleet_id: string | null;
  vehicle_code: string;
  reported_at: string;
  priority: NovedadPriority;
  comment: string | null;
  cloudfleet_status: CloudfleetStatus;
  cloudfleet_issue_number: number | null;
  cloudfleet_error: string | null;
  /** Estado REAL en CloudFleet, replicado por el sync diario. `null` = aún no
   *  verificado. Independiente de `cloudfleet_status`, que es el del envío. */
  external_is_done: boolean | null;
  external_done_at: string | null;
  external_work_order_number: number | null;
  /** Estado de la orden enlazada. Es lo único que distingue "el taller la
   *  tiene" de "el taller terminó": `external_is_done` se activa al ASIGNAR la
   *  novedad a una orden, con la orden todavía abierta. */
  external_work_order_status: string | null;
  external_synced_at: string | null;
  /** CloudFleet ya no conoce la novedad. Deja de perseguir la falla y ésta se
   *  puede volver a escalar. */
  external_deleted_at: string | null;
  /** Actividad del catálogo que se pidió al reportar. NO es el trabajo que la
   *  resolvió: CloudFleet no publica ese enlace. */
  associated_labor_id: number | null;
  associated_labor_name: string | null;
  /** Referencia que el taller copia en cada trabajo de esta novedad. */
  navifault_reference: string | null;
  attachment_count: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface NovedadRead extends Omit<NovedadListItem, 'attachment_count'> {
  odometer: string | null;
  send_mail: boolean;
  cloudfleet_response: Record<string, unknown> | null;
  attachments: NovedadAttachment[];
}

export interface PaginatedNovedades {
  items: NovedadListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface UserRead {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_archived: boolean;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
  roles: RoleSummary[];
  fleets: Fleet[];
}

export interface SessionInfo {
  user: UserRead;
  permissions: string[];
  fleets: Fleet[];
  fleet_scope_global: boolean;
}

export type MeResponse = SessionInfo;

export type LoginResponse = SessionInfo;

export interface PaginatedUsers {
  items: UserRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiError {
  status: number;
  detail: string | unknown;
}

// Analytics — espeja app/schemas/analytics.py

export type FuelKind = 'liquid' | 'gas';

export interface AnalyticsVehicle {
  vehicle_id: string;
  vehicle_label: string | null;
  database_name: string | null;
  motor_type: string | null;
  group_key: string | null;
  rpm_class: string | null;
  fuel_type_raw: string | null;
  fuel_kind: FuelKind;
  fuel_unit: 'gal' | 'm3';
  fuel_classification_source: string | null;
  fuel_classification_conflict: boolean;
  is_active: boolean | null;
  vocacional: boolean;
  /** Grupo interno del cliente (fleet_vehicle_groups.id); null = sin grupo. */
  vehicle_group_id: string | null;
}

export interface CombustibleDaily {
  fact_row_id: string;
  vehicle_id: string | null;
  database_name: string | null;
  motor_type: string | null;
  date_key: number | null;
  fecha: string | null;
  placa: string | null;
  kms_ecm: number | null;
  hrs_ecm: number | null;
  hrs_gps: number | null;
  comb: number | null;
  comb_ralenti: number | null;
  fuel_kind: FuelKind | null;
  fuel_unit: 'gal' | 'm3' | null;
  km_gal: number | null;
  gal_hr: number | null;
  gal_hr_ralenti: number | null;
  km_m3: number | null;
  m3_hr: number | null;
  m3_hr_ralenti: number | null;
  velocidad_promedio: number | null;
  pct_rango_bajo: number | null;
  pct_rango_economico: number | null;
  pct_rango_balanceado: number | null;
  pct_rango_potencia: number | null;
  pct_exceso_rpm: number | null;
  pct_rango_potencia_ineficiente: number | null;
  pct_ralenti: number | null;
  ralenti: number | null;
}

export interface PaginatedCombustible {
  items: CombustibleDaily[];
  total: number;
  limit: number;
  offset: number;
}

export interface CombustibleSummary {
  fuel_kind: FuelKind;
  fuel_unit: 'gal' | 'm3';
  kms_ecm: number;
  hrs_ecm: number;
  hrs_gps: number;
  comb: number;
  comb_ralenti: number | null;
  km_gal: number | null;
  gal_hr: number | null;
  gal_hr_ralenti: number | null;
  km_m3: number | null;
  m3_hr: number | null;
  m3_hr_ralenti: number | null;
  velocidad_promedio: number | null;
  pct_ralenti: number | null;
  pct_exceso_rpm: number | null;
  n_vehiculos: number;
  n_registros: number;
  all_vocacional: boolean;
}

export interface CombustibleTimePoint {
  fuel_kind: FuelKind;
  fuel_unit: 'gal' | 'm3';
  periodo: number;
  label: string;
  kms_ecm: number | null;
  kms_gps: number | null;
  hrs_ecm: number | null;
  hrs_gps: number | null;
  comb: number | null;
  comb_ralenti: number | null;
  km_gal: number | null;
  gal_hr: number | null;
  gal_hr_ralenti: number | null;
  km_m3: number | null;
  m3_hr: number | null;
  m3_hr_ralenti: number | null;
  velocidad_promedio: number | null;
  pct_ralenti: number | null;
}

export interface VehicleRankingItem {
  vehicle_id: string | null;
  vehicle_label: string | null;
  placa: string | null;
  value: number | null;
}

export interface OperativoMonthlyPoint {
  periodo: number;
  label: string;
  // Total (incluye descenso).
  pct_rango_bajo: number | null;
  pct_rango_economico: number | null;
  pct_rango_balanceado: number | null;
  pct_rango_potencia: number | null;
  pct_rango_potencia_ineficiente: number | null;
  pct_exceso_rpm: number | null;
  pct_ralenti: number | null;
  /** Conteo de eventos RPM; no es porcentaje de tiempo. */
  eventos_rpm: number;
  /** Eventos RPM con factor de carga estrictamente menor a 5%. */
  eventos_rpm_descenso: number;
  /** Eventos RPM restantes/no clasificados como descenso. */
  eventos_rpm_sin_descenso: number;
  // Sin descenso (gráfico principal de bandas).
  pct_rango_bajo_sin_descenso: number | null;
  pct_rango_economico_sin_descenso: number | null;
  pct_rango_balanceado_sin_descenso: number | null;
  pct_rango_potencia_sin_descenso: number | null;
  pct_rango_potencia_ineficiente_sin_descenso: number | null;
  pct_exceso_rpm_sin_descenso: number | null;
  // En descenso (gráfico aparte).
  pct_rango_bajo_descenso: number | null;
  pct_rango_economico_descenso: number | null;
  pct_rango_balanceado_descenso: number | null;
  pct_rango_potencia_descenso: number | null;
  pct_rango_potencia_ineficiente_descenso: number | null;
  pct_exceso_rpm_descenso: number | null;
}

export interface PedalSummary {
  promedio: number | null;
  n_lecturas: number;
  ultima_lectura: string | null;
  unidad: string;
}

export interface FactorCargaMonthlyPoint {
  periodo: number;
  label: string;
  factor_de_carga: number | null;
}

export interface FactorCargaSummary {
  promedio: number | null;
  n_lecturas: number;
  monthly: FactorCargaMonthlyPoint[];
}

export type Granularity = 'daily' | 'monthly';

export type RankingMetric =
  | 'comb'
  | 'kms_ecm'
  | 'km_gal'
  | 'km_m3'
  | 'gal_hr'
  | 'm3_hr'
  | 'pct_exceso_rpm'
  | 'pct_ralenti';

// Calificación — espeja schemas de calificación en analytics.py

/** Conteo por estado: rojo, amarillo, verde. Verde es cumplir. */
export interface CalificacionEstado {
  no_cumple: number;
  en_riesgo: number;
  cumple: number;
}

export interface CalificacionEvolucionPoint {
  periodo: number;
  label: string;
  qhs: number | null;
  qho: number | null;
  qgen: number | null;
}

export interface CalificacionOperativoPoint {
  periodo: number;
  label: string;
  pct_eficiente: number | null;
  pct_ralenti: number | null;
  eventos_rpm: number;
  eventos_rpm_sobre_gobernada: number;
}

/** Un componente de Q.H. Operación ya resuelto para un vehículo concreto.
 * El backend entrega `valor_texto`/`objetivo_texto` ya formateados: la UI no
 * reinterpreta la escala del componente ni su unidad. */
export interface CalificacionComponenteDetalle {
  nombre: string;
  valor_texto: string;
  objetivo_texto: string;
  /** 0..100. NULL = componente no evaluable (p. ej. sin km): no es un 0. */
  puntos: number | null;
  peso: number;
  aporte: number | null;
  puntos_perdidos: number | null;
}

/** Eventos de hábitos seguros de un vehículo, agrupados por tipo. */
export interface CalificacionEventoDetalle {
  event_type: string;
  n_eventos: number;
  peso: number;
  aporte_ponderado: number;
}

/** Desglose por vehículo: de dónde sale su nota y qué la bajó. */
export interface CalificacionVehiculoDetalle {
  componentes_qho: CalificacionComponenteDetalle[];
  eventos_qhs: CalificacionEventoDetalle[];
  km: number | null;
  horas_ecm: number | null;
  exposicion_unidades: number | null;
  /** Base de exposición del vehículo: "1000 km" o "100 horas ECM". */
  exposicion_base: string;
  /** Mínimo del periodo en la unidad del vehículo (km u horas ECM). `null` = la
   * regla no aplica: la flota la desactivó o el periodo no tiene extremos. */
  exposicion_minima?: number | null;
  /** `false` = operó por debajo del mínimo, así que no se califica. */
  exposicion_suficiente?: boolean;
  /** Días del periodo consultado, el multiplicador del mínimo por día. */
  dias_periodo?: number | null;
  eventos_qhs_por_1000km: number | null;
  eventos_qhs_tope: number;
  /** Frases listas, ordenadas por puntos perdidos. No reordenar ni reformatear. */
  motivos: string[];
}

export interface CalificacionVehiculo {
  vehicle_id: string;
  placa: string | null;
  vocacional: boolean;
  eventos_rpm: number;
  eventos_rpm_sobre_gobernada: number;
  /** Excesos sobre la sobrevelocidad máxima del motor. Uno solo penaliza. Un
   * motor sin ese límite capturado no puede dispararla. */
  eventos_rpm_sobre_sobrevelocidad: number;
  penalizado_por_sobrevelocidad: boolean;
  /**
   * `false` = operó por debajo de la exposición mínima del periodo: su puntaje
   * sería una tasa sin denominador, así que `qgen` viene en `null` y
   * `qgen_base` conserva lo que habría puntuado. NO es lo mismo que penalizado:
   * ahí hay una falta, aquí falta de datos.
   *
   * Opcional porque este tipo es manual y el API que responde puede ser
   * anterior a la función; ausente se trata como suficiente.
   */
  exposicion_suficiente?: boolean;
  /** Fracción 0..1 del promedio de la flota que aporta este vehículo; 0 en los
   * que no entran. Es lo que reconcilia un puntaje alto con un promedio bajo. */
  peso_en_promedio?: number | null;
  qhs: number | null;
  qho: number | null;
  /** Puntaje antes de la penalización por sobrevelocidad. */
  qgen_base: number | null;
  /** Puntaje efectivo: 0 si está penalizado. */
  qgen: number | null;
  estado: string | null;
  detalle: CalificacionVehiculoDetalle | null;
}

/** Cortes de estado que publica el backend; el gauge no debe tener los suyos. */
export interface CalificacionUmbrales {
  en_riesgo: number;
  cumple: number;
}

export interface CalificacionComponente {
  nombre: string;
  peso: number;
  detalle: string;
}

export interface CalificacionEventoPeso {
  evento: string;
  peso: number;
}

/** Cómo se calcula la nota; lo publica el API para que la explicación no se
 * desfase de la calibración vigente. */
export interface CalificacionMetodologia {
  peso_qho: number;
  peso_qhs: number;
  componentes_qho: CalificacionComponente[];
  eventos_qhs: CalificacionEventoPeso[];
  eventos_excluidos: string[];
  eventos_tope_por_1000km: number;
  peso_evento_no_listado: number;
  /** Regla de la penalización por sobrevelocidad, redactada por el API. */
  penalizacion_sobrevelocidad: string;
  agregacion: string;
  /** Regla de la exposición mínima, redactada por el API según la calibración
   * efectiva de la flota. Opcional: el API puede ser anterior a la función. */
  exposicion_minima?: string;
}

/**
 * Lo que una flota puede recalibrar: los parámetros numéricos y el interruptor
 * de cada penalización. Lo que depende del motor —velocidad gobernada,
 * sobrevelocidad máxima y rangos de RPM— NO está aquí ni debe estarlo: sale de
 * la ficha del motor en Navi Vehículos.
 *
 * Los pesos y las metas de porcentaje viajan como fracción 0..1. La UI los
 * muestra y los edita en porcentaje; la conversión vive en el editor.
 */
export interface CalificacionCalibracion {
  /** Reparto general; `peso_qhs + peso_qho` = 1. */
  peso_qhs: number;
  peso_qho: number;
  /** Reparto dentro de operación; los tres suman 1. */
  peso_eficiente: number;
  peso_ralenti: number;
  peso_exceso_rpm: number;
  /** Meta de tiempo en rango eficiente (fracción 0..1). */
  efic_target: number;
  /** Ventana de ralentí (fracción 0..1); `ralenti_target < ralenti_max`. */
  ralenti_target: number;
  ralenti_max: number;
  /** Tope de eventos de seguridad ponderados por 1000 km. */
  eventos_cap: number;
  /** Topes de excesos de RPM según base de exposición del vehículo. */
  rpm_cap_comercial_1000km: number;
  rpm_cap_vocacional_100h: number;
  /** Agravación de los excesos sobre la velocidad gobernada; >= 1. */
  rpm_high_weight: number;
  /** Cortes de estado en puntos 0..100; `umbral_en_riesgo < umbral_cumple`. */
  umbral_en_riesgo: number;
  umbral_cumple: number;
  /**
   * Exposición mínima POR DÍA del periodo para que un vehículo se califique.
   * 0 desactiva la regla. Opcionales porque este tipo es manual y el API que
   * responde puede ser anterior a la función.
   */
  exposicion_minima_km_dia?: number;
  exposicion_minima_horas_dia?: number;
  /** `true` = la cifra de flota pondera por exposición (histórico y default);
   * `false` = promedio simple, un vehículo un voto. */
  promedio_ponderado?: boolean;
  /** Severidad por tipo de evento de hábitos seguros. */
  qhs_event_weights: Record<string, number>;
  /** Severidad de un tipo de evento que no esté en el mapa anterior. */
  qhs_default_weight: number;
  /**
   * Qué penalizaciones anulan el puntaje, por código del registro del backend.
   * Opcional porque este tipo es manual y el API que responde puede ser anterior
   * a la función: la UI tiene que poder pintar la calibración sin el mapa.
   * Un código ausente cae al default del registro, que es «activa».
   */
  penalizaciones?: Record<string, boolean>;
}

/**
 * Una penalización del catálogo que publica el backend. Es la ÚNICA fuente de
 * los códigos y sus etiquetas: duplicarlas en el frontend haría que una
 * penalización nueva del backend quedara invisible hasta recompilar la web.
 */
export interface CalificacionPenalizacion {
  code: string;
  nombre: string;
  descripcion: string;
}

/**
 * `defecto`: nadie calibró y se usan los valores del sistema.
 * `flota`: el alcance es una sola flota y tiene calibración propia.
 * `mixto`: el alcance mezcla flotas con calibraciones distintas, así que el
 * backend calculó con los valores por defecto. Hay que avisarlo: el puntaje que
 * se está viendo no corresponde a la calibración de ninguna de esas flotas.
 */
export type CalificacionConfigOrigen = 'defecto' | 'flota' | 'mixto';

/**
 * Respuesta cruda de `/reportes/calificacion/config` (GET, PUT y DELETE), que
 * es también el objeto `configuracion` embebido en la calificación: son el
 * mismo hecho y el backend publica el mismo schema en los dos sitios.
 *
 * `config` y `valores` son el MISMO dato con dos nombres: el API vigente lo
 * publica como `config` y una versión anterior del contrato lo llamaba
 * `valores`. Se aceptan ambos y se normaliza en `useCalificacionConfig`; los
 * consumidores no deben leer estas dos claves a mano.
 */
export interface CalificacionConfigResponse {
  origen: CalificacionConfigOrigen;
  fleet_id: string | null;
  actualizado_en: string | null;
  actualizado_por: string | null;
  config?: CalificacionCalibracion;
  valores?: CalificacionCalibracion;
  /** Valores por defecto del sistema; viajan en cada respuesta para poder
   * mostrar «por defecto: 30 %» sin una segunda petición. */
  defaults?: CalificacionCalibracion;
  /** Catálogo de penalizaciones que existen hoy en el backend, con su nombre y
   * su descripción. Viaja aparte del mapa de activas porque son dos hechos
   * distintos: qué penalizaciones EXISTEN y cuáles están encendidas. */
  penalizaciones_disponibles?: CalificacionPenalizacion[];
}

/** Con qué calibración se calculó la respuesta de calificación. */
export type CalificacionConfiguracion = CalificacionConfigResponse;

/** Config ya normalizada: una sola clave para los valores efectivos. */
export interface CalificacionConfigEfectiva {
  origen: CalificacionConfigOrigen;
  fleet_id: string | null;
  actualizado_en: string | null;
  actualizado_por: string | null;
  valores: CalificacionCalibracion;
  defaults: CalificacionCalibracion;
  /** Ya normalizado a arreglo: un API sin la función deja el catálogo vacío y
   * la sección de penalizaciones simplemente no se pinta. */
  penalizaciones_disponibles: CalificacionPenalizacion[];
}

/**
 * Una versión guardada de la calibración. `es_reset` marca un «volver a los
 * valores por defecto», que también es auditoría. `valida: false` es una fila
 * que hoy ya no pasa la validación: se muestra para que se vea qué pasó, no
 * para reutilizarla.
 */
export interface CalificacionConfigHistoryItem {
  id: string;
  creado_en: string;
  es_reset: boolean;
  actor_user_id?: string | null;
  actor_email?: string | null;
  config?: CalificacionCalibracion | null;
  valida?: boolean;
}

export interface CalificacionResponse {
  promedio_general: number | null;
  estado: CalificacionEstado;
  umbrales: CalificacionUmbrales;
  metodologia: CalificacionMetodologia;
  evolucion: CalificacionEvolucionPoint[];
  operativos: CalificacionOperativoPoint[];
  vehiculos: CalificacionVehiculo[];
  /** Opcional a propósito: este tipo es manual y la calibración por flota puede
   * no estar desplegada todavía en el API que responde. La UI tiene que
   * funcionar sin ella. */
  configuracion?: CalificacionConfiguracion | null;
}

// Hábitos seguros — espeja schemas de hábitos en analytics.py

export interface HabitoSummary {
  n_eventos: number;
  n_vehiculos: number;
  n_tipos: number;
  duracion_promedio: number | null;
  distancia_total_mt: number;
}

export interface HabitoTypeBucket {
  event_type: string;
  n_eventos: number;
  duracion_promedio: number | null;
  distancia_total_mt: number;
}

export interface HabitoTimePoint {
  periodo: number;
  label: string;
  n_eventos: number;
}

/** Unidad de la métrica que define un evento de hábitos. */
export type HabitoEventUnit = 'RPM' | 'km/h' | 'G';

/** Eje de la aceleración que define un evento de conducción brusca. */
export type HabitoGAxis = 'longitudinal' | 'lateral' | 'vertical';

export interface HabitoEvent {
  event_sk: string;
  vehicle_id: string | null;
  placa: string | null;
  fecha: string | null;
  fecha_y_hora_del_evento: string | null;
  event_type: string | null;
  rule_name: string | null;
  categoria: string | null;
  duracion_evento: number | null;
  distancia_evento_mt: number | null;
  observacion_corta: string | null;
  latitud: number | null;
  longitud: number | null;
  /** Tipo de motor del vehículo del evento, resuelto en backend. */
  motor_type: string | null;
  /** RPM del evento, parseado en backend desde la observación. */
  rpm_value: number | null;
  /** Velocidad gobernada del motor de ese vehículo. */
  rpm_governed_limit: number | null;
  /** Sobrevelocidad máxima del motor de ese vehículo. */
  rpm_overspeed_limit: number | null;
  /** Velocidad del evento en km/h. */
  velocidad_kmh: number | null;
  /** Factor de carga del evento, en porcentaje 0..100. */
  carga_pct: number | null;
  /** Fuerza G del eje que define el evento, CON signo (una frenada es negativa). */
  g_force: number | null;
  /** Eje al que corresponde `g_force`. */
  g_axis: HabitoGAxis | null;
  /** Métrica que define este tipo de evento (RPM, km/h o G según el tipo). */
  event_value: number | null;
  /** Unidad de `event_value`. */
  event_value_unit: HabitoEventUnit | null;
}

export interface PaginatedHabitos {
  items: HabitoEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface HabitoMapPoint {
  event_sk: string;
  placa: string | null;
  event_type: string | null;
  fecha_y_hora_del_evento: string | null;
  duracion_evento: number | null;
  observacion_corta: string | null;
  latitud: number;
  longitud: number;
}

// Ubicaciones (informe personalizado) — espeja LocationPointRead/UbicacionesPage

export interface LocationPoint {
  fecha_y_hora: string;
  placa: string | null;
  latitude: number;
  longitude: number;
  speed: number | null;
  /** Resuelta contra MyGeotab en la misma petición; null si no se pudo. */
  direccion: string | null;
}

export interface UbicacionesPage {
  items: LocationPoint[];
  /** Día servido por esta petición (`YYYY-MM-DD`). */
  day: string;
  sample_minutes: number;
  /** Siguiente día a pedir; null cuando el rango quedó cubierto. */
  next_cursor: string | null;
}

// Fallas / alertas técnicas — espeja schemas de fallas en analytics.py

export interface FaultSummary {
  n_fallas: number;
  n_eventos?: number;
  n_vehiculos: number;
  n_diagnosticos: number;
  n_urgentes: number;
}

export interface FaultSeverityBucket {
  severity: string;
  n_fallas: number;
}

export interface FaultParetoItem {
  label: string;
  n_fallas: number;
}

export interface FaultTimePoint {
  periodo: number;
  label: string;
  n_fallas: number;
}

export interface FaultEvent {
  row_id: string;
  vehicle_id: string | null;
  movil: string | null;
  fecha: string | null;
  fecha_de_falla: string | null;
  primera_fecha_de_falla?: string | null;
  codigo_diagnostico: number | null;
  codigo_modo_de_falla?: number | null;
  codigo_controlador?: number | null;
  nombre_fuente_diagnostico?: string | null;
  diagnostico: string | null;
  nombre_de_controlador: string | null;
  modo_de_falla: string | null;
  estado_de_falla: string | null;
  recuento_de_fallos: number | null;
  tipo_de_atencion: string | null;
  luz_de_parada_roja: boolean | null;
  luz_de_parada_amber: boolean | null;
  lampara_de_averia: boolean | null;
}

export interface FaultTimelineItem {
  row_id: string;
  fecha_de_falla: string | null;
  estado_de_falla?: string | null;
  recuento_de_fallos?: number | null;
  tipo_de_atencion?: string | null;
  luz_de_parada_roja?: boolean | null;
  luz_de_parada_amber?: boolean | null;
  lampara_de_averia?: boolean | null;
}

export interface PaginatedTimeline {
  items: FaultTimelineItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaginatedFallas {
  items: FaultEvent[];
  total: number;
  limit: number;
  offset: number;
}

export type FaultDimension = 'diagnostico' | 'controlador' | 'modo_falla' | 'fuente';

/** Semáforo del pipeline de etiquetas (worker sidecar + ingestor).
 * `status` lo decide el backend; la UI solo lo pinta. */
export interface TrackingHealth {
  status: 'ok' | 'degraded' | 'down' | 'unknown';
  headline: string;
  notes: string[];
  ingest_ok: boolean | null;
  ingest_age_seconds: number | null;
  worker_status: string | null;
  worker_age_seconds: number | null;
  worker_reasons: string[];
  events_total: number;
  events_with_label: number;
  last_event_at: string | null;
  active_orders: number | null;
  tracking_backlog: number | null;
}

// Análisis Ralentí — espeja schemas de ralenti en analytics.py

/** Rangos cerrados de duración de un episodio de ralentí. Siempre llegan completos y ordenados. */
export type RalentiDurationKey = 'lt1' | '1_5' | '5_10' | 'gt10';

/** Rangos cerrados de RPM promedio del episodio; `sin_rpm` agrupa los que no la tienen. */
export type RalentiRpmKey = 'lt600' | '600_800' | '800_1000' | '1000_1200' | 'gt1200' | 'sin_rpm';

export interface RalentiDurationBucket {
  bucket: RalentiDurationKey;
  label: string;
  eventos: number;
  minutos: number;
  pct_eventos: number;
  pct_minutos: number;
  duracion_promedio_min: number | null;
}

export interface RalentiRpmBucket {
  bucket: RalentiRpmKey;
  label: string;
  eventos: number;
  minutos: number;
  pct_eventos: number;
  pct_minutos: number;
}

export interface RalentiSummary {
  total_eventos: number;
  total_minutos: number;
  duracion_promedio_min: number | null;
  vehiculos: number;
  por_duracion: RalentiDurationBucket[];
  por_rpm: RalentiRpmBucket[];
}

export interface RalentiPlacaRow {
  vehicle_id: string;
  placa: string | null;
  eventos_lt1: number;
  eventos_1_5: number;
  eventos_5_10: number;
  eventos_gt10: number;
  minutos_lt1: number;
  minutos_1_5: number;
  minutos_5_10: number;
  minutos_gt10: number;
  total_eventos: number;
  total_minutos: number;
  /** Rango de duración que acumula MÁS TIEMPO en ralentí para la placa. */
  rango_dominante: RalentiDurationKey | null;
}

/** Celda de la grilla del mapa de calor (coordenadas ya redondeadas por el backend). */
export interface RalentiHeatCell {
  latitud: number;
  longitud: number;
  eventos: number;
  minutos: number;
}

export interface RalentiTimePoint {
  /** `YYYY-MM-DD` en granularidad diaria, `YYYY-MM` en mensual. */
  bucket: string;
  eventos: number;
  minutos: number;
}

export interface RalentiEvent {
  event_sk: string;
  vehicle_id: string;
  placa: string | null;
  inicio: string;
  fin: string;
  duracion_min: number;
  /** Cuántos registros de la fuente se fusionaron en este episodio. */
  eventos_fuente: number;
  rpm_promedio: number | null;
  rpm_maximo: number | null;
  latitud: number | null;
  longitud: number | null;
  duration_bucket: string;
  rpm_bucket: string;
}

export interface PaginatedRalentiEvents {
  items: RalentiEvent[];
  total: number;
  limit: number;
  offset: number;
}
