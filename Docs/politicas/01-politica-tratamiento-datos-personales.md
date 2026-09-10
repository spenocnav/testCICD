# Política de Tratamiento de Datos Personales

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1581 de 2012, Decreto 1074 de 2015 (Título 2, Cap. 25, que compiló el
> Decreto 1377 de 2013). Fecha de elaboración: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

## 1. Identificación del responsable

**[DECISIÓN JURÍDICA]** Completar razón social, NIT, domicilio, correo de
notificaciones y teléfono. Debe designarse el área o persona responsable de
atender consultas y reclamos (Ley 1581/2012, art. 23).

## 2. Ámbito

Esta política cubre el tratamiento de datos personales que se realiza a través de
Portal Clientes: la aplicación web, su API, sus procesos automáticos de
sincronización y su cadena analítica.

## 3. Categorías de datos tratados

Verificado sobre la base de producción al 2026-08-27.

### 3.1 Datos de usuarios del portal

| Dato | Fuente | Observación |
| --- | --- | --- |
| Nombre completo | Alta administrativa | — |
| Correo electrónico | Alta administrativa | Identificador de acceso |
| Contraseña | Fijada por el usuario | Almacenada como hash bcrypt, nunca en claro |
| Último ingreso | Automático | — |
| Rol y flotas asignadas | Alta administrativa | Determina qué información ve |

Población actual: **6 usuarios**.

### 3.2 Datos de operación de vehículos

Volumen en 30 días: 314.042 eventos de falla, 81.535 eventos de conducción,
6.829 registros diarios de combustible, sobre 330 vehículos de 19 flotas.

**[HALLAZGO TÉCNICO] El 100 % de los eventos de conducción tiene coordenadas
geográficas.** Cada registro de exceso de velocidad, frenada brusca, giro brusco
o aceleración brusca incluye latitud, longitud, fecha y hora exactas, asociadas a
una placa.

**[DECISIÓN JURÍDICA] Calificación de estos datos.** La posición sostenida por la
Superintendencia de Industria y Comercio y por la doctrina comparada es que el
dato de geolocalización asociado a un vehículo **es dato personal cuando permite
identificar, directa o indirectamente, a la persona que lo conduce**. En una
flota, el cruce entre placa, fecha y programación de turnos suele permitirlo,
aunque el portal no almacene el nombre del conductor. Si el área jurídica acoge
esa calificación, todo el régimen de la Ley 1581/2012 aplica a esta información y
no solo a los datos de los 6 usuarios. El análisis específico está en el
documento 05.

### 3.3 Datos que NO se tratan

No se registran datos sensibles en el sentido del art. 5 de la Ley 1581/2012: ni
salud, ni biometría, ni origen racial, ni afiliación sindical, política o
religiosa, ni orientación sexual. No hay datos de menores de edad. No se hace
perfilamiento crediticio ni se reporta a centrales de riesgo, por lo que la Ley
1266 de 2008 no resulta aplicable.

## 4. Finalidades

**[DECISIÓN JURÍDICA] La lista debe cerrarse antes de publicar.** El principio de
finalidad (art. 4, literal b) exige que sean determinadas, explícitas y legítimas,
y prohíbe usos posteriores incompatibles. Las finalidades que la aplicación
efectivamente persigue hoy:

1. Autenticar usuarios y controlar su acceso según rol y flota asignada.
2. Presentar al cliente información de operación de sus propios vehículos:
   consumo, distancia, hábitos de conducción, fallas mecánicas y mantenimiento.
3. Generar comunicaciones sobre fallas detectadas, **con apoyo de un modelo de
   inteligencia artificial** (documento 04).
4. Ejecutar procesos de sincronización con las plataformas de las que proviene la
   información.
5. Registrar trazas técnicas de operación y auditoría de sincronizaciones.

**[DECISIÓN JURÍDICA]** Definir si se usará la información para fines analíticos
agregados, comparaciones entre flotas o desarrollo de producto. Hoy la aplicación
no lo hace, pero es una extensión natural y **no estaría amparada por las
finalidades anteriores** sin autorización específica.

## 5. Autorización

**[DECISIÓN JURÍDICA] Este es el punto más débil del esquema actual.** No se
identificó en el código ningún mecanismo de captura, registro ni conservación de
la autorización del titular. Los usuarios se crean administrativamente y quedan
activos.

El art. 9 exige autorización previa, expresa e informada, y el art. 12 impone al
responsable la carga de la prueba. Hay que resolver:

- **Usuarios del portal:** puede bastar la aceptación al primer ingreso, con
  registro de fecha, hora, versión del texto aceptado y evidencia. Requiere
  desarrollo: hoy no existe.
- **Conductores:** su autorización no la puede otorgar el portal. Corresponde al
  cliente de flota, en su condición de empleador, y debe quedar acreditada en el
  contrato. Ver documentos 05 y 11.

### 5.1 El histórico anterior a la autorización

**[DECISIÓN JURÍDICA]** Cuando se implemente el mecanismo de autorización, los
datos ya recolectados no quedan amparados de forma automática. Hay que decidir si
se solicita autorización con efecto sobre lo ya tratado, si se anonimiza el
histórico o si se suprime. El documento 07 §6 desarrolla las opciones.

Es una decisión que conviene tomar **antes** del lanzamiento: después habrá más
volumen y más titulares involucrados.

## 6. Derechos del titular

Conocer, actualizar, rectificar, solicitar prueba de la autorización, ser
informado sobre el uso dado, presentar quejas ante la SIC, revocar la
autorización y solicitar supresión, y acceder gratuitamente a sus datos. El
procedimiento está en el documento 10.

**[HALLAZGO TÉCNICO]** La aplicación **no ofrece hoy** exportación de datos del
titular, supresión a solicitud, ni histórico de autorizaciones. Cualquier
solicitud debe atenderse manualmente contra la base de datos.

## 7. Deberes del responsable

El art. 17 impone, entre otros, garantizar seguridad, confidencialidad y
tramitación de consultas. Estado verificado:

| Deber | Estado |
| --- | --- |
| Contraseñas protegidas | **Cumple.** bcrypt, nunca en claro |
| Sesiones protegidas | **Parcial.** Cookies HttpOnly y `SameSite=Lax`; `Secure` depende de un proxy TLS que el despliegue sugerido no incluye (SEC-025) |
| Control de acceso por flota | **Cumple en aplicación.** Sin refuerzo en base de datos (SEC-014) |
| Cifrado en tránsito | **[HALLAZGO TÉCNICO] No garantizado** por la configuración de despliegue |
| Copias de respaldo | **Parcial.** Existen; sin ensayo de restauración verificado (SEC-018) |
| Registro de incidentes | **No existe** procedimiento formal (documento 09) |
| Prueba de autorización | **No existe** |

## 8. Vigencia

**[DECISIÓN JURÍDICA]** Fijar la vigencia de la política y la de las bases de
datos. El art. 11 prohíbe conservar los datos más allá de lo necesario para la
finalidad. La propuesta de plazos está en el documento 07.
