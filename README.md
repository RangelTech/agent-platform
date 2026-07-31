# agent-platform

Multi-tenant AI agent platform — corporate-grade, template-driven.

Monorepo:

| Path | Service | Description |
|------|---------|-------------|
| `kernel/` | teste_ia-kernel | AI kernel: LangGraph runtime, LiteLLM providers, in-process MCP tools, SSE streaming |
| `backend/` | teste_ia-backend | App backend: auth, CRUD, chat gateway, integrations, serves the frontend build |
| `frontend/` | (static) | React + Vite + TS + Tailwind SPA, built and served by the backend |
| `infra/` | — | Deploy scripts / Cloud Run configs (GCP project eduk-prd-lake) |
| `docs/` | — | Architecture docs. Decisions and spec live in the legacy repo `agent-llm/docs/replatform/` |

## Dev quickstart

```bash
docker compose up --build
# backend: http://localhost:8090  (serves the SPA + /health)
# kernel:  http://localhost:8080/health
```

Docker here is a local developer convenience only. Production does **not** depend
on a local Docker daemon: the target runtime is Cloud Run for compute, VPS
PostgreSQL for the database, and S3-compatible object storage (for example
MinIO) for uploads/artifacts.

Local (no docker) — Python 3.12, Node 20+:

```bash
# backend
cd backend && py -3.12 -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/uvicorn app.main:app --port 8090
# kernel
cd kernel && py -3.12 -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/uvicorn app.main:app --port 8080
# frontend
cd frontend && npm install && npm run build   # backend serves frontend/dist
```

Postgres dev: `docker compose up postgres` (pgvector/pg16, db `agent_llm`, user/pass `agent/agent`). This is only for local integration tests and local development.

## Conventions

- Code, identifiers and comments: **English**. UI copy and agent prompts: **PT-BR**.
- All state in Postgres. No Redis. Files/artifacts in S3-compatible storage or GCS in production, and local dir in dev.
- Migrations: SQL files in `backend/migrations/`, applied automatically on backend boot.
- Tests: black-box at the HTTP seam (`pytest`), LLM faked via deterministic stub provider. `-m integration` requires a running Postgres.
