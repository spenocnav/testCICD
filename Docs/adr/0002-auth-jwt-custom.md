# 0002 — Autenticación con JWT custom + cookies httpOnly

- **Estado:** Aceptado
- **Fecha:** 2026-05-27

## Contexto

Necesitamos autenticar usuarios desde el frontend Next.js contra el backend FastAPI, soportar sesiones largas (días) y cerrar sesión efectivamente al revocar tokens. El sistema es cerrado (no hay terceros consumiendo la API), pero a futuro habrá una PWA móvil para conductores.

## Decisión

Implementar **JWT custom emitidos por FastAPI**:

- **Access token** (15 min) firmado HS256, claim `type: "access"`, lleva permisos en claim `perms`.
- **Refresh token** (7 días) firmado HS256, claim `type: "refresh"`, registrado en tabla `refresh_tokens` con hash SHA-256.
- Ambos se entregan al cliente como **cookies httpOnly + SameSite=Lax + Secure (en producción)**.
- Rotación: cada uso de `/auth/refresh` revoca el refresh anterior y emite uno nuevo.

## Alternativas consideradas

- **Auth.js (NextAuth) con Credentials provider:** acopla auth a Next.js, dificulta consumir desde la PWA móvil con WebSockets.
- **Keycloak / proveedor OIDC externo:** overkill para un sistema cerrado de esta escala. Útil si vendrá SSO corporativo más adelante.
- **Tokens sólo en `Authorization: Bearer`:** obliga a guardar en `localStorage`/memoria del cliente y aumenta exposición a XSS. Cookies httpOnly mitigan ese vector.

## Consecuencias

- ✅ Sesiones revocables (el refresh está en DB y puede invalidarse al desactivar usuario).
- ✅ Frontend sin manipulación manual de tokens (los maneja el navegador como cookies).
- ✅ Mismo mecanismo sirve a la futura PWA si se acepta cookies o se cambia a Authorization header con el mismo token.
- ⚠️ CSRF: mitigado por `SameSite=Lax` para nuestras rutas (todas en mismo origen). Si en producción hay subdominios distintos, considerar CSRF tokens.
- ⚠️ Migración a RS256 con kid/jwks queda pendiente cuando crucemos servicios.
