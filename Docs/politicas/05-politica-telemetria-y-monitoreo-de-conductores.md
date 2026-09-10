# Política de Telemetría y Monitoreo de Conductores

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1581 de 2012; Constitución Política art. 15; Código Sustantivo del
> Trabajo; jurisprudencia de la Corte Constitucional sobre intimidad del
> trabajador (entre otras, T-768/08 y T-405/07). Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

**Este es el documento de mayor riesgo jurídico del conjunto.** No trata de los 6
usuarios del portal, sino de las personas que conducen los 330 vehículos y que
nunca han visto ninguna de estas políticas.

## 1. Qué se registra exactamente

Verificado en la base al 2026-08-27, en 30 días:

| Tipo de evento | Cantidad | Vehículos |
| --- | ---: | ---: |
| Excesos de RPM | 63.142 | 177 |
| Excesos de velocidad | 16.280 | 87 |
| Baches o resaltos fuertes | 1.105 | 107 |
| Giros bruscos | 744 | 88 |
| Aceleraciones bruscas | 163 | 38 |
| Frenadas bruscas | 101 | 46 |

**El 100 % de estos 81.535 eventos incluye latitud, longitud, fecha y hora
exactas, asociadas a una placa.**

Adicionalmente, por vehículo y día: kilómetros recorridos, horas de operación,
velocidad promedio, tiempo en ralentí, consumo de combustible y distribución del
tiempo por banda de revoluciones. Con 19 meses de historia para 289 vehículos.

## 2. Por qué esto es dato personal

**[DECISIÓN JURÍDICA] El argumento, para que el área jurídica lo evalúe.**

El portal no almacena el nombre del conductor. De ahí podría concluirse que no
hay dato personal. Ese razonamiento es frágil por tres motivos:

1. **La identificabilidad indirecta basta.** El art. 3 de la Ley 1581/2012 define
   dato personal como el que puede asociarse a una persona determinada **o
   determinable**. En una flota, el cruce de placa, fecha y hora contra la
   programación de turnos —que el cliente de flota tiene— identifica al conductor
   de forma trivial. Que el portal no haga ese cruce no lo vuelve imposible.
2. **La granularidad es individual, no agregada.** Un evento de frenada brusca en
   una coordenada, a una hora determinada, describe un acto concreto de una
   persona concreta. No es una estadística de flota.
3. **El uso previsible es individual.** La información se presenta por vehículo y
   permite comparar y ordenar. Su utilidad práctica para el cliente es, en buena
   medida, evaluar conductores.

Si el área jurídica acoge esta calificación, el régimen completo de la Ley
1581/2012 aplica a los 81.535 eventos mensuales, y no solo a los 6 usuarios.

## 3. El vacío central

**[HALLAZGO TÉCNICO] Ningún conductor recibe información ni otorga autorización a
través del portal.** La aplicación no tiene relación con él: no es usuario, no
tiene cuenta, no recibe aviso.

**[DECISIÓN JURÍDICA]** Esa relación es laboral y corresponde al cliente de flota
como empleador. Pero Navitrans no puede limitarse a suponer que el cliente cumplió.
Al menos hay que decidir:

1. **Qué se exige contractualmente al cliente de flota.** Como mínimo, que declare
   y acredite haber informado a sus conductores y obtenido su autorización, con
   indemnidad a favor de Navitrans si no lo hizo. Ver documento 11.
2. **Quién responde frente al conductor.** Depende de si Navitrans es responsable
   o encargado, y esa calificación no está resuelta.
3. **Si Navitrans debe entregar un texto modelo** al cliente para que informe a
   sus conductores. Es de bajo costo y reduce mucho la exposición.

## 4. Límites que la jurisprudencia impone

La Corte Constitucional ha admitido el monitoreo laboral cuando es **proporcional,
previamente informado y limitado al ámbito de la actividad laboral**. De ahí se
derivan restricciones concretas:

