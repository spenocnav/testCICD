from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ClientDescriptionStatus = Literal["pending", "ready", "processing", "failed"]


class NavifaultClientDescriptionResponse(BaseModel):
    """Comunicación cliente generada una sola vez por FC/contexto/modelo."""

    status: ClientDescriptionStatus
    descripcion_correo_cliente: str | None = None
    descripcion_plataforma_cliente: str | None = None
    fault_page_id: str
    pub_id: str
    fault_code: int
    variant: str
    prompt_version: str
    model_version: str
    cached: bool


class NavifaultCorpusDocumentResponse(BaseModel):
    """Documento del corpus alcanzado por un enlace del visor.

    Sirve las tres clases —página de falla, análisis y documento técnico— con un
    solo contrato: el visor navega entre ellas y con contratos separados tendría
    que saber a cuál llamar antes de saber a dónde lleva el enlace.
    """

    kind: Literal["page", "analysis", "document"]
    document_id: str
    title: str | None = None
    html: str
    embedded_images: int = 0
    missing_images: int = 0


class NavifaultOriginalFaultDocumentResponse(BaseModel):
    """HTML oficial aislado para visor interno; nunca se entrega a clientes."""

    fault_page_id: str
    pub_id: str
    language: str
    fault_code: int
    variant: str
    title: str | None = None
    html: str
    embedded_images: int
    missing_images: int


class NavifaultFaultCandidate(BaseModel):
    """Contenido oficial mínimo para la vista temporal sin LLM."""

    fault_page_id: str
    pub_id: str
    language: str
    engine_model: str | None = None
    fault_code: int
    variant: str
    title: str | None = None
    reason: str | None = None
    effect: str | None = None


class NavifaultFaultCandidatesResponse(BaseModel):
    """FC Cummins encontradas para una fila histórica de Geotab."""

    status: Literal["matched", "ambiguous", "no_match", "unavailable"]
    detail: str
    candidates: list[NavifaultFaultCandidate] = Field(default_factory=list)


FaultManagementState = Literal[
    "pending",
    "escalada",
    "pendiente_registro",
    "repeated",
    "managed",
]


class NavifaultManagedFaultCaseRead(BaseModel):
    """Caso agrupado para la bandeja interna de gestores de flota."""

    case_id: UUID | None = None
    status: FaultManagementState
    vehicle_id: UUID
    plate: str | None = None
    source: str | None = None
    diagnostic_code: int | None = None
    failure_mode: float | None = None
    diagnostic: str | None = None
    failure_mode_name: str | None = None
    controller: str | None = None
    attention_type: str | None = None
    stop_red: bool | None = None
    stop_amber: bool | None = None
    malfunction: bool | None = None
    warning: bool | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    sample_fault_row_id: str
    analytics_records: int
    reported_occurrences: int
    repeated_occurrences: int = 0
    last_managed_at: datetime | None = None
    last_managed_by: str | None = None
    last_note: str | None = None
    #: El escalamiento del ciclo VIGENTE. Se publica en todos los estados, no
    #: sólo en `escalada`: la falla y la novedad tienen ciclos de vida
    #: independientes —gestionar la falla no cierra la issue— así que una fila
    #: debe poder decir "Gestionada · Novedad #698 abierta". Viene en `null`
    #: cuando el escalamiento es de un ciclo anterior.
    escalated_novedad_id: UUID | None = None
    escalated_issue_number: int | None = None
    escalated_at: datetime | None = None
    escalated_by: str | None = None
    #: `null` = nunca verificado contra CloudFleet, no "abierta".
    escalated_external_is_done: bool | None = None
    escalated_work_order_number: int | None = None
    #: La issue fue borrada en CloudFleet: no hay nada persiguiendo la falla y
    #: se puede escalar de nuevo.
    escalated_deleted_at: datetime | None = None
    #: Trabajo de la orden al que el taller ató la novedad.
    escalated_labor_id: int | None = None
    escalated_labor_name: str | None = None
    #: Ocurrencias posteriores al escalamiento. `null` si no está escalada en
    #: este ciclo; nunca 0 en ese caso, porque "no escalada" y "escalada sin
    #: reincidencia" no son lo mismo.
    occurrences_since_escalation: int | None = None


