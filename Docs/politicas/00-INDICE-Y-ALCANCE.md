# Políticas jurídicas de Portal Clientes — índice y alcance

> **Documentos en borrador para revisión del área jurídica.**
> No fueron redactados por un abogado y no constituyen asesoría legal. Cada uno
> propone una estructura y deja explícitos los hechos técnicos verificados en la
> aplicación, para que el área jurídica decida el texto vinculante, la base legal
> aplicable y las obligaciones exigibles.
>
> Donde un documento dice **[DECISIÓN JURÍDICA]** hay algo que no se puede
> resolver desde la ingeniería. Donde dice **[HALLAZGO TÉCNICO]** hay un hecho
> verificado en el código o en la base de datos que puede tener consecuencias
> legales.

Elaborado el **2026-08-27** sobre la rama `Navifault` del repositorio
Navi-Portal-Clientes y sobre la base de datos `portal_clientes` en producción.

---

## Qué es la aplicación, en términos jurídicos

**Portal Clientes** es una aplicación web B2B que Navitrans pone a disposición de
sus clientes de flota. No es un servicio al consumidor final: los usuarios son
personas designadas por empresas clientes para consultar información sobre sus
propios vehículos.

Lo que trata, verificado en la base al 2026-08-27:

| Categoría | Volumen | Naturaleza |
| --- | --- | --- |
| Usuarios del portal | 6 | Nombre, correo, hash de contraseña, último ingreso |
| Flotas (empresas cliente) | 19 activas | Datos de persona jurídica |
| Vehículos | 330 activos | Placa, dispositivo telemático, motor |
| Eventos de conducción con GPS | 81.535 en 30 días | **100 % con latitud y longitud** |
| Eventos de falla mecánica | 314.042 en 30 días | Diagnóstico técnico del vehículo |
| Registros diarios de combustible | 19 meses de historia | Consumo, distancia, velocidad |
| Órdenes de mantenimiento | 3.277 | Intervenciones sobre el vehículo |
| Adjuntos de usuarios | 0 | Funcionalidad construida, sin uso |

**El punto jurídicamente sensible no son los 6 usuarios del portal, sino los
81.535 eventos de conducción georreferenciados.** Un evento de "frenada brusca"
con coordenadas, fecha, hora y placa describe la conducta de la persona que iba
conduciendo. Si esa persona es identificable —y en una flota normalmente lo es,
cruzando la placa con la programación de turnos— **es dato personal y en la
práctica es monitoreo laboral**, aunque el portal nunca almacene el nombre del
conductor. Ese análisis está en el documento 05.

---

## Qué significa que la aplicación esté en desarrollo

**Portal Clientes no está en versión final.** Los volúmenes de este documento son
una fotografía del entorno de desarrollo al 2026-08-27; crecerán, y algunas
funcionalidades cambiarán antes del lanzamiento. Eso tiene tres consecuencias que
atraviesan todas las políticas.

### 1. Las cifras son de hoy; las políticas deben dimensionarse para el destino

Seis usuarios se atienden a mano. Seiscientos, no. Una política que hoy funciona
por su pequeña escala —atender una solicitud de supresión abriendo la base de
datos, por ejemplo— deja de funcionar sin que nadie lo note.

**[DECISIÓN JURÍDICA] Cada política debe redactarse para el volumen objetivo, no
para el actual.** Y donde la obligación exija una capacidad técnica que hoy no
existe, esa capacidad debe entrar al plan de producto antes del lanzamiento, no
después. La lista está en el documento 14.

### 2. Pero los datos ya son reales, y las obligaciones ya corren

Esta es la distinción que no se debe perder de vista. Verificado al 2026-08-27:

| Verificación | Resultado |
| --- | --- |
| Flotas con nombre de empresa real | **22 de 22** |
| Vehículos con placa colombiana real | **330 de 330** |
| Usuarios con correo real | 4 de 6 |
| Telemetría | Real, proveniente de la plataforma del proveedor |

**No hay datos sintéticos.** "En desarrollo" describe la madurez del software, no
la naturaleza de los datos. La Ley 1581 de 2012 no condiciona sus deberes a que
un producto haya sido lanzado: **si hay tratamiento de datos personales reales,
hay responsable, hay deberes y hay titulares con derechos**, aunque el producto
esté en construcción y aunque nadie externo lo esté usando todavía.

En la práctica, estar en desarrollo **no atenúa la obligación: agrava el riesgo**,
porque un entorno de desarrollo suele tener menos controles que uno productivo y
aquí está tratando información real.

### 3. El entorno de desarrollo es hoy el entorno que sirve

**[HALLAZGO TÉCNICO]** La auditoría interna registra en SEC-033 que la instancia
que presta servicio ejecuta directamente el árbol de trabajo del desarrollador,
con recarga automática al detectar cambios, y que el mismo anfitrión no dispone de
terminación TLS (SEC-025).

La combinación —datos personales reales, incluidos 81.535 eventos de conducción
georreferenciados al mes, sobre una instalación de grado desarrollo— es
precisamente el escenario que el deber de seguridad del art. 17 literal d busca
evitar.