- **[DECISIÓN JURÍDICA] Fuera de jornada.** Un vehículo puede moverse fuera del
  horario laboral, o ser usado para desplazamientos personales autorizados. La
  telemetría no distingue. Registrar la ubicación de una persona fuera de su
  jornada excede el ámbito laboral. Hay que decidir si se restringe la
  visualización a ventanas horarias pactadas.
- **Proporcionalidad.** Registrar velocidad y frenadas para seguridad vial es
  claramente proporcional. Conservar el rastro geográfico completo durante años
  probablemente no. Ver documento 07.
- **No sanción automática.** Ningún dato del portal debería usarse como prueba
  única para una sanción disciplinaria sin contradicción del trabajador.

## 5. Una limitación técnica con consecuencia jurídica favorable

**[HALLAZGO TÉCNICO]** Los campos `velocidad_kmh`, `rpm`, `carga_pct`, `g_force`
y `g_axis` de los eventos de conducción están **100 % vacíos**: de 81.535 eventos
en 30 días, ninguno tiene valor.

Esto significa que hoy el sistema registra **que** hubo un exceso de velocidad,
pero no de cuánto; **que** hubo una frenada brusca, pero no su intensidad.

**[DECISIÓN JURÍDICA]** Tiene doble lectura y conviene decidirla ahora, antes de
que alguien llene esos campos por razones analíticas:

- Reduce la intrusividad actual y refuerza la proporcionalidad.
- Pero también impide graduar la gravedad, de modo que cualquier ranking de
  conducción construido hoy trata igual un exceso de 5 km/h y uno de 40. **Usar
  ese ranking para evaluar personas sería injusto y jurídicamente indefendible.**

Si se decide llenarlos, la intrusividad aumenta y esta política debe revisarse.

## 6. Compromisos propuestos

**[DECISIÓN JURÍDICA] Validar cada uno.**

1. La telemetría se usa para seguridad vial, eficiencia operativa y mantenimiento;
   no para vigilancia general del trabajador.
2. No se usará como prueba única en procesos disciplinarios.
3. El acceso está restringido a usuarios autorizados de la flota propietaria del
   vehículo — control que la aplicación sí implementa y verifica en cada petición.
4. No se comparte con terceros distintos de los descritos en el documento 11.
5. Los plazos de conservación se limitan a lo necesario (documento 07).
6. El conductor puede ejercer sus derechos; el canal está en el documento 10.


## 7. El producto está en desarrollo; la vigilancia ya ocurre

**[DECISIÓN JURÍDICA] Conviene decirlo sin rodeos, porque es la lectura menos
cómoda del conjunto.**

Portal Clientes no ha sido lanzado. Pero los 81.535 eventos de conducción
georreferenciados del último mes **son reales**: corresponden a 235 vehículos con
placa colombiana real, de 22 flotas con nombre de empresa real, y ya están
almacenados.

Es decir: **el registro de la conducta de conducción de personas reales ya está
ocurriendo, sin que esas personas hayan sido informadas, y sin que exista todavía
una política que lo ampare.** Que el producto no se haya lanzado no cambia ese
hecho; solo significa que aún no hay clientes consultándolo.

Tres consecuencias:

1. **La obligación de informar no espera al lanzamiento.** Si el tratamiento ya
   ocurre, el deber de información y autorización ya es exigible.
2. **El histórico acumulado hereda el problema.** Cuando se defina el mecanismo
   de autorización, habrá que decidir qué pasa con los datos recolectados antes
   de que existiera: si se amparan retroactivamente, se anonimizan o se suprimen.
   Cuanto más se demore, mayor será el volumen afectado.
3. **El riesgo crece solo.** Cada mes de desarrollo añade unos 80.000 eventos
   georreferenciados al histórico no amparado.

**[DECISIÓN JURÍDICA]** Una medida provisional de bajo costo mientras se resuelve:
**dejar de conservar la coordenada geográfica**, o degradar su precisión, hasta
que el marco de autorización esté definido. El evento seguiría siendo útil para
seguridad vial y eficiencia —que son las finalidades declaradas— y desaparecería
el elemento más intrusivo. Es reversible: si luego se decide conservarla, se
reactiva.
