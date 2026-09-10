# Requisitos Previos al Lanzamiento a Producción

> Borrador para revisión jurídica. No es asesoría legal.
> Documento transversal: no es una política, es la lista de lo que debe existir
> antes de que Portal Clientes opere con clientes accediendo directamente.
> Fecha: 2026-08-27.

Portal Clientes está en desarrollo, pero **trata datos personales reales desde
hoy** (ver documento 00). Por eso este documento separa dos horizontes:

- **Bloque A — ya obliga.** Hay tratamiento real en curso; el incumplimiento es
  actual, no futuro.
- **Bloque B — antes del lanzamiento.** Exigible cuando el producto pase a
  operación con clientes.

---

## Bloque A — Obligaciones que ya corren

| # | Qué | Documento | Estado |
| --- | --- | --- | --- |
| A1 | **Decidir el entorno que sirve datos reales.** Elevarlo a grado productivo, o separarlo y usar datos anonimizados en desarrollo | 00 §3, 06 | **No resuelto** |
| A2 | **Cifrado en tránsito.** El despliegue no incluye terminación TLS | 06 | **No resuelto** |
| A3 | **Evaluar SEC-001 y SEC-002 como posibles incidentes notificables.** El plazo corre desde el conocimiento, documentado el 2026-07-23 | 09 | **No evaluado** |
| A4 | **Definir responsable vs. encargado** frente a los clientes de flota | 11 | **No resuelto** |
| A5 | **Verificar la ubicación de los proveedores** y si hay transferencia internacional | 08 | **No verificado** |
| A6 | **Suprimir o justificar los datos huérfanos**: 1,29 M de puntos de ubicación sin finalidad ni consumidor desde julio de 2026 | 07 | **No resuelto** |
| A7 | **Designar responsable de atención al titular** (art. 23) | 10 | **No designado** |
| A8 | **Evaluar inscripción en el Registro Nacional de Bases de Datos** | 00 | **No verificado** |

**[DECISIÓN JURÍDICA]** A1 y A3 son los de mayor exposición. A3 además tiene
plazo: cuanto más tarde la evaluación, más difícil sostener que se actuó con
diligencia.

---

## Bloque B — Antes del lanzamiento

### B.1 Capacidades de producto que las políticas exigen y hoy no existen

| # | Capacidad | Por qué | Documento |
| --- | --- | --- | --- |
| B1 | **Registro de autorización**: quién aceptó, qué versión y cuándo | Art. 9 y 12: la carga de la prueba es del responsable | 01 |
| B2 | **Aviso de privacidad visible** antes de la recolección, con versionado | El portal no muestra ninguno hoy | 02 |
| B3 | **Términos y condiciones con aceptación acreditable** | No existen | 12 |
| B4 | **Canal para ejercer derechos**, con registro de solicitudes y plazos | No existe; sin registro no hay cómo acreditar cumplimiento | 10 |
| B5 | **Exportación y supresión a solicitud** | Hoy se haría a mano contra la base de datos | 07, 10 |
| B6 | **Aplicación automática de plazos de conservación** | Hoy nada se borra salvo los tokens de sesión | 07 |
| B7 | **Aviso de contenido generado por IA** en correo y portal | El texto llega hoy sin identificarse como tal | 04 |
| B8 | **Declaración de cobertura en los indicadores** | El 11,4 % de los días de combustible se excluye sin avisar | 12 §2.4 |

**[DECISIÓN JURÍDICA]** B1 a B4 son requisitos de cumplimiento sin sustituto: no
hay forma de acreditar autorización, información ni atención de derechos sin
ellos. B5 y B6 admiten un procedimiento manual documentado **mientras la escala lo
permita**, pero eso debe decidirse expresamente y revisarse al crecer.

### B.2 Decisiones jurídicas que condicionan el desarrollo

| # | Decisión | Consecuencia técnica si se resuelve tarde |
| --- | --- | --- |
| B9 | Calificación de la telemetría georreferenciada como dato personal | Determina si B1–B6 aplican a 81.535 eventos mensuales o solo a los usuarios del portal |
| B10 | Quién obtiene la autorización de los conductores | Puede exigir desarrollo en el portal si no basta la vía contractual |
| B11 | Plazos de conservación por categoría | B6 no se puede construir sin ellos |
| B12 | Si se exige revisión humana previa al envío de comunicaciones de IA | Cambia el flujo del módulo Navifault y su diseño de colas |
| B13 | Restricción horaria de la visualización de telemetría | Cambia el modelo de consulta |
| B14 | Si el mapa sigue usando un tercero externo | Determina si hay que servir las imágenes desde infraestructura propia |

**[DECISIÓN JURÍDICA]** B9 es la decisión de la que dependen casi todas las demás.
Resolverla temprano evita construir dos veces.

### B.3 Requisitos de seguridad

| # | Requisito | Referencia |
| --- | --- | --- |
| B15 | Separación de privilegios en base de datos | SEC-014 |
| B16 | Restauración de respaldo probada | SEC-018 |
| B17 | Actualización de componentes con vulnerabilidades críticas | SEC-003 |
| B18 | Validación real de archivos adjuntos y antivirus | SEC-008 |
| B19 | Corregir el cierre de sesión que informa éxito sin revocar | SEC-016 |
| B20 | Mensajes de error sin detalle interno | SEC-023 |
| B21 | Protección contra falsificación de peticiones | SEC-021 |

---

## Orden propuesto

**[DECISIÓN JURÍDICA] Es una propuesta de ingeniería sobre riesgo y dependencia;
la prioridad legal la fija el área jurídica.**

1. **A3** — evaluar los posibles incidentes. Tiene plazo y no depende de nada.
2. **A1 y A2** — decidir el entorno y el cifrado. Es el riesgo activo más grande.
3. **A4 y B9** — responsable/encargado y calificación de la telemetría. De aquí
   cuelga casi todo lo demás.
4. **A6** — suprimir los datos sin finalidad. Barato y elimina exposición.
5. **B1 a B4** — las capacidades de cumplimiento, en paralelo al desarrollo.
6. **B7** — el aviso de IA. Es barato y cierra una discusión incómoda.
7. **B15 a B21** — el endurecimiento de seguridad, antes de abrir a clientes.

---

## Lo que este documento no resuelve

- La fecha objetivo de lanzamiento, que condiciona todos los plazos.
- Si habrá una fase piloto con clientes reales, y bajo qué condiciones. **Un
  piloto con clientes accediendo es lanzamiento a efectos legales**, aunque el
  producto se llame beta.
- Si algún cliente de flota es entidad pública, lo que activaría requisitos
  adicionales de accesibilidad y contratación estatal (documento 13).