class NavifaultManagedFaultCasesResponse(BaseModel):
    items: list[NavifaultManagedFaultCaseRead]
    total: int
    limit: int
    offset: int


class NavifaultManagementSummary(BaseModel):
    """Indicadores internos de estado y tiempo de gestión por firma de falla."""

    managed_faults: int
    repeated_faults: int
    #: Fallas con una novedad persiguiéndolas y sin confirmar todavía. Reúne
    #: `escalada` y `pendiente_registro`: en las dos hay algo en curso.
    escalated_faults: int = 0
    average_management_seconds: int | None = None
    management_time_sample_size: int
    #: De la aparición de la falla a que llegó al taller. Se mide igual que el
    #: de gestión; hoy no se pinta, pero el registro queda.
    average_escalation_seconds: int | None = None
    escalation_time_sample_size: int = 0
    management_time_window_days: int


class NavifaultFaultManagementStatesRequest(BaseModel):
    """Filas visibles cuya gestión se consulta en una sola petición."""

    fault_row_ids: list[str] = Field(min_length=1, max_length=100)


class NavifaultFaultManagementStateRead(BaseModel):
    fault_row_id: str
    status: FaultManagementState
    last_note: str | None = None
    #: Novedad que persigue la falla, sólo mientras el escalamiento gobierna el
    #: estado. Permite que la ficha ofrezca el enlace en vez de un botón de
    #: escalar que el backend va a rechazar con 409.
    escalated_novedad_id: UUID | None = None
    escalated_issue_number: int | None = None
    #: Identificador que el taller copia en los trabajos de la orden. Se deriva
    #: de la falla, así que existe aunque nunca se haya escalado.
    navifault_reference: str | None = None
    #: Orden que el portal YA asocia a esta falla en su ciclo vigente: la que el
    #: taller le puso a la novedad, o la que sostiene una gestión confirmada.
    #: Existe para que el panel no le pida a la persona un número que el sistema
    #: ya conoce. `null` obliga a escribirlo, que es el caso de una falla
    #: atendida en una orden abierta por otra razón.
    work_order_number: int | None = None
    #: Si el vehículo no está en CloudFleet no se le puede crear una novedad, y
    #: escalar fallaría después de que la persona escriba el comentario.
    vehicle_in_cloudfleet: bool = True
    #: La falla la reporta el propio equipo telemático sobre sí mismo. Es la
    #: única excepción al "sin orden no hay gestión": no es del vehículo, así
    #: que no puede tener una orden suya y se cierra con una nota. Lo decide el
    #: DATO —la fuente— y no una elección del usuario, y lo decide el backend
    #: para que la pantalla no pueda ofrecer una vía que el endpoint rechaza.
    is_telematics: bool = False


class NavifaultEscalateFaultRequest(BaseModel):
    """Escalar una falla a una novedad de CloudFleet.

    `reported_at` NO viaja en el cuerpo: es la última ocurrencia de la falla, que
    el backend ya conoce. Que el cliente la mandara permitiría reportar al taller
    una fecha que no corresponde al evento.
    """

    fault_row_id: str = Field(min_length=1, max_length=128)
    priority: Literal["low", "medium", "high"] = "medium"
    comment: str | None = Field(default=None, max_length=4000)
    odometer: Decimal | None = Field(default=None, ge=0)
    send_mail: bool = False


class NavifaultEscalationResponse(BaseModel):
    novedad_id: UUID
    cloudfleet_issue_number: int | None = None
    status: FaultManagementState
    escalated_at: datetime
    #: False cuando la `Idempotency-Key` reencontró una novedad ya existente: no
    #: se volvió a llamar al proveedor.
    created: bool


class NavifaultOrderWorkRequest(BaseModel):
    """Orden de CloudFleet contra la que se quiere gestionar una falla."""

    fault_row_id: str = Field(min_length=1, max_length=128)
    work_order_number: int = Field(gt=0)


