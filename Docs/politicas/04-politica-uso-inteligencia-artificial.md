# Política de Uso de Inteligencia Artificial

> Borrador para revisión jurídica. No es asesoría legal.
> Base: CONPES 4144 de 2025 (Política Nacional de IA); Reglamento (UE) 2024/1689
> (Ley de IA europea) como referencia de estándar; Ley 1581 de 2012 art. 16.
> Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

Colombia no tiene todavía una ley vinculante de inteligencia artificial. Existe
política pública (CONPES 4144) y proyectos en trámite. Esta política se propone
sobre el estándar internacional más exigente, para no tener que rehacerla.

## 1. Dónde se usa IA en Portal Clientes

**Un solo uso, y es de cara al cliente.** El módulo Navifault genera
automáticamente dos textos por cada código de falla detectado:

1. una comunicación corta que **se envía al correo del cliente**;
2. una explicación más extensa que **el cliente lee en el portal**.

Ambos explican, en lenguaje no técnico, qué significa una falla mecánica
detectada en su vehículo y si debe llevarlo a taller.

### 1.1 Cómo funciona, verificado en el código

- El modelo se ejecuta en **infraestructura propia**, no en un servicio de un
  tercero. Ningún dato sale hacia un proveedor externo de IA.
- Recibe **exclusivamente** la documentación técnica oficial del fabricante
  correspondiente al código de falla. **No recibe datos personales**: ni nombre,
  ni correo, ni ubicación, ni identificación del conductor.
- El prompt le prohíbe expresamente inventar causas, síntomas, procedimientos o
  recomendaciones que la documentación no sustente.
- Un validador automático rechaza la salida si no cumple el formato exigido, y se
  reintenta una vez indicando el motivo del rechazo.
- Solo se genera cuando el código de falla resuelve **de forma inequívoca** a una
  página del manual. Si hay ambigüedad, **no se genera nada**: no se elige una
  variante por conjetura.

Volumen al 2026-08-27: se identificaron **310 combinaciones distintas** de motor y
código con correspondencia exacta en el manual, sobre 1.596 combinaciones
observadas en seis meses.

## 2. El hallazgo que exige decisión inmediata

**[HALLAZGO TÉCNICO] Hoy el cliente no sabe que el texto lo escribió una IA.**

La instrucción que se le da al modelo dice, textualmente, que entregue *"el
resultado como comunicación final para el cliente, sin explicar el proceso
utilizado para producirlo"*. No hay marca, aviso ni nota al pie en el correo ni
en el portal.

**[DECISIÓN JURÍDICA]** Esa instrucción se escribió por una razón editorial
razonable —que el texto no hable de sí mismo— y no con intención de ocultar. Pero
el efecto es que **una comunicación automatizada sobre el estado mecánico de un
vehículo llega al cliente sin identificarse como tal**.

El art. 50 del Reglamento (UE) 2024/1689 exige informar cuando una persona
interactúa con un sistema de IA o recibe contenido generado por ella. Colombia no
lo exige hoy. La pregunta para el área jurídica no es solo si es obligatorio,
sino **qué pasa si un cliente lleva su vehículo a taller con base en una
explicación generada automáticamente y el resultado no es el esperado.**

Recomendación de ingeniería: revelar el origen. Se puede hacer sin tocar el
prompt, añadiendo una nota fija fuera del texto generado.

## 3. Clasificación de riesgo

**[DECISIÓN JURÍDICA]** Bajo el esquema europeo, este uso **no parece** de alto
riesgo: no decide sobre personas, no accede a empleo, crédito ni servicios
esenciales, y no hace inferencias sobre individuos. Se acercaría a la categoría de
riesgo limitado, cuya obligación principal es precisamente la transparencia del
punto 2.

Ese análisis debe confirmarse y **rehacerse si el uso cambia**. Dos extensiones
plausibles que sí moverían la clasificación:

- puntuar o clasificar conductores con apoyo de IA — pasaría a evaluar personas;
- recomendar automáticamente intervenciones o inmovilizar un vehículo — pasaría a
  tener consecuencias materiales directas.

## 4. Decisiones automatizadas

El art. 16 de la Ley 1581/2012 y el art. 22 del RGPD regulan las decisiones
basadas únicamente en tratamiento automatizado con efectos jurídicos o
significativos.

**Hoy no hay decisión automatizada:** el sistema redacta una explicación y
recomienda revisar en taller cuando la documentación indica una lámpara activa. La
decisión de llevar el vehículo la toma el cliente.

**[DECISIÓN JURÍDICA]** Aun así, conviene reconocer expresamente el derecho a
solicitar revisión humana de cualquier comunicación generada. Es barato de
conceder y cierra la discusión por anticipado.

## 5. Supervisión humana y trazabilidad

**[HALLAZGO TÉCNICO] No existe hoy revisión humana previa al envío.** El texto se
genera y queda disponible sin aprobación.

Sí existe trazabilidad técnica: por cada generación se conservan el modelo
empleado, la versión del prompt, una huella del contexto entregado, los tokens
consumidos y el motivo de terminación. Eso permite reconstruir **exactamente** qué
produjo un texto determinado — capacidad valiosa si alguna vez hay que responder
por una comunicación concreta.

**[DECISIÓN JURÍDICA]** Definir si se exige revisión humana antes del envío, o si
basta con la trazabilidad y el derecho a revisión posterior. La decisión depende
de cuánto pese el riesgo de una explicación mecánica equivocada.

## 6. Evaluación y control de calidad

Se realizó una evaluación comparativa el 2026-08-27 sobre 12 códigos de falla,
con juicio humano a ciegas, para decidir la configuración del modelo. Ese tipo de
evaluación debería institucionalizarse.

**[DECISIÓN JURÍDICA]** Fijar la periodicidad de reevaluación, el criterio de
aceptación y quién responde por él. Un modelo que hoy redacta bien puede degradarse
si cambia el modelo, el prompt o el corpus documental.

## 7. Principios que se comprometen

**[DECISIÓN JURÍDICA] Validar la redacción; la sustancia ya se cumple.**

1. **Transparencia.** Se informará que el contenido es generado por IA. *Pendiente
   de implementar — ver punto 2.*
2. **Fundamentación.** El sistema solo afirma lo que la documentación oficial del
   fabricante sustenta.
3. **Abstención ante la duda.** Si el código no resuelve inequívocamente, no se
   genera comunicación alguna.
4. **Minimización.** El modelo no recibe datos personales.
5. **Soberanía del dato.** La inferencia ocurre en infraestructura propia.
6. **Revisión humana.** El cliente puede solicitar que una persona revise
   cualquier comunicación.
7. **Trazabilidad.** Toda generación es reconstruible.
