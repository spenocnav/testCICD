# Architecture Decision Records (ADR)

Decisiones arquitectónicas relevantes del proyecto. Cada ADR describe una decisión tomada, su contexto, alternativas consideradas y consecuencias.

## Convenciones

- Numeración secuencial: `0001-titulo-en-kebab.md`, `0002-...`.
- Estado: `Aceptado | Reemplazado por NNNN | Obsoleto`.
- Plantilla:

  ```markdown
  # NNNN — Título

  - **Estado:** Aceptado
  - **Fecha:** YYYY-MM-DD
  - **Decisores:** quién

  ## Contexto

  Qué problema o necesidad motivó la decisión.

  ## Decisión

  Qué se eligió, en una o dos oraciones.

  ## Alternativas consideradas

  - Opción A — por qué se descartó
  - Opción B — por qué se descartó

  ## Consecuencias

  Implicaciones positivas, negativas y trade-offs aceptados.
  ```

## Índice

- [0001 — Monorepo con Turborepo + pnpm](0001-monorepo-turborepo.md)
- [0002 — Autenticación con JWT custom + cookies httpOnly](0002-auth-jwt-custom.md)
- [0003 — RBAC N:M usuarios ↔ roles ↔ permisos](0003-rbac-n-a-n.md)
- [0004 — Driver Postgres psycopg3 en lugar de asyncpg](0004-driver-psycopg.md)
- [0005 — Hash bcrypt directo sin passlib](0005-bcrypt-sin-passlib.md)
