#!/usr/bin/env bash
# Deploy do backend (FastAPI + SPA embutida) pro Cloud Run, projeto rangel-tech.
#
# infra-01 seção 5 (mega-spec-reestrutura): decisão é migrar o backend da VPS
# pro Cloud Run (min=0, tolera cold start). Este script sobe a imagem em
# PARALELO ao que já roda na VPS (`.github/workflows/deploy-vps.yml`) — não
# mexe em DNS/Traefik, o corte de tráfego real (ia.rangeltech.net apontar pra
# cá) é decisão separada, feita só depois de validar o serviço novo de pé.
#
# DATABASE_URL aponta pro Postgres real da VPS via porta 5433
# (postgres-direct, TLS) — mesmo achado do litellm-router/kernel-llm: a
# porta 5432 (PgBouncer) não faz TLS server-side.
set -euo pipefail

PROJECT=rangel-tech
REGION=us-central1
REPO=us-central1-docker.pkg.dev/$PROJECT/containers

cd "$(dirname "$0")/.."
SHORT_SHA=$(git rev-parse --short HEAD)
GCLOUD_BIN=${GCLOUD_BIN:-gcloud}

if [ -n "$(git status --porcelain --untracked-files=normal -- backend frontend infra)" ]; then
  echo "source tree is dirty; commit source changes before deploy" >&2
  git status --short --untracked-files=normal -- backend frontend infra >&2
  exit 1
fi

"$GCLOUD_BIN" builds submit --project=$PROJECT \
  --config=infra/cloudbuild-backend.yaml \
  --substitutions=SHORT_SHA=$SHORT_SHA .

"$GCLOUD_BIN" run deploy agent-platform-backend \
  --project=$PROJECT --region=$REGION \
  --image=$REPO/agent-platform-backend:$SHORT_SHA \
  --set-secrets=DATABASE_URL=agent-platform-database-url:latest,ENCRYPTION_KEY=agent-platform-encryption-key:latest,S3_SECRET_ACCESS_KEY=agent-platform-s3-secret-key:latest,KERNEL_INTERNAL_TOKEN=agent-platform-kernel-internal-token:latest,BRIDGE_ADMIN_TOKEN=agent-platform-bridge-admin-token:latest \
  --set-env-vars="KERNEL_URL=https://teste-ia-kernel-pujq3pjmca-uc.a.run.app,PUBLIC_BASE_URL=https://ia.rangeltech.net,BRIDGE_URL=https://bridge.rangeltech.net,STORAGE_BACKEND=s3,S3_ENDPOINT_URL=https://storage.rangeltech.net,S3_REGION=us-east-1,S3_BUCKET=teste-ia,S3_ACCESS_KEY_ID=minioadmin,S3_PREFIX=agent-llm,S3_PUBLIC_BASE_URL=https://storage.rangeltech.net/teste-ia" \
  --allow-unauthenticated \
  --memory=1Gi --cpu=1 --min-instances=0 --max-instances=5 \
  --timeout=300
