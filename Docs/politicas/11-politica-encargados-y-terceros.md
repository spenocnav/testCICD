# Política de Encargados del Tratamiento y Terceros

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1581 de 2012 arts. 3(c) y (d), 18, 25; Decreto 1074 de 2015.
> Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

## 1. La pregunta que condiciona todo lo demás

**[DECISIÓN JURÍDICA] ¿Navitrans es responsable o encargado del tratamiento de la
telemetría de los vehículos de sus clientes?**

Esta calificación no es formal: determina quién debe obtener las autorizaciones,
quién responde ante los titulares, quién notifica los incidentes y quién atiende
las solicitudes de derechos. **Ninguna de las otras políticas puede cerrarse antes
de resolverla.**

Elementos que apuntan a **encargado**:

- Los vehículos son del cliente de flota.
- Los conductores son empleados del cliente, no de Navitrans.
- El portal presenta al cliente información sobre su propia operación.

Elementos que apuntan a **responsable**:

- Navitrans decide qué datos se extraen, cómo se transforman y qué métricas se
  derivan.
- Navitrans define las finalidades del módulo de comunicaciones y su lógica.
- Navitrans elige los proveedores telemáticos y las condiciones de tratamiento.
- Navitrans decide los plazos de conservación.

**Puede haber corresponsabilidad**, o distintas calificaciones por flujo: encargado
respecto de la telemetría operativa, responsable respecto de los usuarios del
portal y de las comunicaciones generadas.

## 2. Terceros identificados

**[HALLAZGO TÉCNICO] Verificado en el código. Ubicación y régimen contractual NO
verificados.**

| Tercero | Función | Datos involucrados | Rol probable |
| --- | --- | --- | --- |
| **MyGeotab** | Plataforma telemática de origen | Eventos de conducción con GPS, fallas, contadores | Encargado o responsable independiente |
| **CloudFleet** | Gestión de mantenimiento | Órdenes, programaciones, novedades | Encargado |
| **Navi Vehículos** | Catálogo maestro | Vehículos, clientes, motores | Interno o encargado |
| **OpenStreetMap** | Imágenes de mapa | **IP del usuario y zona consultada** | **Sin relación contractual** |
| **Sentry** | Reporte de errores | Errores técnicos; configurado sin datos personales | Encargado |
| **Clientes de flota** | Destinatarios de la información | Todo lo de su flota | Responsable o corresponsable |

## 3. Contratos de transmisión

El art. 25 exige contrato entre responsable y encargado con el alcance,
actividades y obligaciones del encargado.

**[DECISIÓN JURÍDICA]** Verificar si existen y si contienen las cláusulas
exigidas. Mínimo: finalidad determinada, prohibición de uso propio, medidas de
seguridad, subencargados con autorización previa, asistencia ante solicitudes de
titulares, notificación de incidentes en plazo compatible con el propio (ver
documento 09), devolución o supresión al terminar, y auditoría.

## 4. El contrato con el cliente de flota

**[DECISIÓN JURÍDICA] Es el más importante y el que más expone a Navitrans.**

Debe resolver, como mínimo:

1. **Quién obtiene la autorización de los conductores.** Corresponde al cliente
   como empleador, pero debe quedar escrito.
2. **Declaración del cliente** de que informó a sus conductores sobre el
   monitoreo, con indemnidad a favor de Navitrans si no lo hizo.
3. **Quién atiende las solicitudes de derechos de los conductores**, y con qué
   asistencia de la otra parte (documento 10).
4. **Qué puede hacer el cliente con la información.** En particular, si puede
   usarla para procesos disciplinarios y bajo qué condiciones (documento 05).
5. **Plazos de conservación** y qué ocurre al terminar el contrato.
6. **Notificación de incidentes** en ambos sentidos.

## 5. OpenStreetMap: un tercero sin contrato

**[HALLAZGO TÉCNICO]** Recibe la IP del usuario y el área geográfica consultada
en cada carga del mapa, **sin que exista contrato, encargo ni posibilidad de
auditoría**. No es un proveedor contratado: es un servicio público al que la
aplicación dirige el navegador del usuario.

**[DECISIÓN JURÍDICA]** No encaja en la figura de encargado ni en la de
transferencia ordinaria. Servir las imágenes desde infraestructura propia elimina
la figura por completo, y probablemente sea más simple que construir un
fundamento jurídico para ella.
