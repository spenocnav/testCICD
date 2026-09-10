# Contrato de integración — Lo que recibe Portal Clientes

> Documento hermano: [plan-integracion-portal-clientes.md](plan-integracion-portal-clientes.md)
> (cambios necesarios en Navi Vehículos para producir esto).

Navi Vehículos es la **fuente de verdad** de clientes, databases Geotab,
credenciales, reglas y vehículos. Portal Clientes mantiene una **réplica local de
solo lectura**, sincronizada por HTTP. Portal Clientes nunca edita estos datos; si
algo está mal, se corrige en Navi Vehículos y la sync lo propaga.

---

## 1. Autenticación

- Header `X-API-Key: <clave>` en cada request.
- La clave se configura en Navi Vehículos (`INTEGRATION_API_KEYS`, admite varias para
  rotación) y en Portal Clientes como secreto de entorno (`NAVI_API_KEY`).
- Solo HTTPS. Respuestas `401` si falta/es inválida la clave; `503` si Navi
  Vehículos no tiene `INTEGRATION_API_KEYS` configurada.

> **Estado**: este contrato ya está implementado en Navi Vehículos
> (`app/api/routes/integration.py` + `app/services/integration_export.py`).

## 2. Endpoint principal

```
GET {NAVI_BASE_URL}/api/v1/integration/snapshot
    ?since=2026-06-10T00:00:00Z          (opcional — sync incremental)
    &include_credentials=true            (opcional — default false)
```

- Sin `since`: snapshot completo (full sync).
- Con `since`: solo registros con `updated_at > since` (sync incremental).
- `include_credentials=true`: agrega `password_enc` a cada credencial. Usarlo solo
  desde el job de sync del backend de Portal Clientes — nunca desde un browser.

**La contraseña de Geotab nunca viaja en claro.** `password` lleva siempre la
máscara `********`; el secreto va en `password_enc` como token Fernet.

Hay **dos claves con papeles distintos**, y no se deben fusionar:

| clave | dónde vive | qué protege |
| --- | --- | --- |
| `INTEGRATION_FERNET_KEY` | solo Navi Vehículos | su propio reposo |
| `SNAPSHOT_TRANSPORT_FERNET_KEY` | Navi Vehículos | el `password_enc` del snapshot |
| `MASTER_FERNET_KEY` | Portal Clientes e InformesRendimiento | el reposo del portal |

La de transporte **es** la `MASTER_FERNET_KEY` del portal: por eso el portal
puede guardar el token tal cual, sin descifrarlo. Navi descifra con su clave de
reposo y vuelve a cifrar con la de transporte al construir el snapshot.

Ese paso extra es deliberado y compra dos cosas:

1. el snapshot atraviesa un CDN que termina TLS, donde la contraseña en claro
   sería legible por el intermediario;
2. los dos reposos quedan independientes. **Rotar `MASTER_FERNET_KEY` es cambiar
   una variable en Navi**, sin re-cifrar su base; y rotar la clave de reposo de
   Navi no toca al portal ni al ETL.

`password` conserva la máscara en lugar de desaparecer: un consumidor que no
conozca `password_enc` la lee, deja intacto lo que ya tiene y el orden de
despliegue de las dos aplicaciones deja de importar.

Los dos fallos posibles —el valor en reposo no descifra, o falta la clave de
transporte— son fail-closed: el snapshot sale sin `password_enc`. **No hay
passthrough**, porque su efecto sería publicar la contraseña en claro.

Para re-cifrar el reposo de Navi entre claves:
`python -m app.jobs.rotate_credential_key --apply`, idempotente y sin tocar una
fila que no pueda descifrar.

### 2.1 Respuesta (shape completo)

