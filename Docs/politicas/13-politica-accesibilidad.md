# Política de Accesibilidad Digital

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1618 de 2013; Ley 1346 de 2009 (Convención sobre los Derechos de las
> Personas con Discapacidad); Resolución 1519 de 2020 del MinTIC; WCAG 2.2.
> Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

## 1. Aplicabilidad

**[DECISIÓN JURÍDICA]** La Resolución 1519 de 2020 obliga en materia de
accesibilidad web a **sujetos obligados de la Ley de Transparencia**, típicamente
entidades públicas. Portal Clientes es una aplicación privada B2B, por lo que
probablemente no está cubierta por ese régimen.

No obstante, la Ley 1618 de 2013 establece deberes generales de accesibilidad y
prohíbe la discriminación por razón de discapacidad. Y si alguna flota cliente es
entidad pública o contratista del Estado, **puede trasladar contractualmente la
exigencia**. Conviene verificar la composición de la cartera de clientes antes de
descartar la obligación.

## 2. Estándar propuesto

**[DECISIÓN JURÍDICA]** Adoptar **WCAG 2.2 nivel AA** como objetivo. Es el
estándar que las normas colombianas referencian y el de mayor aceptación
internacional.

## 3. Estado actual

**[HALLAZGO TÉCNICO] No se realizó auditoría de accesibilidad y no debe asumirse
cumplimiento.** Lo que se observa desde el código:

| Aspecto | Observación |
| --- | --- |
| Semántica HTML | La interfaz usa componentes React sobre HTML estándar. **No auditado** |
| Contraste de color | **No auditado** |
| Navegación por teclado | **No auditada** |
| Lectores de pantalla | **No probados** |
| Gráficos y mapas | **Riesgo alto.** Los datos se presentan en gran medida como gráficos y mapas, que sin alternativa textual son inaccesibles |
| Tablas de datos | Existen y son la vía alternativa natural a los gráficos |
| Preferencia de movimiento reducido | **No verificada** |

## 4. El punto crítico: la información solo existe como gráfico

**[DECISIÓN JURÍDICA]** Una aplicación cuyo valor es la visualización de datos
tiene un desafío específico: un gráfico de líneas sin equivalente textual **no es
percibible** por una persona que usa lector de pantalla.

La mitigación estándar no es describir el gráfico, sino **ofrecer los mismos
datos en tabla accesible**, con encabezados correctos, y permitir descargarlos.
Portal Clientes ya tiene tablas en varias pantallas, así que el camino existe.

## 5. Compromisos propuestos

**[DECISIÓN JURÍDICA] No comprometer cumplimiento antes de auditar.**

1. Adoptar WCAG 2.2 AA como objetivo, con plan y fechas.
2. Realizar una auditoría inicial que establezca la línea base.
3. Ofrecer alternativa textual o tabular para todo contenido gráfico.
4. Habilitar un canal para reportar barreras de accesibilidad.
5. Incluir revisión de accesibilidad en el desarrollo de pantallas nuevas.

## 6. Advertencia

**[DECISIÓN JURÍDICA]** Publicar una declaración de conformidad sin haber
auditado es una afirmación falsa sobre el producto, con riesgo bajo el Estatuto
del Consumidor por información engañosa. **Auditar primero, declarar después.**
