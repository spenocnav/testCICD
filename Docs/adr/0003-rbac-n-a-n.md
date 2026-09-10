# 0003 — RBAC con modelo N:M usuarios ↔ roles ↔ permisos

- **Estado:** Aceptado
- **Fecha:** 2026-05-27

## Contexto

El sistema necesita control de acceso para distinguir al menos administradores, gestores y usuarios de solo lectura. A futuro habrá conductores con permisos diferenciados sobre flotas. Los permisos deben poder agregarse y reorganizarse sin redeploys.

## Decisión

Modelo de tres tablas y dos uniones:

```
users  ↔  user_roles  ↔  roles  ↔  role_permissions  ↔  permissions
```

- Permisos identificados por `code` (`users.read`, `users.delete`, `roles.manage`, …).
- Roles son agrupaciones de permisos con un `code` propio (`admin`, `gestor`, `viewer`).
- Un usuario tiene N roles; un rol tiene N permisos.
- Chequeo: `user_has_permission(user, code)` recorre `user.roles → role.permissions`. Cargado con `selectinload(User.roles).selectinload(Role.permissions)` para evitar N+1 / lazy I/O.
- Dependencia FastAPI: `require_permission("users.delete")` genera 403 si falta.

## Alternativas consideradas

- **Roles enum hardcoded:** simple pero requiere redeploy para reorganizar permisos.
- **Roles + scopes por recurso (ABAC ligero):** más potente (por ej., "viewer SOLO de flota X"), pero excesivo para esta fase. Se puede acomodar a futuro con una columna `scope` o tabla `user_role_scopes` sin romper el modelo.

## Consecuencias

- ✅ Admin puede crear roles y reasignar permisos sin tocar código.
- ✅ Frontend muestra/oculta UI con `<Can permission="…">` reusando el mismo set de códigos.
- ⚠️ Cualquier nuevo permiso requiere una migración de datos (seed) — buena cosa porque hace explícito el cambio.
- ⚠️ Sin scopes por recurso aún. Cuando se necesite, extender con tabla intermedia y mantener compatibilidad.