```json
{
  "generated_at": "2026-06-11T14:30:00Z",
  "since": null,
  "customers": [
    {
      "id": 12,
      "name": "Transportes El Roble",
      "range_mode": "reglas",
      "updated_at": "2026-06-01T10:00:00Z",
      "databases": [
        {
          "id": 31,
          "database_name": "el_roble_sa",
          "database_key": "el_roble_sa",
          "connection_type": "geotab",
          "access_url": null,
          "provider_config": { "plate_prefix": "TR" },
          "updated_at": "2026-06-01T10:00:00Z",
          "credentials": [
            {
              "id": 7,
              "username": "reportes@navitrans.com.co",
              "password": "********",
              "password_enc": "gAAAAAB...",
              "label": "cuenta reportes",
              "is_active": true,
              "updated_at": "2026-06-01T10:00:00Z"
            },
            {
              "id": 8,
              "username": "reportes2@navitrans.com.co",
              "password": "********",
              "password_enc": "gAAAAAB...",
              "label": "cuenta secundaria",
              "is_active": true,
              "updated_at": "2026-06-01T10:00:00Z"
            }
          ],
          "rules": [
            {
              "rule_source_id": 501,
              "application_id": 101,
              "rule_id": "aB1cD2eF3gH",
              "name": "RPM > 2200 (ISD)",
              "category": "operacion",
              "motor_type": "ISD",
              "event_type": null,
              "band": "exceso_rpm",
              "is_descenso": false,
              "description": null,
              "created_at": "2026-05-20T09:00:00Z"
            },
            {
              "rule_source_id": 501,
              "application_id": 104,
              "rule_id": "aB1cD2eF3gH",
              "name": "RPM > 2200 (ISD)",
              "category": "habito_seguro",
              "motor_type": "ISD",
              "event_type": "exceso_rpm",
              "band": null,
              "is_descenso": false,
              "description": "Excesos de RPM",
              "created_at": "2026-05-20T09:00:00Z"
            },
            {
              "id": 103,
              "rule_id": "aR7sT8uV9wX",
              "name": "RPM > 2000 (X15)",
              "category": "operacion",
              "motor_type": "X15",
              "band": "rango_potencia",
              "is_descenso": true,
              "created_at": "2026-05-20T09:02:00Z"
            },
            {
              "id": 102,
              "rule_id": "aX9yZ8wV7uT",
              "name": "Frenada brusca",
              "category": "habito_seguro",
              "description": "Frenadas bruscas",
              "motor_type": null,
              "band": null,
              "is_descenso": false,
              "created_at": "2026-05-20T09:05:00Z"
            }
          ]
        }
      ]
    }
  ],
  "vehicles": [
    {
      "plate": "ABC123",
      "vin": "3HSDJAPR1KN123456",
      "geotab_device_id": "b1F2",
      "geotab_device_synced_at": "2026-06-10T03:00:00Z",
      "customer_id": 12,
      "customer_database_id": 31,
      "geotab_customer_database_id": 31,
      "geotab_customer_status": "found",
      "engine_number": "79123456",
      "technical_number": "D103005BX03",
      "cpl": "4955",
      "marca": "INTERNATIONAL",
      "linea": "PROSTAR",
      "marketing_model_name": "ProStar",
      "service_model_name": "ProStar 73 in Sky-Rise Sleeper",
      "ano_modelo": "2023",
      "tipo_combustible": "DIESEL",
      "nombre_vehiculo": "ABC123 - PROSTAR",
      "vocacional": false,
      "motor_type": "ISD",
      "updated_at": "2026-06-10T03:00:00Z"
    }
  ]
}
```

Notas sobre los campos clave:

