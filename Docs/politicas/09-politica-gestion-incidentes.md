# Política de Gestión de Incidentes de Seguridad

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1581 de 2012 art. 17(n); Decreto 1074 de 2015; RGPD arts. 33 y 34 como
> referencia de estándar. Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

El art. 17 literal n obliga al responsable a **informar a la autoridad de
protección de datos cuando se presenten violaciones a los códigos de seguridad y
existan riesgos en la administración de la información** de los titulares.

## 1. Estado actual

**[HALLAZGO TÉCNICO] No existe procedimiento formal de gestión de incidentes**:
ni criterios de clasificación, ni plazos internos, ni responsable designado, ni
registro. No se identificó bitácora de incidentes.

## 2. Dos situaciones que requieren evaluación inmediata

**[DECISIÓN JURÍDICA] Esto no puede esperar a que la política se apruebe.**

### 2.1 Credenciales versionadas (SEC-001)

La auditoría interna confirmó que credenciales de acceso a la plataforma
telemática quedaron escritas en ocho archivos del repositorio del ETL, con 23
ocurrencias rastreadas. Esas credenciales dan acceso a telemetría multiflota.

Preguntas que determinan si hay incidente notificable:

- ¿El repositorio fue alguna vez público, clonado, forkeado o publicado en un
  registro de imágenes?
- ¿Las credenciales fueron rotadas? ¿Cuándo?
- ¿Hay registros de acceso del proveedor que permitan descartar uso no autorizado?

**Si no puede descartarse el acceso de un tercero, hay que evaluar la
notificación a la SIC y a los clientes de flota afectados.** El plazo corre desde
el conocimiento del hecho, y la auditoría lo documentó el 2026-07-23.

### 2.2 Contexto de imagen contaminado (SEC-002)

El contexto de construcción de la imagen del ETL incluía archivos de
configuración con secretos, una caché con 19 sesiones y aproximadamente 1,13 GB
de telemetría. Si esa imagen se publicó en un registro accesible, el análisis es
el mismo.

## 3. Procedimiento propuesto

**[DECISIÓN JURÍDICA] Validar plazos y umbrales.**

1. **Detección y registro.** Todo incidente sospechado se registra con fecha y
   hora de conocimiento — **este dato determina los plazos legales**.
2. **Contención.** Rotar credenciales, revocar sesiones, aislar el componente.
3. **Evaluación.** ¿Hubo datos personales involucrados? ¿De cuántos titulares?
   ¿Qué categorías? ¿Hay riesgo de daño?
4. **Clasificación.**
   - *Bajo:* sin datos personales o sin posibilidad real de acceso.
   - *Medio:* datos personales involucrados, acceso improbable.
   - *Alto:* acceso probable o confirmado, o datos de geolocalización.
5. **Notificación.** Los de nivel alto se notifican a la SIC. **[DECISIÓN
   JURÍDICA]** fijar plazo interno; el estándar internacional es 72 horas.
6. **Comunicación a titulares y clientes de flota** cuando haya riesgo alto.
7. **Cierre** con causa raíz y medidas correctivas.
8. **Registro permanente** de todos los incidentes, incluidos los no notificados,
   con la justificación de por qué no se notificaron.

## 4. Roles

**[DECISIÓN JURÍDICA]** Designar quién declara un incidente, quién decide
notificar y quién comunica a clientes. Sin nombres, el procedimiento no opera.

## 5. Relación con proveedores

**[DECISIÓN JURÍDICA]** Los contratos con encargados deben obligarlos a notificar
incidentes a Navitrans en un plazo que permita cumplir el propio. Un encargado que
notifica a los 30 días hace imposible cualquier plazo de 72 horas.
