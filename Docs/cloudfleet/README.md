# Integración con CloudFleet — material para el proveedor

## `oportunidades-mejora-api.html`

Levantamiento de puntos donde la API de CloudFleet no expone algo que su propia
interfaz sí sabe, o donde el nombre de un campo no coincide con su contenido.
Pensado para enviarlo al equipo de soporte de CloudFleet.

Veinte hallazgos con identificador estable **CF-01 … CF-20**, agrupados en
webhooks, enlace novedad↔trabajo, semántica de estados, listados y paginación,
contenido de los datos y documentación. Cada uno lleva evidencia reproducible
con números reales y una línea de qué se pide.

**Este archivo es la fuente.** Está publicado además como artefacto privado; al
modificarlo hay que volver a publicarlo desde esta misma ruta para que el enlace
siga apuntando a la versión vigente. No editar la copia publicada por separado.

### Cómo se levantó

Lecturas sobre la API en producción con la clave del portal, más una prueba
controlada de extremo a extremo el 2026-09-05 —novedad #706 sobre la orden 5733,
vehículo JTX904— hecha durante el diseño del escalamiento de fallas de Navifault
a novedades de CloudFleet. Ninguna escritura fuera de esa prueba.

### Estado

Sin enviar. **Completo**: la prueba de extremo a extremo del 2026-09-05 se ejecutó
en sus seis pasos —escalar, asignar a la orden, agregar repuesto, borrar el
trabajo, anular la orden y borrar la novedad— y de los dos últimos salieron CF-19
(`doneAt` es la fecha de la orden) y CF-20 (anular borra los trabajos y
repuestos).

### Contenido sensible

Lleva placa, número de novedad, número de orden y cifras agregadas de la
instancia de CloudFleet de Navitrans. Es información pertinente para que el
proveedor reproduzca los casos y proviene de sus propios sistemas, pero conviene
revisarla si el destinatario llega a ser más amplio que su equipo de soporte.
