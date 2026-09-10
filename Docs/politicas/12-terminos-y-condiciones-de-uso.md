# Términos y Condiciones de Uso

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 527 de 1999 (comercio electrónico); Ley 1480 de 2011 (Estatuto del
> Consumidor); Ley 1273 de 2009; Código Civil y Código de Comercio.
> Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

**[HALLAZGO TÉCNICO] El portal no tiene hoy términos y condiciones.** No hay
aceptación al ingreso ni texto publicado.

## 1. Naturaleza de la relación

**[DECISIÓN JURÍDICA]** Portal Clientes es una herramienta B2B: los usuarios
acceden en representación de una empresa cliente, no como consumidores finales.
Eso incide en la aplicabilidad del Estatuto del Consumidor, que protege al
consumidor persona natural que adquiere para satisfacer necesidades propias.

Conviene decidir expresamente si el portal se ofrece como servicio accesorio del
contrato principal de flota o como servicio autónomo. La respuesta cambia el
régimen de responsabilidad.

## 2. Contenido propuesto

**[DECISIÓN JURÍDICA] Estructura sugerida; el texto vinculante lo redacta el área
jurídica.**

### 2.1 Objeto y alcance
Qué ofrece el portal: consulta de información de operación, mantenimiento y
fallas de los vehículos de la flota del usuario.

### 2.2 Acceso y credenciales
- Las cuentas son **personales e intransferibles**.
- El usuario responde por las operaciones realizadas con sus credenciales.
- Debe notificar de inmediato cualquier uso no autorizado.
- **[HALLAZGO TÉCNICO]** Advertir que la sesión permanece activa hasta 7 días si
  no se cierra explícitamente, por el token de refresco. Es relevante en equipos
  compartidos, sobre todo mientras SEC-016 no esté resuelto.

### 2.3 Uso permitido y prohibido
Prohibir expresamente: intentar acceder a información de flotas ajenas; extraer
masivamente información por medios automatizados; interferir con la operación del
servicio; y realizar ingeniería inversa.

**[DECISIÓN JURÍDICA]** Advertir que estas conductas pueden constituir delito
bajo la Ley 1273 de 2009 —acceso abusivo a sistema informático, entre otros— sin
perjuicio de las acciones contractuales.

### 2.4 Naturaleza de la información
**[DECISIÓN JURÍDICA] La cláusula más importante del documento.**

La información proviene de plataformas telemáticas de terceros y de la
documentación técnica del fabricante. Debe quedar claro que:

- **Es informativa y no sustituye el diagnóstico de un técnico calificado.**
- Las explicaciones de fallas son **generadas por un sistema de inteligencia
  artificial** a partir de documentación oficial (documento 04).
- La decisión de intervenir un vehículo corresponde al cliente.
- Puede haber datos faltantes o inconsistentes por fallas de los dispositivos o
  de las plataformas de origen.

**[HALLAZGO TÉCNICO]** Esto último no es hipotético y conviene que la cláusula lo
refleje con honestidad: al 2026-08-27, el **11,4 %** de los registros diarios de
combustible de los últimos 90 días no tiene distancia efectiva calculable y queda
excluido de los cálculos, y **41 de 330 vehículos activos (12 %)** no aparecen en
el modelo analítico.

### 2.5 Disponibilidad
**[DECISIÓN JURÍDICA]** Definir si se compromete un nivel de servicio. Si no,
decirlo. Prometer disponibilidad sin medirla crea obligación exigible.

### 2.6 Propiedad intelectual
El portal, su código y sus interfaces son de Navitrans. **[DECISIÓN JURÍDICA]**
Definir la titularidad de los datos de operación de los vehículos, que es una
cuestión contractual con el cliente de flota, no de propiedad intelectual.

Debe reconocerse además la atribución de los datos cartográficos conforme a la
licencia ODbL de OpenStreetMap, **si el mapa se mantiene con esa fuente**.

### 2.7 Datos personales
Remisión a las políticas 01, 02 y 05.

### 2.8 Modificaciones
**[DECISIÓN JURÍDICA]** Definir cómo se comunican los cambios y desde cuándo
rigen. **[HALLAZGO TÉCNICO]** La aplicación no tiene hoy versionado de textos
legales ni registro de aceptación, así que no puede acreditar qué versión aceptó
cada usuario.

### 2.9 Ley aplicable y jurisdicción
**[DECISIÓN JURÍDICA]** Legislación colombiana y jurisdicción competente. Evaluar
si procede cláusula compromisoria.

## 3. Aceptación

**[DECISIÓN JURÍDICA]** Debe poder acreditarse quién aceptó, qué versión y
cuándo. La Ley 527 de 1999 da validez al mensaje de datos, pero la carga de la
prueba sigue siendo del que alega. Requiere desarrollo: hoy no existe.