class NavifaultOrderPartRead(BaseModel):
    """Repuesto consumido por un trabajo.

    `code` llega vacío en CloudFleet y la referencia de la pieza viaja dentro
    del nombre, así que el nombre es el dato.
    """

    id: int | None = None
    name: str | None = None
    code: str | None = None
    qty: float | None = None


class NavifaultOrderLaborRead(BaseModel):
    """Trabajo de la orden que lleva la referencia de esta falla."""

    id: int | None = None
    name: str | None = None
    code: str | None = None
    system: str | None = None
    subsystem: str | None = None
    maintenance_type: str | None = None
    #: Con repuestos se cambió una pieza; sin ellos se intervino sin cambiarla.
    #: Sale del dato real y no de lo que alguien escribió.
    replaced_component: bool = False
    parts: list[NavifaultOrderPartRead] = Field(default_factory=list)


class NavifaultOrderWorkResponse(BaseModel):
    """Lo que el taller registró para esta falla en esa orden."""

    work_order_number: int
    work_order_status: str | None = None
    #: La referencia que se buscó. Se publica para que la pantalla pueda decir
    #: exactamente qué hay que copiar cuando no encuentra nada.
    reference: str
    #: ¿La orden terminó? Una falla se gestiona confirmando lo que el taller
    #: TERMINÓ, así que con la orden abierta no hay nada que confirmar todavía.
    #: Lo decide el backend y la pantalla sólo lo obedece: replicar aquí la
    #: lista de estados de cierre es exactamente cómo las dos versiones de una
    #: regla se separan sin que nada lo detecte.
    work_order_finished: bool = False
    labors: list[NavifaultOrderLaborRead] = Field(default_factory=list)


class NavifaultFaultEvidenceEntry(BaseModel):
    """Qué se hizo con esta misma falla en OTRO vehículo, sin decir en cuál.

    No lleva placa, vehículo, flota ni autor, y no es un descuido: la evidencia
    cruza todas las flotas porque el conocimiento técnico sobre un código es de
    Navitrans, mientras que el dato operativo es del cliente.
    """

    #: Cierre con nota —fallas del equipo telemático—: es lo único que quedó.
    note: str | None = None
    #: Confirmación contra una orden: el número NO se publica, sólo si la hubo.
    from_work_order: bool = False
    labors: list[NavifaultOrderLaborRead] = Field(default_factory=list)
    managed_at: datetime
    #: `held` no volvió tras la ventana · `returned` volvió · `pending` la
    #: ventana no ha transcurrido, así que todavía no se sabe. Tres estados y no
    #: dos: afirmar que funcionó antes de que pase el plazo es exactamente lo
    #: que la ventana de 30 días existe para impedir.
    verification: Literal["held", "returned", "pending"]


class NavifaultFaultEvidenceResponse(BaseModel):
    entries: list[NavifaultFaultEvidenceEntry] = Field(default_factory=list)
    #: Días de la ventana de verificación, para que la pantalla no los repita.
    verification_window_days: int


class NavifaultConfirmRequest(NavifaultOrderWorkRequest):
    """Confirmar lo registrado en la orden es lo que gestiona la falla."""

    note: str | None = Field(default=None, max_length=4000)


class NavifaultUnmarkFaultManagedRequest(BaseModel):
    """Cuerpo de Deshacer: devuelve una falla gestionada a pendiente.

    Lleva sólo una nota porque deshacer no declara un desenlace: retira el que
    había, y lo que hace falta es el motivo. Se llamaba
    `NavifaultMarkFaultManagedRequest` y lo compartía con `mark-managed`, que se
    retiró el 2026-09-01 cuando gestionar pasó a exigir el desenlace.
    """

    fault_row_id: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=4000)


class NavifaultManagedFaultActionRead(BaseModel):
    action_id: UUID
    action_type: Literal["managed", "unmanaged", "escalated"]
    managed_at: datetime
    managed_through_at: datetime | None = None
    managed_through_row_id: str | None = None
    actor_name: str | None = None
    note: str | None = None
