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

Postgres dev: `docker compose up postgres` (pgvector/pg16, db `agent_llm`, user/pass `agent/agent`).

## Conventions

- Code, identifiers and comments: **English**. UI copy and agent prompts: **PT-BR**.
- All state in Postgres. No Redis. Files/artifacts in GCS (prod) or local dir (dev).
- Migrations: SQL files in `backend/migrations/`, applied automatically on backend boot.
- Tests: black-box at the HTTP seam (`pytest`), LLM faked via deterministic stub provider. `-m integration` requires a running Postgres.
