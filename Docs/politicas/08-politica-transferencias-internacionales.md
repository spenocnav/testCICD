# Política de Transferencias y Transmisiones Internacionales de Datos

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1581 de 2012 arts. 26 y 27; Decreto 1074 de 2015; Circulares Externas
> de la SIC sobre países con nivel adecuado de protección. Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

El art. 26 prohíbe la transferencia a países que no ofrezcan niveles adecuados de
protección, salvo excepciones tasadas: autorización expresa e inequívoca del
titular, intercambio de datos médicos por razones de salud, transacciones
bancarias, tratados internacionales, o aval previo de la SIC.

## 1. Flujos identificados

**[HALLAZGO TÉCNICO] Verificados en el código al 2026-08-27. La ubicación
geográfica de cada proveedor NO fue verificada y es lo primero que debe
establecerse.**

| Destino | Qué recibe o entrega | Ubicación |
| --- | --- | --- |
| **MyGeotab** | Origen de toda la telemetría: eventos de conducción con GPS, fallas, contadores | **[POR VERIFICAR]** |
| **CloudFleet** | Mantenimiento: órdenes de trabajo, programaciones. Recibe datos de novedades | **[POR VERIFICAR]** |
| **Navi Vehículos** | Catálogo maestro de vehículos, clientes y motores | **[POR VERIFICAR]** |
| **OpenStreetMap** | **La IP del usuario y el área geográfica que consulta**, en cada carga de mapa | Fundación con sede en Reino Unido |
| **Sentry** | Errores de aplicación. Configurado para no enviar datos personales | **[POR VERIFICAR]** |
| **Proveedor de IA** | Nada: el modelo corre en infraestructura propia | Local |

## 2. Lo que hay que resolver

**[DECISIÓN JURÍDICA] En este orden:**

1. **Establecer dónde está alojado cada proveedor.** Sin eso no hay análisis
   posible. Es un dato contractual, no técnico.
2. **Determinar si es transferencia o transmisión.** La distinción del art. 3 es
   determinante: la transmisión —a un encargado que trata por cuenta del
   responsable— tiene requisitos menos gravosos y admite el contrato de
   transmisión del art. 25. La transferencia a un tercero responsable es la que
   activa la prohibición del art. 26.
3. **Verificar si el país destino está en la lista de nivel adecuado de la SIC.**
4. **Si no lo está**, escoger vía: autorización expresa e inequívoca del titular,
   cláusulas contractuales con declaración de cumplimiento, o solicitud de aval
   previo ante la SIC.

## 3. El caso de OpenStreetMap es distinto y más difícil

**[HALLAZGO TÉCNICO]** No es una transferencia que Navitrans ejecute desde sus
servidores: **es el navegador del usuario el que contacta directamente al
tercero**, porque la página le indica de dónde traer las imágenes del mapa.

Eso significa que:

- Navitrans no envía el dato, pero **causa** su envío.
- El tercero recibe la IP del usuario y las coordenadas del área consultada, es
  decir, **la zona donde ocurrió un evento de conducción**.
- No hay contrato ni relación de encargo con ese tercero.
- No hay forma de auditar qué hace con esa información.

**[DECISIÓN JURÍDICA]** Es el flujo más difícil de amparar de todo el inventario,
precisamente porque no hay contrato posible. La alternativa técnica —servir las
imágenes desde infraestructura propia— elimina el problema en lugar de
documentarlo, y probablemente sea la vía más eficiente.

## 4. Cláusulas mínimas propuestas

**[DECISIÓN JURÍDICA]** Para cada proveedor con el que sí hay contrato:
finalidad determinada, prohibición de uso para fines propios, medidas de
seguridad, subencargados solo con autorización, deber de asistencia ante
solicitudes de titulares, notificación de incidentes en plazo definido,
devolución o supresión al terminar, y auditoría.

## 5. Registro

**[DECISIÓN JURÍDICA]** Debe mantenerse un inventario actualizado de flujos
internacionales. Este documento puede servir de base, pero necesita dueño y
periodicidad de revisión.
