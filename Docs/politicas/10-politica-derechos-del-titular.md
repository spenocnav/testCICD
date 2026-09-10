# Política de Atención de Consultas, Reclamos y Derechos del Titular

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1581 de 2012 arts. 8, 14, 15 y 16; Decreto 1074 de 2015.
> Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

## 1. Derechos

Conocer, actualizar y rectificar sus datos; solicitar prueba de la autorización;
ser informado sobre el uso dado; presentar quejas ante la SIC por infracciones;
revocar la autorización y solicitar la supresión cuando proceda; y acceder
gratuitamente a sus datos.

## 2. Plazos legales

| Solicitud | Plazo | Prórroga |
| --- | --- | --- |
| **Consulta** (art. 14) | 10 días hábiles | 5 días hábiles más, informando el motivo y la fecha |
| **Reclamo** (art. 15) | 15 días hábiles | 8 días hábiles más, informando el motivo y la fecha |
| Reclamo incompleto | Requerir al titular dentro de 5 días | Se entiende desistido a los 2 meses sin respuesta |

**[DECISIÓN JURÍDICA]** Fijar plazos internos más cortos que los legales, para
absorber demoras. Los legales son máximos, no objetivos.

## 3. Canal

**[DECISIÓN JURÍDICA]** Definir correo, formulario o dirección física, y
designar al responsable de atención (art. 23). Debe publicarse en el aviso de
privacidad y en la política.

**[HALLAZGO TÉCNICO]** El portal no ofrece hoy ningún canal para ejercer estos
derechos: no hay formulario, ni enlace, ni sección de privacidad.

## 4. Quién puede solicitar

**[DECISIÓN JURÍDICA] Aquí hay una dificultad práctica seria.**

Los titulares no son solo los 6 usuarios del portal. **También lo son los
conductores** de los 330 vehículos, cuyos eventos de conducción se registran con
geolocalización (documento 05). Esos conductores:

- no tienen cuenta en el portal;
- no han recibido aviso de privacidad;
- probablemente no saben que estos datos existen.

Si uno de ellos presenta una solicitud, hay que poder atenderla. Eso exige
resolver:

1. **Cómo se acredita la legitimación.** El titular debe demostrar que condujo un
   vehículo determinado en un período determinado. Esa información la tiene el
   cliente de flota, no Navitrans.
2. **A quién se dirige.** Si Navitrans es encargado, la solicitud corresponde al
   cliente de flota como responsable, y Navitrans debe asistirlo. Si es
   responsable, debe atenderla directamente. **La calificación sigue sin resolver
   (documento 11).**
3. **Qué se entrega.** Todos los eventos asociados a las placas que esa persona
   condujo en ese período.

## 5. Capacidad técnica

**[HALLAZGO TÉCNICO] La aplicación no tiene funciones de acceso, exportación,
rectificación ni supresión a solicitud del titular.** Toda solicitud se atiende
manualmente contra la base de datos, por alguien con acceso privilegiado.

**[DECISIÓN JURÍDICA]** Con 6 usuarios es manejable. Con conductores como
titulares reconocidos, no lo es. Decidir si se construyen esas funciones o si el
proceso manual se documenta formalmente, con control de quién lo ejecuta y
registro de cada solicitud atendida.

## 6. Registro

**[DECISIÓN JURÍDICA]** Llevar registro de cada solicitud —fecha de recepción,
titular, tipo, respuesta y fecha— es la única forma de acreditar cumplimiento
ante la SIC. No existe hoy.

## 7. Reclamo previo

El art. 16 exige agotar el trámite ante el responsable antes de acudir a la SIC.
Debe informarse al titular.
