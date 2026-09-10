# 0001 — Monorepo con Turborepo + pnpm

- **Estado:** Aceptado
- **Fecha:** 2026-05-27

## Contexto

El sistema tendrá un frontend Next.js, un backend FastAPI y, a futuro, un worker de telemetría y paquetes compartidos (tipos generados desde OpenAPI, posibles wrappers UI). Necesitamos ejecutar tareas comunes (lint, build, test) sobre todos los proyectos sin replicar configuración ni tooling.

## Decisión

Adoptar **Turborepo + pnpm workspaces** como estructura raíz. Las apps viven en `apps/*` y los paquetes compartidos en `packages/*`.

## Alternativas consideradas

- **Dos repos separados (frontend + backend):** menos fricción inicial pero acoplamiento manual de versionado y tipos. Descartado: queremos tipos TS auto-generados desde OpenAPI.
- **Nx:** más potente pero más complejo y opinionado. Sobredimensionado para 2-3 apps en esta etapa.
- **Workspaces de pnpm sin Turbo:** suficiente al inicio, pero ya queremos caching de tasks y pipelines declarativos.

## Consecuencias

- ✅ Comando único `pnpm dev` / `pnpm check` cubre todo el repo.
- ✅ Tipos generados desde OpenAPI se publican como paquete `@portal-clientes/shared-types`.
- ⚠️ Onboarding requiere instalar `pnpm` (no `npm`).
- ⚠️ Algunas herramientas Python (mypy/ruff) viven dentro de `apps/api` con su propio `pyproject.toml`. Turbo orquesta vía wrappers en `package.json`.
