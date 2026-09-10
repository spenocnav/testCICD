# 0004 — Driver Postgres: psycopg3 en lugar de asyncpg

- **Estado:** Aceptado
- **Fecha:** 2026-05-27

## Contexto

La arquitectura inicial planteaba `asyncpg` por rendimiento. Durante Fase 1, ejecutando en Windows + Docker Desktop, `asyncpg` 0.30 y 0.31 fallan el handshake con un error genérico `ConnectionDoesNotExistError: connection was closed in the middle of operation`, incluso con `WindowsSelectorEventLoopPolicy`. El problema no aparece desde Linux ni desde dentro del contenedor. `psycopg3` async funciona sin issues en el mismo setup.

## Decisión

Usar `psycopg[binary]` como driver async para SQLAlchemy. URLs cambian a `postgresql+psycopg://...`. `asyncpg` se elimina de dependencias.

## Alternativas consideradas

- **Mantener asyncpg y exigir WSL:** introduce fricción a desarrolladores Windows.
- **Mantener asyncpg y forzar SelectorEventLoopPolicy:** no resuelve el problema observado.
- **Cambiar a sync con psycopg sync:** rompe la arquitectura ASGI / async-first.

## Consecuencias

- ✅ Funciona en Windows, macOS y Linux sin tuning especial.
- ✅ `psycopg3` async es estable y oficial de PostgreSQL.
- ⚠️ Marginalmente más lento que `asyncpg` bajo carga muy alta; aceptable para esta etapa.
- ⚠️ Requiere ajustar `alembic/env.py` para forzar `WindowsSelectorEventLoopPolicy` cuando se ejecuta en Windows (psycopg async no acepta `ProactorEventLoop`).