**[DECISIÓN JURÍDICA] Es la decisión más urgente del conjunto**, y no puede
esperar a la publicación de las políticas. Hay tres caminos y son excluyentes:

1. Elevar el entorno actual a grado productivo: TLS, despliegue explícito, sin
   montaje del árbol de trabajo.
2. Separar el entorno de desarrollo del que sirve, y que el de desarrollo use
   **datos anonimizados o sintéticos**.
3. Documentar formalmente por qué el riesgo es aceptable durante un período
   acotado, con fecha de cierre y responsable.

Lo que no es defensible es que la situación continúe sin decisión.

### 4. La ventaja de decidir ahora

El único aspecto favorable de estar en desarrollo: **cambiar el diseño hoy es
barato**. Las capacidades que las políticas van a exigir —registro de
autorizaciones, exportación y supresión a solicitud, versionado de textos legales,
plazos de conservación aplicados automáticamente, aviso de contenido generado por
IA— cuestan una fracción si entran ahora, frente a construirlas sobre un producto
en operación con clientes.

Es lo que la doctrina llama **privacidad desde el diseño**, y este es exactamente
el momento en que se puede aplicar.

---

## Cómo leer el resto de los documentos

Cada política mezcla dos horizontes y conviene distinguirlos:

- **Lo que ya obliga**, porque hay datos personales reales bajo tratamiento.
- **Lo que debe estar listo antes del lanzamiento**, cuando el producto pase a
  operación con clientes accediendo directamente.

El documento 14 separa ambos y propone un orden.

---

## Los documentos

| # | Documento | Marco principal |
| --- | --- | --- |
| 01 | Política de tratamiento de datos personales | Ley 1581/2012, Decreto 1074/2015 |
| 02 | Aviso de privacidad | Decreto 1377/2013, art. 14 y ss. |
| 03 | Política de cookies y tecnologías de seguimiento | Ley 1581/2012, ePrivacy, RGPD |
| 04 | Política de uso de inteligencia artificial | CONPES 4144, Reglamento (UE) 2024/1689 |
| 05 | Política de telemetría y monitoreo de conductores | Ley 1581/2012, C.S.T., jurisprudencia constitucional |
| 06 | Política de seguridad de la información | Ley 1581/2012 art. 4(g), Ley 1273/2009 |
| 07 | Política de retención y supresión | Ley 1581/2012 art. 11, Ley 594/2000 |
| 08 | Política de transferencias internacionales | Ley 1581/2012 art. 26, Circulares SIC |
| 09 | Política de gestión de incidentes | Decreto 1074/2015, RGPD art. 33-34 |
| 10 | Política de derechos del titular y PQRS | Ley 1581/2012 arts. 8, 14, 15 |
| 11 | Política de encargados y terceros | Ley 1581/2012 art. 3(d), 25 |
| 12 | Términos y condiciones de uso | Ley 527/1999, Ley 1480/2011 |
| 13 | Política de accesibilidad | Ley 1618/2013, WCAG 2.2 |
| 14 | Requisitos previos al lanzamiento a producción | Transversal |

---

## Advertencias transversales para el área jurídica

**[HALLAZGO TÉCNICO] La auditoría interna registra bloqueadores abiertos que
tienen lectura jurídica.** Están documentados en la auditoría técnica interna
del proyecto con identificadores `SEC-xxx` (resumen en `ARQUITECTURA_COMPLETA.md`,
sección 7). Los de mayor relevancia legal:

- **SEC-001:** credenciales de acceso a la plataforma telemática quedaron
  versionadas en el repositorio del ETL. Si ese repositorio fue clonado, forkeado
  o publicado, **puede constituir un incidente de seguridad notificable**. El área
  jurídica debe evaluarlo antes de que prescriban plazos de notificación.
- **SEC-002:** el contexto de construcción de la imagen del ETL incorporaba
  archivos `.env`, cachés de sesión y ~1,13 GB de telemetría. Mismo análisis.
- **SEC-025:** el despliegue sugerido **no incluye terminación TLS**. Las cookies
  marcadas `Secure` y el encabezado HSTS no crean cifrado por sí solos. Si el
  portal se sirviera por HTTP en producción, las credenciales viajarían en claro.
- **SEC-014:** no hay separación de privilegios en la base de datos ni seguridad
  a nivel de fila. El aislamiento entre flotas depende exclusivamente de la
  lógica de aplicación.
- **SEC-018:** no existe un ensayo de restauración verificado. La disponibilidad
  es uno de los deberes del responsable (Ley 1581/2012, art. 4 literal g).

**[DECISIÓN JURÍDICA] Ninguna de estas políticas puede publicarse antes de
resolver quién es el responsable y quién el encargado** en cada flujo. Ver
documento 11: la respuesta cambia según si Navitrans decide las finalidades del
tratamiento de la telemetría o solo las ejecuta por cuenta del cliente de flota.

**[DECISIÓN JURÍDICA] Registro Nacional de Bases de Datos.** Si Navitrans califica
como responsable y supera los umbrales de la Circular Externa correspondiente,
las bases de datos deben inscribirse ante la SIC. No se verificó si esa
inscripción existe.