| Campo | Significado |
|---|---|
| `databases[].database_key` | **Clave de la db física de Geotab** (`database_name` en minúsculas). El nombre de database es único globalmente en MyGeotab, así que dos filas con el mismo `database_key` —aunque pertenezcan a clientes distintos— son **la misma database** y comparten reglas y credenciales. Ver §3.1. |
| `vehicles[].geotab_device_id` | **Código interno de Geotab** del vehículo **en la db del cliente** (`geotab_customer_database_id`). Es el id que Portal Clientes usa para llamar a la API de Geotab (`deviceSearch: {id: ...}`). |
| `vehicles[].geotab_customer_status` | `found` / `not_found` / `unknown` / `not_applicable`. Solo confiar en `geotab_device_id` cuando es `found`. |
| `databases[].provider_config.plate_prefix` | Algunos clientes nombran devices con prefijo (ej. device `TRABC123` para placa `ABC123`). Relevante si Portal Clientes busca por placa en vez de por id. |
| `rules[].category` | `operacion` (reglas de motor) o `habito_seguro`. `rule_id` es el id nativo de Geotab para consultar `ExceptionEvent` (`ruleSearch: {id: ...}`). |
| `rules[].application_id` / `rules[].source_id` | Identidad estable primaria de la aplicación. Dos aplicaciones pueden compartir `rule_id`; no se deduplican entre categorías. Sin este id, la identidad lógica es `rule_id + category + motor_type`. |
| `rules[].motor_type` | **Tipo de motor al que aplica la aplicación.** Es obligatorio para `operacion`. En `habito_seguro`, `null` conserva las reglas globales históricas; la aplicación derivada con `event_type = exceso_rpm` lleva el mismo motor de su aplicación de operación y solo aplica a ese motor. |
| `rules[].event_type` | Semántica estable de la aplicación de hábitos. Para la derivada de exceso de RPM es `exceso_rpm`; Portal Clientes no la infiere desde `rules[].name` ni crea la aplicación. |
| `rules[].band` | **Banda de RPM que mide la regla**, declarada explícitamente por Navi Vehículos. Enum: `rango_bajo`, `rango_economico`, `rango_balanceado`, `rango_potencia`, `rango_potencia_ineficiente`, `exceso_rpm`, `ralenti`. `null` para `habito_seguro` y para snapshots de versiones anteriores al campo. Es la **fuente primaria** del mapeo del ETL; sin ella el ETL infiere la banda del nombre por palabra clave, lo cual es frágil ante renombres (una regla renombrada desaparece del cálculo de `% por rango`). Portal Clientes reporta las reglas de `operacion` sin banda en `/gestion/calidad-datos` (métrica `rules_without_band`). |
| `rules[].is_descenso` | Booleano, nunca `null` (default `false`). `true` = la regla mide el tramo **en descenso** de esa banda; el ETL la reporta como dimensión aparte (`Rango Bajo Descenso`, …). Inválido con `band = ralenti` o con `band = null`; Portal Clientes lo degrada a `false` en ambos casos. |
| `rules[].description` | **Clasificación estable de una regla de `habito_seguro`**, independiente del nombre físico configurado en Geotab. Valores: `Excesos de velocidad`, `Giros bruscos`, `Excesos de RPM`, `Frenadas bruscas`, `Baches o Resaltos fuertes`, `Aceleraciones bruscas`. Es `null` para `operacion`. En snapshots antiguos de exceso de RPM, Portal usa `event_type = exceso_rpm` como compatibilidad, nunca el nombre físico. |
| `vehicles[].motor_type` | Tipo de motor del vehículo (catálogo controlado). Define qué reglas de `operacion` recibe. Puede ser `null` (vehículo sin motor asignado → solo recibe `habito_seguro`); esos vehículos deben reportarse para completarlos. |
| `vehicles[].marketing_model_name` | Nombre comercial del modelo asignado al vehículo desde `vehicle_motor_assignments`. Es el nombre recomendado para mostrar al cliente cuando existe. |
| `vehicles[].service_model_name` | Nombre de modelo de servicio asignado al vehículo desde `vehicle_motor_assignments`. Sirve para detalle técnico/posventa y como fallback si no hay modelo comercial. |
| `vehicles[].vocacional` | Booleano, **nunca `null`** (default `false` en origen). `true` = uso vocacional; `false` = transporte/comercial. Atributo del vehículo; respeta el filtro incremental `?since=`. |
| `credentials[]` | Pool de credenciales de esa db. Portal Clientes debe **rotar** entre las activas (round-robin o LRU) para no saturar una sola sesión Geotab. |

### 2.2 Endpoints de conveniencia

```
GET /api/v1/integration/vehicles?since=...&limit=500&offset=0
GET /api/v1/integration/customers?include_credentials=true
```

Mismos shapes que las secciones correspondientes del snapshot, con paginación para
`vehicles` (flotas grandes; `limit` máx. 2000). La respuesta de `/vehicles` incluye
`limit`, `offset` y `count` además del array `vehicles`.

### 2.3 Modo de rangos por cliente (`range_mode`)

Cada cliente trae `range_mode` (`"reglas"` | `"rpm"`, nunca null), que define de
dónde salen las bandas de RPM de esa flota:

- `"reglas"` (default): bandas derivadas de las reglas Geotab del cliente
  (`band` / `is_descenso` de cada aplicación). Es el comportamiento histórico.
- `"rpm"`: las reglas no se usan; el tiempo por banda se calcula cortando el eje
  de revoluciones con los rangos del motor (§2.4).

Se decide **por cliente**, no por database ni por vehículo. El toggle vive en
Navi Vehículos; Portal Clientes lo replica en `fleets.range_mode` y lo muestra
read-only en el detalle de flota ("Rangos por Reglas" / "Rangos por RPM").
Un payload viejo sin el campo, o con un valor desconocido, se degrada a
`"reglas"`: nunca cambia el comportamiento de una flota por un dato inesperado.

### 2.4 Rangos de RPM y velocidades por motor (`motors[]`)

`/integration/snapshot` y `/integration/customers` traen un arreglo `motors` de
primer nivel:

```json
"motors": [
  {
    "motor_type": "X11",
    "updated_at": "2026-08-18T13:00:00Z",
    "governed_speed_rpm": 1925,
    "max_overspeed_rpm": 2375,
    "rpm_bands": [
      { "band": "rango_bajo",                 "rpm_min": 600,  "rpm_max": 1100 },
      { "band": "rango_economico",            "rpm_min": 1100, "rpm_max": 1450 },
      { "band": "rango_balanceado",           "rpm_min": 1450, "rpm_max": 1800 },
      { "band": "rango_potencia",             "rpm_min": 1800, "rpm_max": 2300 },
      { "band": "rango_potencia_ineficiente", "rpm_min": 2300, "rpm_max": 2750 },
      { "band": "exceso_rpm",                 "rpm_min": 2750, "rpm_max": null  }
    ]
  },
  {
    "motor_type": "F2.8",
    "updated_at": "...",
    "governed_speed_rpm": null,
    "max_overspeed_rpm": null,
    "rpm_bands": []
  }
]
```

- `governed_speed_rpm` (velocidad nominal gobernada sin carga) y
  `max_overspeed_rpm` (capacidad máxima de sobrevelocidad) son datos de la hoja
  técnica del fabricante, en RPM. **Aditivos y nullable**: `null` = aún no
  capturado en la fuente, nunca 0. No dependen de `range_mode`: viajan para
  todos los motores. El portal descarta el par entero si no es numérico, si no
  es positivo o si la sobrevelocidad queda por debajo de la gobernada.
- `rpm_min` inclusivo, `rpm_max` exclusivo; `null` solo en la banda más alta.
- La partición debe cubrir el eje completo, contigua y sin solapes. El portal
  valida lo mismo al ingerir y, si algo no cuadra, **descarta el motor entero**
  (queda "sin configurar") en vez de guardar media partición.
- El tiempo por debajo de `rango_bajo.rpm_min` no pertenece a ninguna banda.
- `ralenti` no es un tramo del eje: en este modo se deriva de la telemetría
  (motor encendido y velocidad 0).
- `rpm_bands: []` = motor sin configurar. El ETL **se salta** esos vehículos y el
  portal lo reporta en Calidad de datos ("Motor sin rangos de RPM").
- `motors` viaja completo también en las llamadas incrementales (`since`).

Réplica local: tabla `motor_rpm_bands` (`motor_type`, `band`, `rpm_min`,
`rpm_max`), reemplazada por completo en cada sync. Las velocidades van en
`motor_catalog.governed_speed_rpm` / `max_overspeed_rpm`, y se ven en
Gestión → Flotas → detalle, panel **Motores**
(`GET /api/v1/fleets/{fleet_id}/motors`).

---

## 3. Tablas sugeridas en Portal Clientes

Réplica con ids de origen (`source_id`) para upserts idempotentes, y `synced_at`
para detectar registros que dejaron de venir. PostgreSQL:

### 3.1 Regla de oro: la db física se identifica por `database_key`

Varios clientes pueden compartir la misma database de Geotab (ej. dos clientes
dentro de la db `navitrans`). En el snapshot, cada cliente trae su propia fila de
database, pero **las reglas y credenciales viven bajo la fila donde se
registraron** (sin duplicar). Por eso, en Portal Clientes:

- **Reglas de un vehículo** = reglas de **todas** las filas de database cuyo
  `database_key` coincide con el de la database asignada al vehículo — no solo
  las de su `database_id` — **cruzadas por el tipo de motor del vehículo**: las
  de cualquier categoría con `motor_type` solo aplican si coincide con
  `vehicles.motor_type`; las aplicaciones históricas de `habito_seguro` con
  `motor_type IS NULL` aplican a todos.
- **Credenciales para consultar Geotab** = pool de todas las filas con el mismo
  `database_key` (rotar entre todas).

```sql
-- Aplicaciones para un vehículo; consultar cada r.rule_id físico una sola vez:
SELECT r.*, app.*
FROM vehicles v
JOIN geotab_databases d   ON d.id = v.database_id
JOIN geotab_databases sib ON sib.database_key = d.database_key
JOIN geotab_rules r       ON r.database_id = sib.id
JOIN geotab_rule_applications app ON app.geotab_rule_id = r.id
WHERE r.is_active AND app.is_active
  AND (
    (app.category = 'habito_seguro' AND app.motor_type IS NULL)
    OR app.motor_type = v.motor_type
  );
-- Si v.motor_type IS NULL, el vehículo solo recibe habito_seguro: reportarlo.
```

```sql
CREATE TABLE customers (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL UNIQUE,        -- customers.id en Navi Vehículos
    name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE geotab_databases (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL UNIQUE,        -- customer_databases.id
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    database_name TEXT NOT NULL,
    database_key TEXT NOT NULL,              -- db FISICA: filas con la misma key comparten reglas/credenciales
    connection_type TEXT NOT NULL,
    plate_prefix TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_geotab_databases_key ON geotab_databases (database_key);

CREATE TABLE geotab_credentials (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL UNIQUE,        -- customer_database_credentials.id
    database_id BIGINT NOT NULL REFERENCES geotab_databases(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,        -- cifrar al guardar (Fernet)
    label TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ NULL,           -- rotación local del pool
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Catálogo controlado de tipos de motor (vocabulario compartido por
-- vehicles.motor_type y geotab_rules.motor_type, para que el cruce no falle
-- por typos/mayúsculas).
CREATE TABLE motor_catalog (
    motor_type TEXT PRIMARY KEY,             -- ISD, X15, B6.7, ...
    description TEXT NULL
);

CREATE TABLE geotab_rules (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL UNIQUE,        -- geotab_rules.id
    database_id BIGINT NOT NULL REFERENCES geotab_databases(id) ON DELETE CASCADE,
    rule_id TEXT NOT NULL,                   -- id nativo Geotab
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('operacion', 'habito_seguro')),
    motor_type TEXT NULL REFERENCES motor_catalog(motor_type) ON DELETE RESTRICT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (database_id, rule_id, category, motor_type),
    -- operacion => motor obligatorio; habito_seguro => motor NULL.
    CHECK (
        (category = 'operacion'     AND motor_type IS NOT NULL) OR
        (category = 'habito_seguro' AND motor_type IS NULL)
    )
);

CREATE TABLE vehicles (
    id BIGSERIAL PRIMARY KEY,
    plate VARCHAR(10) NOT NULL UNIQUE,       -- clave natural compartida
    vin TEXT NULL,
    geotab_device_id TEXT NULL,              -- código interno Geotab (db del cliente)
    customer_id BIGINT NULL REFERENCES customers(id),
    database_id BIGINT NULL REFERENCES geotab_databases(id),
    geotab_customer_status TEXT NOT NULL DEFAULT 'unknown',
    engine_number TEXT NULL,
    technical_number TEXT NULL,
    marca TEXT NULL,
    linea TEXT NULL,
    marketing_model_name TEXT NULL,          -- modelo comercial desde vehicle_motor_assignments
    service_model_name TEXT NULL,            -- modelo servicio desde vehicle_motor_assignments
    ano_modelo TEXT NULL,
    tipo_combustible TEXT NULL,
    nombre_vehiculo TEXT NULL,
    vocacional BOOLEAN NOT NULL DEFAULT FALSE,  -- true = uso vocacional; false = transporte/comercial
    motor_type TEXT NULL REFERENCES motor_catalog(motor_type) ON DELETE RESTRICT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_vehicles_customer ON vehicles (customer_id);
CREATE INDEX idx_vehicles_motor_type ON vehicles (motor_type);
CREATE INDEX idx_rules_database_category ON geotab_rules (database_id, category);
CREATE INDEX idx_rules_motor_type ON geotab_rules (motor_type);
```

