# Arquitectura y evolución de PostgreSQL

## Objetivo

Este documento define el contrato operativo de la base de Navi Portal Clientes.
El objetivo no es maximizar índices ni aplicar tuning genérico, sino mantener:

- aislamiento multi-flota fail-closed;
- latencia predecible al crecer flotas e historia;
- transacciones cortas y recuperables;
- integridad validada por PostgreSQL, no solo por Pydantic/ORM;
- cambios online, versionados y reversibles;
- capacidad observable y presupuestada por proceso.

Las fuentes ejecutables siguen siendo `apps/api/alembic/` para `public` y
`InformesRendimiento/load/schema.py` para `analytics`.

## Fotografía inspeccionada (2026-07-27)

La inspección fue de metadatos y estadísticas agregadas en PostgreSQL 16; no se
leyeron filas de negocio. La base observada tenía 44 tablas:

| Relación | Filas estimadas | Tamaño total |
| --- | ---: | ---: |
| `analytics.fact_location_point` | 3.315.087 | 1.357 MB |
| `analytics.fact_fault_event` | 334.433 | 292 MB |
| `analytics.fact_altimetria_reading` | 506.451 | 207 MB |
| `analytics.fact_pedal_reading` | 87.444 | 39 MB |
| `analytics.fact_habito_event` | 42.594 | 18 MB |
| `public.cloudfleet_maintenance_schedules` | 3.918 | 9,7 MB |

Se observaron millones de accesos repetidos a `analytics.dim_vehicle` y faltaban
índices para su join por `device_id`, el hecho mensual y los órdenes temporales
de eventos. `pg_stat_statements` no estaba habilitado, por lo que esta evidencia
permite corregir multiplicadores estructurales, pero no sustituye planes
`EXPLAIN (ANALYZE, BUFFERS)` en una réplica anonimizada.

## Diseño vigente

### `public`: OLTP y coordinación

- UUID para identidad interna; claves naturales tienen `UNIQUE` cuando son
  realmente estables.
- FK con política de borrado explícita.
- asociaciones N:N con PK compuesta y un índice inverso para deletes/joins desde
  cualquiera de los dos lados.
- colas con índices parciales sobre estados activos; los estados terminales no
  deben dominar el hot index.
- checks de estados y orden temporal para rechazar valores imposibles incluso
  desde workers o SQL directo.
- migraciones de índices mediante `CREATE INDEX CONCURRENTLY`.

### `analytics`: modelo semántico

- pertenece al ETL, no a Alembic;
- el API solo lee;
- los facts filtran primero por `vehicle_id` y clave temporal;
- el alcance de flota se incorpora como subquery SQL y nunca se basa en
  `database_name`;
- el contrato declara índices específicos; `index=True` en los modelos read-only
  no crea DDL.

## Presupuesto de conexiones y timeouts

Cada proceso crea su propio pool. El default del runtime es:

| Parámetro | Default por proceso |
| --- | ---: |
| pool persistente | 5 |
| overflow | 5 |
| espera por conexión | 10 s |
| reciclado | 1.800 s |
| statement timeout | 30 s |
| lock timeout | 5 s |
| idle in transaction | 60 s |

Son límites defensivos, no valores universales. Antes de aumentar workers:

```text
conexiones máximas de app =
  suma_procesos(pool_size + max_overflow)
```

La suma de aplicación, migraciones, ETL, administración y reserva de emergencia
debe permanecer normalmente por debajo del 70–80 % de `max_connections`.
PgBouncer en modo transaction es el siguiente paso cuando existan múltiples
réplicas de API; no corrige consultas lentas ni transacciones largas.

## Concurrencia y transacciones

- Los syncs master y CloudFleet usan advisory locks de sesión con nombres
  estables. Un trigger concurrente falla rápido; no espera indefinidamente.
