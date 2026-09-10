# Contribuir a Portal Clientes

Guía para colaborar en este monorepo. Pensada para flujo local primero; cuando se integre con GitHub, se ajustan los detalles de PRs.

## Flujo de ramas

- `main`: rama estable. Solo recibe merges con PR aprobado o (en local) merges fast-forward desde ramas integradas.
- `feat/<slug-corto>`: nueva funcionalidad. Ej. `feat/users-bulk-import`.
- `fix/<slug-corto>`: bug. Ej. `fix/login-redirect-loop`.
- `chore/<slug-corto>`: tareas de mantenimiento (deps, scripts, docs).
- `refactor/<slug-corto>`: cambios sin alterar comportamiento externo.
- `docs/<slug-corto>`: cambios sólo documentación.

Rebasea sobre `main` antes de integrar. Evita merges sucios.

## Convención de commits

Sigue [Conventional Commits](https://www.conventionalcommits.org/) en formato corto:

```
<type>(<scope opcional>): <resumen imperativo>

<cuerpo opcional explicando *por qué*>

<footer opcional, ej. issue: #42>
```

Tipos: `feat`, `fix`, `chore`, `refactor`, `docs`, `test`, `perf`, `build`.

Ejemplos:

```
feat(users): permitir reactivar usuarios desactivados
fix(auth): regenerar refresh token al rotar para invalidar el anterior
chore(deps): subir sqlalchemy a 2.0.40
refactor(api): mover hashing de password a core/security
```

Reglas:

- Resumen en **imperativo**, en minúsculas, sin punto final, máximo ~72 caracteres.
- Si el cambio rompe API/contrato, agrega `!` antes de `:` y un footer `BREAKING CHANGE: ...`.

## Antes de subir cambios

Corre el equivalente local a CI:

```powershell
# Venv del backend activado y postgres+redis levantados
pnpm check
```

Esto ejecuta `lint + typecheck + test + build` en todos los workspaces.

Para validar un solo lado:

```powershell
pnpm check:web
pnpm check:api
```

Si tocaste schemas Pydantic o endpoints, regenera tipos TS:

```powershell
# Con la API corriendo
pnpm gen:types
```

## Estilo de código

- **TypeScript**: `strict` activado. No usar `any` salvo en boundaries con código sin tipos.
- **Python**: Ruff + mypy estricto. Anotar todo lo público.
- **Comentarios**: solo cuando aporten el *por qué* no evidente. No documentar el *qué*.
- **Tests**: incluir uno mínimo cuando se agrega lógica de negocio. Para fixes, agregar regression test.

## Migraciones de BD

Toda alteración del schema o seed va en una migración Alembic, no en código de aplicación.

```powershell
cd apps\api
python -m alembic revision --autogenerate -m "add column X to users"
# revisar el archivo generado en apps/api/alembic/versions/
python -m alembic upgrade head
```

No edites migraciones ya mergeadas a `main`. Crea una nueva.

## ADRs

Decisiones arquitectónicas relevantes (auth, ORM, infraestructura, etc.) se documentan en `Docs/adr/NNNN-titulo.md`. Si introduces un cambio de fondo, agrega un ADR antes de mergear.

## Secrets

- Nunca commitees `.env`. Solo `.env.example` con valores placeholder.
- Rota `JWT_SECRET` y `BOOTSTRAP_ADMIN_PASSWORD` en cualquier entorno compartido.

## Estructura del proyecto

Consulta `README.md` para el setup y `ARQUITECTURA_COMPLETA.md` para la arquitectura, los módulos y el estado del proyecto.
