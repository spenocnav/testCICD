# Portal Clientes API

Backend FastAPI.

## Setup local (sin Docker)

```powershell
cd apps\api
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Visitar `http://localhost:8000/docs`.

## Comandos

| Comando | Acción |
| --- | --- |
| `pnpm dev` | uvicorn con reload |
| `pnpm lint` | ruff check |
| `pnpm typecheck` | mypy strict |
| `pnpm test` | pytest |