- El lock no mantiene una transacción abierta durante HTTP.
- CloudFleet hace fetch fuera de SQL, confirma vehículos por recurso, órdenes
  por chunk y agendas mediante reemplazo atómico de ventana.
- Los upserts agrupan hasta 500 filas, deduplican claves dentro del batch y
  aplican `IS DISTINCT FROM` para no generar WAL/bloat si el payload no cambió.
- `FOR UPDATE SKIP LOCKED` se reserva para consumidores de cola.
- Toda nueva ruta que tome varios locks debe documentar un orden global de
  adquisición. Nunca llamar red/MinIO mientras se retiene un row lock.

## Índices: reglas

Un índice se acepta cuando responde a filtro, join, orden o constraint real.
Cada índice adicional aumenta WAL, memoria, vacuum y costo de ingestión.

Índices principales añadidos:

- asociaciones: `(role_id,user_id)`, `(permission_id,role_id)` y
  `(fleet_id,user_id)`;
- vehículos: `(fleet_id,plate,id)` y variante parcial activa;
- novedades: `(fleet_id,reported_at,id)`;
- extracción/colas: índices parciales solo para trabajo pendiente;
- analytics: `dim_vehicle(device_id)`,
  `fact_combustible_monthly(vehicle_id,month_start_date_key)` y órdenes
  `(vehicle_id,date_key,timestamp,pk)` para hábitos, fallas y pedal.

Revisar trimestralmente `pg_stat_user_indexes`. No borrar un índice por
`idx_scan=0` sin cubrir al menos un ciclo de negocio/ETL y verificar constraints.

## Evolución y particionado

No se convierte automáticamente `fact_location_point` porque su PK actual no
incluye la clave de partición y una conversión online requiere contrato de IDs,
backfill y cutover. El umbral de diseño ya se alcanzó por tamaño, pero la
migración debe seguir estas fases:

1. fijar una PK compatible, por ejemplo `(date_key, log_row_id)`;
2. crear tabla nueva particionada por rango mensual de `date_key`;
3. crear partición default y particiones futuras antes de escribir;
4. copiar por mes con checksums/conteos y throttling;
5. dual-write o pausar ingestión para un delta final acotado;
6. validar FK, consultas y retención;
7. renombrar en una ventana corta y conservar rollback;
8. automatizar creación de particiones y alertar si se usa la default.

Aplicar el mismo patrón a otros facts solo cuando planes y crecimiento lo
justifiquen. Particionar tablas pequeñas suele empeorar planificación y
mantenimiento.

## Seguridad de datos pendiente

La separación de schemas no es aislamiento de privilegios por sí sola. La fase
siguiente debe crear roles distintos:

- owner/migrator sin login de aplicación;
- API OLTP con DML mínimo;
- API analytics read-only;
- ETL con write en `analytics` y lectura maestra mínima;
- workers limitados a sus tablas;
- operador de backup/monitorización.

Después se debe revocar `PUBLIC`, fijar `search_path`, evaluar RLS con
`FORCE ROW LEVEL SECURITY` y propagar un contexto de flota por transacción.
RLS no se activa hasta tener pruebas negativas de admin, usuario multi-flota,
worker y migración: una política incompleta puede bloquear producción o dar una
falsa sensación de aislamiento.

## Operación

Requisitos antes de producción:

- backup PostgreSQL + WAL/PITR fuera del host;
- restore drill automatizado con RPO/RTO medidos;
- réplica de lectura para analytics cuando la carga mixta degrade OLTP;
- `pg_stat_statements`, métricas de pool, locks, temp, WAL y dead tuples;
- autovacuum específico para tablas de alto churn, calibrado con métricas;
- planes antes/después en fixture anonimizado 1x y 5x.

Nunca ejecutar cargas ni `EXPLAIN ANALYZE` costoso contra producción. Los cambios
de índices grandes deben desplegarse online, vigilar progreso/locks y tener
presupuesto de disco para heap + índice viejo/nuevo + WAL.
