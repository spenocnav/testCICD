# Política de Retención y Supresión de Datos

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1581 de 2012 arts. 4(c), 8(e) y 11; Ley 594 de 2000 (archivos);
> Código de Comercio art. 28 y 60. Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

El principio de temporalidad del art. 11 prohíbe conservar datos personales más
allá del tiempo necesario para la finalidad. Hoy **no existe ninguna política de
retención implementada**: salvo la purga de tokens de sesión, nada se borra.

## 1. Lo que hay hoy

**[HALLAZGO TÉCNICO]** Verificado al 2026-08-27:

| Dato | Antigüedad | Purga automática |
| --- | --- | --- |
| Tokens de refresco | 7 días de vigencia | **Sí**, proceso dedicado |
| Datos diarios de combustible | Desde **enero de 2025** (19 meses) | No |
| Eventos de falla | Desde marzo de 2026 (~1 millón) | No |
| Eventos de conducción con GPS | Desde marzo de 2026 | No |
| Puntos de ubicación | 1.287.900 filas, **sin uso desde julio de 2026** | No |
| Lecturas de pedal | Desde enero de 2026 | No |
| Órdenes de mantenimiento | 3.277 | No |
| Usuarios inactivos | — | No, se marcan inactivos y permanecen |

## 2. Plazos propuestos

**[DECISIÓN JURÍDICA] Todos los plazos son propuestas de ingeniería y deben
fijarse jurídicamente, contrastando el principio de temporalidad con las
obligaciones de conservación comercial y contractual.**

| Categoría | Propuesta | Fundamento |
| --- | --- | --- |
| Datos de usuario activo | Mientras dure la relación + [—] | Ejecución del contrato |
| Datos de usuario inactivo | Supresión o anonimización a los [—] meses | Temporalidad |
| Eventos de conducción **con coordenadas** | **12 meses**, luego anonimizar la ubicación conservando el evento | El dato geográfico individual es el más intrusivo y el que menos valor analítico conserva con el tiempo |
| Agregados diarios de combustible | **60 meses** | Valor analítico legítimo, baja intrusividad, sin geolocalización |
| Eventos de falla mecánica | **36 meses** | Historial técnico del vehículo, no del conductor |
| Órdenes de mantenimiento | Según obligación contractual y comercial | Código de Comercio |
| Registros de auditoría de acceso | **[—]** | Necesarios para investigar incidentes |
| Adjuntos | Vinculados al caso que los origina | — |

El criterio que atraviesa la tabla: **cuanto más identifica a una persona, más
corto el plazo; cuanto más describe al vehículo, más largo puede ser.**

## 3. Los datos huérfanos

**[HALLAZGO TÉCNICO]** Tres conjuntos dejaron de recibir datos el 22–23 de julio
de 2026 y **ningún componente los consulta**: puntos de ubicación (1.287.900
filas, 337 MB), lecturas de altimetría (139.778 filas) y consumo de DEF (748
filas). Contienen rastro geográfico histórico de 14 vehículos.

**[DECISIÓN JURÍDICA]** Datos personales que ya no sirven a ninguna finalidad
deben suprimirse: es exactamente el supuesto del art. 11. Conservarlos sin
finalidad es la posición más difícil de defender de todo el inventario. Decidir
entre supresión, anonimización o reactivación documentada de la finalidad.

## 4. Supresión a solicitud del titular

**[HALLAZGO TÉCNICO] No implementada.** No existe función de supresión ni de
exportación. Toda solicitud se atiende manualmente contra la base de datos.

**[DECISIÓN JURÍDICA]** Definir:

- Qué se suprime y qué se anonimiza. Un evento de falla mecánica sin
  identificación del conductor conserva valor técnico legítimo para el
  propietario del vehículo.
- Qué ocurre con la información que pertenece al **cliente de flota** y no al
  titular individual. Un conductor no puede exigir la supresión del historial de
  mantenimiento de un vehículo que no es suyo.
- El plazo interno de atención, dentro de los límites legales.

## 5. Anonimización

**[DECISIÓN JURÍDICA]** Si se adopta la anonimización como alternativa a la
supresión, debe quedar definido qué la constituye. Quitar la placa **no basta**
si quedan coordenadas, fecha y hora: la reidentificación sigue siendo trivial. Un
criterio defendible exige eliminar o degradar la precisión geográfica y temporal,
no solo el identificador.


## 6. El histórico acumulado durante el desarrollo

**[DECISIÓN JURÍDICA]** La aplicación lleva meses acumulando datos reales sin que
exista política de conservación. Al 2026-08-27: 19 meses de registros de
combustible, 6 meses de eventos de falla y de conducción georreferenciada, y
1.287.900 puntos de ubicación que ya no tienen finalidad.

Cuando se fijen los plazos del punto 2, habrá que decidir qué se hace con lo ya
recolectado. Tres opciones, y la elección no es neutra:

1. **Aplicar los plazos retroactivamente** y suprimir lo que los exceda. Es lo más
   consistente con el principio de temporalidad.
2. **Aplicarlos hacia adelante**, conservando el histórico. Requiere justificar por
   qué ese histórico sigue siendo necesario.
3. **Anonimizar el histórico** y aplicar plazos solo a lo nuevo. Suele ser el punto
   medio defendible, siempre que la anonimización sea real (ver §5).

**Cada mes de demora aumenta el volumen afectado**, en el orden de 80.000 eventos
georreferenciados y 200.000 eventos de falla mensuales. Decidir temprano es más
barato en todos los sentidos.