---

## 4. Cómo consumirlo (job de sync)

### 4.1 Estrategia

| Mecanismo | Frecuencia | Qué hace |
|---|---|---|
| **Sync incremental** | cada 15–60 min (cron/APScheduler) | `GET /snapshot?since=<último sync exitoso>` → upsert por `source_id` / `plate`. |
| **Full sync** | 1 vez al día | `GET /snapshot` sin `since` → upsert todo + marcar `is_active = false` a lo que **no** vino (detección de borrados). |

Guardar el watermark (`last_sync_at = generated_at` de la respuesta, no la hora
local) en una tabla `sync_state` de Portal Clientes.

### 4.2 Pseudocódigo del job (Python / httpx)

```python
def run_sync(full: bool = False):
    since = None if full else get_sync_state("snapshot")
    params = {"include_credentials": "true"}
    if since:
        params["since"] = since

    resp = httpx.get(
        f"{NAVI_BASE_URL}/api/v1/integration/snapshot",
        params=params,
        headers={"X-API-Key": NAVI_API_KEY},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    with db.transaction():
        seen = upsert_customers(data["customers"])      # upsert por source_id
        seen |= upsert_vehicles(data["vehicles"])       # upsert por plate
        if full:
            deactivate_missing(seen)                    # is_active = false
        set_sync_state("snapshot", data["generated_at"])
```

Reglas del upsert:

- `INSERT ... ON CONFLICT (source_id) DO UPDATE` (vehículos: `ON CONFLICT (plate)`).
- Guardar `password_enc` tal cual: ya es un token Fernet de la clave compartida.
  Un token que no descifre aborta el sync en vez de persistirse — guardarlo
  dejaría al ETL sin autenticar contra geotab y el síntoma aparecería un día
  después, sin nada que lo ligara al sync. Sin `password_enc`, se cifra el
  `password` en claro (camino de compatibilidad con snapshots anteriores).
- La transacción es por sync completo: o entra todo el snapshot o nada.
- Reintentos con backoff ante 5xx/timeout; si el sync falla, el watermark no avanza
  y el próximo intento repite el rango (los upserts son idempotentes).

### 4.3 Uso del pool de credenciales en Portal Clientes

Al consultar la API de Geotab (informes, ExceptionEvents de las reglas):

1. Tomar la credencial activa con `last_used_at` más antiguo **entre todas las
   databases con el mismo `database_key`** (ver §3.1) y marcarla usada.
2. Crear la sesión `mygeotab.API(username, password, database)` — cachear por
   `database:username` (mismo patrón que Navi Vehículos).
3. Ante error de autenticación, desactivar temporalmente esa credencial y reintentar
   con la siguiente del pool.
4. Consultar eventos por regla: `Get ExceptionEvent` con
   `search={"ruleSearch": {"id": rule_id}, "deviceSearch": {"id": geotab_device_id}, "fromDate": ..., "toDate": ...}`.

### 4.4 Errores del endpoint

| Código | Causa | Acción en Portal Clientes |
|---|---|---|
| `401` | API key inválida/ausente | Alertar — requiere intervención (rotación de clave). |
| `422` | Parámetro `since` mal formado | Bug del cliente — corregir formato ISO-8601. |
| `429` | Rate limit | Backoff exponencial, reintentar. |
| `503` | `INTEGRATION_API_KEYS` sin configurar en Navi Vehículos | Alertar — requiere intervención. |
| `5xx` | Error interno Navi Vehículos | Reintentar con backoff; watermark no avanza. |

---

## 5. Seguridad

- La API key vive solo en variables de entorno de ambos backends.
- `include_credentials=true` únicamente desde el job server-side; el frontend de
  Portal Clientes jamás ve passwords de Geotab.
- Passwords cifrados en reposo en ambas apps (Fernet con key propia por app).
- El endpoint de integración no expone usuarios, roles ni datos de auth de Navi
  Vehículos — solo el dominio cliente/vehículo/regla/credencial.
