# Política de Seguridad de la Información

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1581 de 2012 art. 4(g) y 17(d); Ley 1273 de 2009; ISO/IEC 27001 como
> marco de referencia. Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

El deber de seguridad no es una recomendación: el art. 17 literal d de la Ley
1581/2012 obliga al responsable a conservar la información bajo las condiciones
necesarias para impedir adulteración, pérdida, consulta o uso no autorizado.

## 1. Controles verificados como implementados

| Control | Estado |
| --- | --- |
| Contraseñas con hash bcrypt, nunca en claro | Implementado |
| Sesiones en cookies `HttpOnly`, inaccesibles a scripts | Implementado |
| Token de refresco almacenado con hash y rotación de un solo uso | Implementado |
| Recarga de permisos en cada petición, sin confiar en credenciales antiguas | Implementado |
| Aislamiento entre flotas en el backend, con pruebas negativas | Implementado |
| Límite de intentos de autenticación | Implementado |
| Contenedores de aplicación sin privilegios de administrador | Implementado |
| Respuestas de la API sin almacenamiento en caché | Implementado |
| Servicio de errores configurado para no enviar datos personales | Implementado |

## 2. Deficiencias abiertas con relevancia jurídica

**[HALLAZGO TÉCNICO] Provienen de la auditoría técnica interna del proyecto
(resumen en `ARQUITECTURA_COMPLETA.md`, sección 7). El área jurídica debe conocerlas porque afectan
directamente el cumplimiento del deber de seguridad.**

| Ref. | Deficiencia | Lectura jurídica |
| --- | --- | --- |
| SEC-001 | Credenciales de acceso a la plataforma telemática quedaron versionadas en el repositorio del ETL | **Posible incidente notificable.** Ver documento 09 |
| SEC-002 | La imagen del ETL incorporaba `.env`, cachés de sesión y ~1,13 GB de telemetría | Igual que el anterior |
| SEC-025 | El despliegue sugerido no incluye terminación TLS | Sin cifrado en tránsito garantizado, el deber de seguridad no se cumple |
| SEC-014 | Un único usuario privilegiado de base de datos; sin seguridad a nivel de fila | El aislamiento entre clientes depende solo de la aplicación |
| SEC-018 | Sin ensayo de restauración verificado | La disponibilidad es parte del deber de seguridad |
| SEC-003 | Componentes de interfaz con vulnerabilidades críticas conocidas sin actualizar | Diligencia debida |
| SEC-008 | Archivos adjuntos sin validación de contenido real ni antivirus | Vector de introducción de código malicioso (Ley 1273/2009) |
| SEC-023 | Mensajes de error internos expuestos al usuario | Filtración de información técnica |
| SEC-021 | Sin token anti-falsificación de peticiones | Riesgo de acción no autorizada en nombre del usuario |
| SEC-016 | El cierre de sesión puede informar éxito sin haber revocado | **El usuario cree que cerró sesión y no la cerró** |

**[DECISIÓN JURÍDICA]** SEC-016 merece atención específica: informar al titular
que su sesión terminó cuando no terminó es una afirmación incorrecta sobre la
seguridad de sus datos, con consecuencias si ocurre en un equipo compartido.

## 2 bis. El entorno de desarrollo trata datos reales

**[HALLAZGO TÉCNICO]** No hay separación entre el entorno donde se desarrolla y
el que sirve la información. Según SEC-033, la instancia en servicio ejecuta
directamente el árbol de trabajo del desarrollador y recarga al detectar cambios;
según SEC-025, el anfitrión no dispone de terminación TLS.

Sobre esa instalación viven datos reales: 22 flotas con nombre de empresa real,
330 vehículos con placa real y 81.535 eventos de conducción georreferenciados al
mes.

**[DECISIÓN JURÍDICA]** El art. 17 literal d no distingue entre entornos: obliga a
conservar la información bajo las condiciones necesarias para impedir su
adulteración, pérdida o uso no autorizado. Un entorno de desarrollo que trata
datos personales reales queda sujeto al mismo deber que uno productivo, y
normalmente tiene menos controles.

Tres salidas, excluyentes entre sí:

1. Elevar el entorno a grado productivo.
2. Separar los entornos y **usar datos anonimizados o sintéticos en desarrollo**.
   Es la solución de fondo y la única que elimina la exposición.
3. Aceptar formalmente el riesgo por un período acotado, con fecha de cierre y
   responsable designado.

La opción 2 tiene además una ventaja práctica: hoy cualquier persona con acceso al
entorno de desarrollo tiene acceso material a la telemetría de clientes reales.

## 3. Compromisos propuestos

**[DECISIÓN JURÍDICA] Cada compromiso que se asuma en una política publicada es
exigible. No comprometer lo que no está implementado.**

1. **Cifrado en tránsito.** Todo acceso por HTTPS. *Requiere resolver SEC-025
   antes de comprometerlo.*
2. **Control de acceso por rol y flota,** con verificación en el servidor en cada
   operación. *Implementado.*
3. **Contraseñas nunca almacenadas ni transmitidas en claro.** *Implementado.*
4. **Mínimo privilegio** en el acceso a datos. *No implementado — SEC-014.*
5. **Respaldos periódicos con restauración probada.** *Parcial — SEC-018.*
6. **Registro de auditoría** de accesos y operaciones sensibles. *Parcial.*
7. **Gestión de vulnerabilidades** con revisión periódica de dependencias.
   *Requiere formalización — SEC-003, SEC-026.*
8. **Confidencialidad del personal** con acceso a la información.

## 4. Ley 1273 de 2009

La Ley 1273 tipifica el acceso abusivo a sistema informático, la interceptación
de datos, el daño informático y la violación de datos personales, entre otros.

**[DECISIÓN JURÍDICA]** Conviene que el documento 12 (términos de uso) prohíba
expresamente al usuario intentar acceder a información de flotas ajenas, y
advierta que tales conductas pueden constituir delito. Hoy los términos no
existen.

## 5. Revisión

**[DECISIÓN JURÍDICA]** Fijar periodicidad de revisión de esta política y del
inventario de deficiencias, y designar responsable. Sin dueño y sin fecha, un
registro de riesgos se vuelve un documento histórico.
