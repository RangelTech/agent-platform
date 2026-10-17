#!/usr/bin/env bash
# Gate local (e do CI/release): ruff + pytest (unit + integration) em um
# container python:3.12 contra um Postgres pgvector descartavel, ambos numa
# rede docker propria. Reprodutivel (nao depende do python da maquina) e falha
# de verdade (sem `|| true`).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd -W 2>/dev/null || pwd)"
ID="agent-platform-verify-$$"
cleanup() { docker rm -f "${ID}-pg" >/dev/null 2>&1 || true; docker network rm "$ID" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker network create "$ID" >/dev/null
docker run -d --name "${ID}-pg" --network "$ID" --network-alias postgres \
  -e POSTGRES_DB=agent_llm -e POSTGRES_USER=agent -e POSTGRES_PASSWORD=agent \
  pgvector/pgvector:pg16 >/dev/null
for i in $(seq 1 60); do
  if docker exec "${ID}-pg" psql -U agent -d agent_llm -tAc "select 1" >/dev/null 2>&1; then break; fi
  sleep 1
  [ "$i" -eq 60 ] && { echo "postgres nao subiu"; docker logs "${ID}-pg" | tail -20; exit 1; }
done
# MSYS_NO_PATHCONV evita a conversao de caminhos do Git Bash no Windows.
MSYS_NO_PATHCONV=1 docker run --rm --network "$ID" \
  -v "${ROOT}:/src:ro" \
  -e DATABASE_URL="postgresql://agent:agent@postgres:5432/agent_llm" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  python:3.12-slim bash -euc '
    mkdir /work && cp -r /src/backend /src/custom_tool_runner /src/ruff.toml /work/ && cd /work/backend
    pip install -q --disable-pip-version-check -r requirements-dev.txt
    ruff check --no-cache .
    python -m pytest -q -p no:cacheprovider -m "integration or not integration"
  '
echo "verify OK"
