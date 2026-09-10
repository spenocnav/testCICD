# Política de Cookies y Tecnologías de Seguimiento

> Borrador para revisión jurídica. No es asesoría legal.
> Base: Ley 1581 de 2012; Directiva 2002/58/CE (ePrivacy) y RGPD si aplica a
> titulares en la Unión Europea. Fecha: 2026-08-27.

> **Estado del producto: en desarrollo.** Las cifras de este documento son una
> fotografía del entorno de desarrollo al 2026-08-27 y **no son el perfil final
> de tratamiento**: los volúmenes crecerán y algunas funcionalidades cambiarán.
> Lo que **sí es definitivo hoy** es que los datos son reales —22 de 22 flotas
> con nombre de empresa real, 330 de 330 vehículos con placa real, telemetría
> real— de modo que las obligaciones legales ya aplican. Ver el documento 00,
> sección "Qué significa que la aplicación esté en desarrollo".

## 1. Cookies que la aplicación instala

Verificado en el código al 2026-08-27. **La aplicación no usa cookies
publicitarias, de analítica de terceros ni de seguimiento entre sitios.**

| Cookie | Finalidad | Vigencia | Atributos |
| --- | --- | --- | --- |
| Token de acceso | Mantener la sesión autenticada | **15 minutos** | `HttpOnly`, `SameSite=Lax`, `Secure` solo en producción |
| Token de refresco | Renovar la sesión sin volver a autenticarse | **7 días** | `HttpOnly`, `SameSite=Lax`, `Secure` solo en producción |

Ambas son **estrictamente necesarias**: sin ellas el portal no puede funcionar.
Bajo el estándar europeo, esta categoría no requiere consentimiento previo, pero
**sí requiere información**.

`HttpOnly` significa que ningún script de la página puede leerlas, lo que las
protege frente a ataques de robo de sesión.

## 2. Almacenamiento local del navegador

**[HALLAZGO TÉCNICO]** La aplicación guarda además preferencias de interfaz en el
navegador, como la flota seleccionada y el estado del menú lateral. No son
cookies en sentido técnico —no viajan al servidor— pero para el titular son
indistinguibles y **deben declararse**. No contienen datos personales.

## 3. Terceros que reciben datos por la simple visita

**[HALLAZGO TÉCNICO] Este es el punto que exige decisión.** La pantalla de mapa
solicita las imágenes de fondo directamente a los servidores de OpenStreetMap.
Eso significa que **el navegador del usuario contacta a un tercero fuera del
control de Navitrans**, y en esa petición ese tercero recibe:

- la dirección IP del usuario, que revela su ubicación aproximada y su proveedor;
- la fecha y hora de la consulta;
- las coordenadas del área que se está mirando, es decir, **la zona aproximada
  donde ocurrió el evento de conducción consultado**.

Está registrado en la auditoría interna como parte de SEC-025.

**[DECISIÓN JURÍDICA]** Hay tres caminos y ninguno es solo técnico:

1. Declararlo en esta política y en el aviso de privacidad, y obtener el amparo
   correspondiente.
2. Servir las imágenes de mapa desde infraestructura propia, eliminando el flujo
   hacia el tercero. Es la opción que suprime el problema en vez de informarlo.
3. Retirar el mapa.

## 4. Ausencia de mecanismo de consentimiento

**[HALLAZGO TÉCNICO]** El portal **no presenta hoy ningún banner ni mecanismo de
gestión de cookies**, y no existe forma de registrar la decisión del usuario.

Si el área jurídica concluye que solo hay cookies estrictamente necesarias y que
ningún titular está sujeto al RGPD, puede bastar con informar. **Si el mapa se
mantiene**, la petición a OpenStreetMap probablemente exceda lo estrictamente
necesario y el análisis cambia.

## 5. Cómo puede el usuario controlarlas

**[DECISIÓN JURÍDICA]** Debe informarse que el usuario puede bloquear o eliminar
cookies desde su navegador, advirtiendo que **bloquear las de sesión impide
ingresar al portal**, porque son el único mecanismo de autenticación: la
aplicación no guarda credenciales en ningún otro lugar del navegador.
